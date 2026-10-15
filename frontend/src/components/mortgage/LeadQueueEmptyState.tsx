import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import { EmptyState } from '../ui/EmptyState';
import { Button } from '../Primitives';
import { GenieAskAbout } from './GenieAskAbout';
import { genieContextFromQueueFilters, genieLeadPrompt, genieSegmentPrompt, genieStatePrompt } from '../../lib/genieContext';
import { requestGenieContextScope, type GenieContext } from '../../lib/apiClients/genieContextScope';
import { openGenie } from '../../lib/genieOpen';

/**
 * The Lead Queue's measured zero (audit 2026-09-21 `states-v2`): its cause
 * and a next step, never a zero-count table. Loaded on demand by the route,
 * only when a measured zero is on screen (a lazy module under src/routes/
 * would count as a route for the budget gate, so it lives here).
 *
 *   - coverage      the county rollup ANSWERED with no ZIPs;
 *   - intersection  2+ segment chips in all-mode;
 *   - filtered      any other filtered zero; an unfiltered zero says what
 *                   the default view lists and offers no Clear.
 *   - a request-scoped queue (`?approval_request_batch=`, W5d lq deferral 9)
 *     whose ONLY filter is the request and that measures zero says the
 *     request is done: every borrower in it was decided, withdrawn or
 *     expired. Its one action clears the request. Beside any other filter
 *     the zero may be that filter's (GET /api/leads intersects the request's
 *     open borrowers with every other filter), so the filtered state answers
 *     there instead. A request-scoped queue never offers a Genie Ask (a
 *     request is a list of named borrowers, not a question).
 *
 * "Ask Genie" prefills (never submits) a REVIEWED prompt. Phase 1 renders it
 * client-side when the filters are one state and/or one segment. Phase 2
 * (genie-04 states-v2) covers the rest of the closed grammar in
 * `genieContextFromQueueFilters` (a segment intersection, a county, a ZIP, a
 * governed city, a Competitor ref, the assignee preset): on CLICK only, the
 * codes go to `POST /api/genie/context-scope`, which renders and
 * battery-tests the question server-side; the composer then holds that
 * prompt verbatim. Borrower ids, criteria, emails and free text never reach
 * either path.
 */

const UNFILTERED_SECONDARY = 'The default view lists marketing-eligible borrowers only.';
/** lead-queue.filters APPROVAL_REQUEST_BATCH_PARAM (not imported: this chunk stays off the route's filters module). */
const REQUEST_SCOPE_PARAM = 'approval_request_batch';
export const REQUEST_SCOPE_EMPTY = {
  title: 'No open borrowers left in this approval request.',
  secondary: 'Each one was decided, withdrawn or has expired.',
  clear: 'Clear the request',
} as const;
const PROMPT_FILTER_PARAMS: ReadonlySet<string> = new Set(['state', 'states', 'segment', 'segment_codes', 'segment_mode']);
/** Fixed copy: nothing about a withheld or failed render is improvised client-side. */
export const GENIE_SCOPE_UNAVAILABLE_COPY = "Genie can't take this filter set as a question.";
const ASSIGNEE_SUFFIX = ' without the assignee filter';

function csv(filters: URLSearchParams, ...keys: string[]): string[] {
  return [...new Set(keys.flatMap((key) => (filters.get(key) ?? '').split(',')).map((value) => value.trim()).filter(Boolean))];
}

/**
 * The reviewed Genie prompt for the queue's FILTER params (the column preset
 * and the table place already removed: lead-queue.activeFilters
 * leadQueueFilterParams), or null. Non-null only when every filter is one
 * state (`state`, or a one-entry `states`) and/or one segment: its output is
 * always a genieLeadPrompt / genieSegmentPrompt / genieStatePrompt rendering,
 * which tests/unit/test_genie_context_templates.py runs through the prompt
 * guards. Null for 2+ segments (genieLeadPrompt names only the first, so the
 * prefill would misstate an intersection) and for an unknown segment or one
 * without templates.
 */
export function queueFilterGeniePrompt(filters: URLSearchParams): string | null {
  for (const key of filters.keys()) {
    if (!PROMPT_FILTER_PARAMS.has(key)) return null;
  }
  const states = csv(filters, 'state', 'states').map((code) => code.toUpperCase());
  const segments = csv(filters, 'segment', 'segment_codes').map((code) => code.toLowerCase());
  if (states.length > 1 || segments.length > 1) return null;
  const [state] = states;
  const [segment] = segments;
  if (segment && genieSegmentPrompt(segment) === null) return null;
  if (state && segment) return genieLeadPrompt({ segmentCodes: [segment], stateCode: state });
  if (segment) return genieSegmentPrompt(segment);
  if (state) return genieStatePrompt(state);
  return null;
}

type ScopePhase = 'idle' | 'pending' | 'unavailable';

/**
 * The phase-2 Ask: one POST per click (never on render, hover or focus),
 * pending while it runs, aborted on unmount. A prompt whose `omitted` list is
 * what this context expects prefills the composer; a withheld render, an
 * unexpected `omitted` or a failed request replaces the button with a fixed
 * muted line inside the empty state's role=status region. A FOCUSED Ask
 * hands focus to `focusFallbackRef` (Clear filters, always beside it) in the
 * commit that removes it, never to <body> (WCAG 2.4.3); an Ask that did not
 * hold focus moves nothing.
 */
function GenieScopedAsk({ context, focusFallbackRef }: { context: GenieContext; focusFallbackRef: RefObject<HTMLButtonElement | null> }) {
  const [phase, setPhase] = useState<ScopePhase>('idle');
  const controllerRef = useRef<AbortController | null>(null);
  const askRef = useRef<HTMLButtonElement | null>(null);
  // Written by the settle handler while the Ask is still mounted; read after
  // the commit that replaced it.
  const refocusRef = useRef(false);
  useEffect(() => () => controllerRef.current?.abort(), []);
  useLayoutEffect(() => {
    if (phase !== 'unavailable' || !refocusRef.current) return;
    refocusRef.current = false;
    focusFallbackRef.current?.focus({ preventScroll: true });
  }, [phase, focusFallbackRef]);
  if (phase === 'unavailable') return <span className="muted fs-12">{GENIE_SCOPE_UNAVAILABLE_COPY}</span>;
  const withoutAssignee = context.assignee === 'me';
  const suffix = withoutAssignee ? ASSIGNEE_SUFFIX : '';
  const unavailable = () => {
    const button = askRef.current;
    refocusRef.current = button !== null && document.activeElement === button;
    setPhase('unavailable');
  };
  const ask = () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setPhase('pending');
    requestGenieContextScope(context, controller.signal).then(
      (response) => {
        if (controller.signal.aborted) return;
        const omittedAsExpected = response.omitted.length === (withoutAssignee ? 1 : 0);
        if (response.prompt && response.withheld === null && omittedAsExpected) {
          setPhase('idle');
          openGenie({ prompt: response.prompt });
          return;
        }
        unavailable();
      },
      () => {
        if (!controller.signal.aborted) unavailable();
      },
    );
  };
  return (
    <Button
      ref={askRef}
      variant="ghost"
      size="sm"
      className="genie-ask-about"
      icon="sparkle"
      loading={phase === 'pending'}
      aria-label={`Ask Genie about this filter selection${suffix}`}
      onClick={ask}
    >
      {`Ask Genie${suffix}`}
    </Button>
  );
}

interface LeadQueueEmptyStateProps {
  /** The queue's filter params (no column preset, no table place). */
  filterParams: URLSearchParams;
  /** The county rollup answered with zero ZIPs. */
  countyNoCoverage: boolean;
  /** 2+ segment chips in all-mode. */
  intersection: boolean;
  onClearFilters: () => void;
}

/** True only when the approval request is the queue's sole filter: then a zero can only mean the request has no open borrowers left. */
function requestScopeOnly(filterParams: URLSearchParams): boolean {
  const keys = [...filterParams.keys()];
  return keys.length > 0 && keys.every((key) => key === REQUEST_SCOPE_PARAM);
}

export function LeadQueueEmptyState(props: LeadQueueEmptyStateProps) {
  if (!requestScopeOnly(props.filterParams)) return <FilteredQueueEmpty {...props} />;
  return (
    <EmptyState
      cause="filtered"
      title={REQUEST_SCOPE_EMPTY.title}
      secondary={REQUEST_SCOPE_EMPTY.secondary}
      actions={[
        <button type="button" className="btn btn--sm" onClick={props.onClearFilters}>
          {REQUEST_SCOPE_EMPTY.clear}
        </button>,
      ]}
    />
  );
}

function FilteredQueueEmpty({ filterParams, countyNoCoverage, intersection, onClearFilters }: LeadQueueEmptyStateProps) {
  const filtersActive = [...filterParams.keys()].length > 0;
  const cause = countyNoCoverage ? 'coverage' : intersection ? 'intersection' : 'filtered';
  // A request-scoped queue with another filter beside it never offers an Ask,
  // whatever the two prompt builders would make of the rest of the filters.
  const askable = !filterParams.has(REQUEST_SCOPE_PARAM);
  const prompt = askable && cause === 'filtered' ? queueFilterGeniePrompt(filterParams) : null;
  const context = askable && cause !== 'coverage' && prompt === null ? genieContextFromQueueFilters(filterParams) : null;
  const clearRef = useRef<HTMLButtonElement | null>(null);
  return (
    <EmptyState
      cause={cause}
      secondary={filtersActive ? undefined : UNFILTERED_SECONDARY}
      actions={[
        filtersActive ? (
          <button ref={clearRef} type="button" className="btn btn--sm" aria-label="Clear lead queue filters" onClick={onClearFilters}>
            Clear filters
          </button>
        ) : null,
        prompt ? (
          <GenieAskAbout prompt={prompt} subject="filter selection" />
        ) : context ? (
          // Keyed by the selection: a new filter set starts a fresh Ask.
          <GenieScopedAsk key={filterParams.toString()} context={context} focusFallbackRef={clearRef} />
        ) : null,
      ]}
    />
  );
}
