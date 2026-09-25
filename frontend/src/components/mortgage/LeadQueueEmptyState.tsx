import { EmptyState } from '../ui/EmptyState';
import { GenieAskAbout } from './GenieAskAbout';
import { genieLeadPrompt, genieSegmentPrompt, genieStatePrompt } from '../../lib/genieContext';

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
 *
 * "Ask Genie" prefills (never submits) a REVIEWED prompt, and only when the
 * active filters are one state and/or one segment: counties, ZIPs, cities,
 * borrower ids, counts, lender names and free text never reach a prompt.
 */

const UNFILTERED_SECONDARY = 'The default view lists marketing-eligible borrowers only.';
const PROMPT_FILTER_PARAMS: ReadonlySet<string> = new Set(['state', 'states', 'segment', 'segment_codes', 'segment_mode']);

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

interface LeadQueueEmptyStateProps {
  /** The queue's filter params (no column preset, no table place). */
  filterParams: URLSearchParams;
  /** The county rollup answered with zero ZIPs. */
  countyNoCoverage: boolean;
  /** 2+ segment chips in all-mode. */
  intersection: boolean;
  onClearFilters: () => void;
}

export function LeadQueueEmptyState({ filterParams, countyNoCoverage, intersection, onClearFilters }: LeadQueueEmptyStateProps) {
  const filtersActive = [...filterParams.keys()].length > 0;
  const cause = countyNoCoverage ? 'coverage' : intersection ? 'intersection' : 'filtered';
  const prompt = cause === 'filtered' ? queueFilterGeniePrompt(filterParams) : null;
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
