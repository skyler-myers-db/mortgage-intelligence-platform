import { EmptyState } from '../components/ui/EmptyState';
import { GenieAskAbout } from '../components/mortgage/GenieAskAbout';
import { genieLeadPrompt, genieSegmentPrompt, genieStatePrompt } from '../lib/genieContext';
import { LEAD_TABLE_PLACE_PARAMS, LEAD_TABLE_VIEW_PARAM, parseSegmentCodes } from './lead-queue.filters';

/**
 * The Lead Queue's measured zero (audit 2026-09-21 `states-v2`): its cause
 * and a next step, never a zero-count table.
 *
 *   - coverage      the county rollup ANSWERED with no ZIPs;
 *   - intersection  2+ segment chips in all-mode;
 *   - filtered      any other filtered zero; an unfiltered zero says what
 *                   the default view lists and offers no Clear.
 *
 * "Ask Genie" prefills (never submits) a REVIEWED prompt, and only when the
 * active filters are one state and/or one segment: counties, ZIPs, cities,
 * borrower ids, counts, lender names and free text never reach a prompt.
 */

const UNFILTERED_SECONDARY = 'The default view lists marketing-eligible borrowers only.';
const DISPLAY_PARAMS: ReadonlySet<string> = new Set([LEAD_TABLE_VIEW_PARAM, ...LEAD_TABLE_PLACE_PARAMS]);
const PROMPT_FILTER_PARAMS: ReadonlySet<string> = new Set(['state', 'states', 'segment', 'segment_codes', 'segment_mode']);

function csv(searchParams: URLSearchParams, ...keys: string[]): string[] {
  return [...new Set(keys.flatMap((key) => (searchParams.get(key) ?? '').split(',')).map((value) => value.trim()).filter(Boolean))];
}

/**
 * The reviewed Genie prompt for the queue's filters, or null. Non-null only
 * when every active filter is one state (`state`, or a one-entry `states`)
 * and/or one segment: its output is always a genieLeadPrompt /
 * genieSegmentPrompt / genieStatePrompt rendering, which
 * tests/unit/test_genie_context_templates.py runs through the prompt guards.
 * Null for 2+ segments (genieLeadPrompt names only the first, so the prefill
 * would misstate an intersection) and for a segment without templates.
 */
export function queueFilterGeniePrompt(searchParams: URLSearchParams): string | null {
  for (const key of searchParams.keys()) {
    if (!DISPLAY_PARAMS.has(key) && !PROMPT_FILTER_PARAMS.has(key)) return null;
  }
  const states = csv(searchParams, 'state', 'states').map((code) => code.toUpperCase());
  const rawSegments = csv(searchParams, 'segment', 'segment_codes');
  const segments = parseSegmentCodes(rawSegments.join(','));
  if (states.length > 1 || rawSegments.length > 1 || segments.length !== rawSegments.length) return null;
  const [state] = states;
  const [segment] = segments;
  if (segment && genieSegmentPrompt(segment) === null) return null;
  if (state && segment) return genieLeadPrompt({ segmentCodes: [segment], stateCode: state });
  if (segment) return genieSegmentPrompt(segment);
  if (state) return genieStatePrompt(state);
  return null;
}

interface LeadQueueEmptyStateProps {
  searchParams: URLSearchParams;
  filtersActive: boolean;
  /** The county rollup answered with zero ZIPs. */
  countyNoCoverage: boolean;
  /** 2+ segment chips in all-mode. */
  intersection: boolean;
  onClearFilters: () => void;
}

export function LeadQueueEmptyState({
  searchParams,
  filtersActive,
  countyNoCoverage,
  intersection,
  onClearFilters,
}: LeadQueueEmptyStateProps) {
  const cause = countyNoCoverage ? 'coverage' : intersection ? 'intersection' : 'filtered';
  const prompt = cause === 'filtered' ? queueFilterGeniePrompt(searchParams) : null;
  return (
    <EmptyState
      cause={cause}
      secondary={filtersActive ? undefined : UNFILTERED_SECONDARY}
      actions={[
        filtersActive ? (
          <button type="button" className="btn btn--sm" aria-label="Clear lead queue filters" onClick={onClearFilters}>
            Clear filters
          </button>
        ) : null,
        prompt ? <GenieAskAbout prompt={prompt} subject="filter selection" /> : null,
      ]}
    />
  );
}
