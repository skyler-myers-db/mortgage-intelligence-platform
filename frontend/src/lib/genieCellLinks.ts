/**
 * Genie table-cell linkage.
 *
 * A Genie answer table is currently a dead end: the reader sees a masked
 * borrower id or a state code and has to retype it into the Lead Queue. These
 * helpers decide when a cell value is a safe navigation target and build the
 * SAME route the rest of the app already uses, so a Genie answer drills into
 * exactly the surface the drill-down cards and geography map drill into.
 *
 * Deliberately conservative: only values that match the canonical masked
 * borrower id shape (`B-[0-9A-Z]{13}`, per CLAUDE.md "Naming rules") and
 * 2-letter state codes become links. Anything else stays plain text — a
 * fabricated link into /borrower-360/<garbage> is worse than no link.
 */

import { buildLeadQueuePath } from '../components/mortgage/USChoroplethMap.utils';

/** Canonical masked borrower id (CLAUDE.md naming rules). */
export const MASKED_BORROWER_ID_RE = /^B-[0-9A-Z]{13}$/;

/** 2-letter USPS state code, as emitted by the gold tables. */
const STATE_CODE_RE = /^[A-Z]{2}$/;

/** Columns whose values address a borrower record. */
const BORROWER_ID_COLUMNS = new Set(['borrower_id']);

/** Columns whose values address a state. */
const STATE_COLUMNS = new Set(['state', 'state_code']);

/** Columns whose values address a segment. */
const SEGMENT_COLUMNS = new Set(['segment_code', 'segment']);

/**
 * The segments the Lead Queue can filter on, mirroring `SegmentCode` in
 * `backend/schemas/lead.py`. A value outside this set gets no link rather than
 * a queue that silently ignores the parameter.
 */
const LINKABLE_SEGMENT_CODES = new Set([
  'itm', 'listed', 'permit', 'investor', 'equity', 'retention',
  'second_lien_itm', 'heloc_draw_to_payback', 'home_equity_history',
  'refi_propensity', 'itm_on_related_property', 'payoff_loss_leads',
  'permit_activity',
]);

export function isMaskedBorrowerId(value: unknown): value is string {
  return typeof value === 'string' && MASKED_BORROWER_ID_RE.test(value.trim());
}

export function isStateCode(value: unknown): value is string {
  return typeof value === 'string' && STATE_CODE_RE.test(value.trim().toUpperCase());
}

/** Borrower 360 deep link — mirrors every other borrower link in the app. */
export function borrower360Path(borrowerId: string): string {
  return `/borrower-360/${encodeURIComponent(borrowerId.trim())}`;
}

/**
 * The answer's own non-geographic filters, as read from the governed action's
 * `criteria.result_filters` (the backend derives them from the answer's SQL in
 * `_route_from_answer_rows`).
 *
 * Geography is deliberately excluded: a row link is scoped to THAT row's
 * state, not to every state the answer covered.
 */
export interface GenieAnswerCohort {
  segmentFilter?: string[];
  segmentFilterMode?: 'any' | 'all';
  portfolioCriteria?: Record<string, string | number>;
}

const REPLAYABLE_NUMERIC_FILTERS = [
  'min_opportunity_score',
  'min_equity_pct',
  'min_rate_spread_bps',
] as const;

/**
 * Lift the answer's replayable filters off its `open_cohort` action.
 *
 * Returns `{}` when the answer has no governed cohort — in which case a row
 * link carries geography alone, exactly as before.
 */
export function answerCohortFromActions(
  actions: readonly { action_type?: string; criteria?: Record<string, unknown> | null }[] | null
    | undefined,
): GenieAnswerCohort {
  const action = (actions ?? []).find((a) => a.action_type === 'open_cohort');
  const raw = action?.criteria?.result_filters;
  const filters = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : null;
  if (!filters) return {};
  const cohort: GenieAnswerCohort = {};
  const segments = Array.isArray(filters.segment_codes)
    ? filters.segment_codes.filter((s): s is string => typeof s === 'string')
    : [];
  if (segments.length > 0) {
    cohort.segmentFilter = segments;
    cohort.segmentFilterMode = filters.segment_mode === 'all' ? 'all' : 'any';
  }
  const criteria: Record<string, string | number> = {};
  const portfolio = filters.portfolio_criteria;
  if (portfolio && typeof portfolio === 'object') {
    Object.entries(portfolio as Record<string, unknown>).forEach(([key, value]) => {
      if (typeof value === 'string' && value !== '') criteria[key] = value;
    });
  }
  REPLAYABLE_NUMERIC_FILTERS.forEach((key) => {
    const value = filters[key];
    if (typeof value === 'number' && Number.isFinite(value)) criteria[key] = value;
  });
  if (Object.keys(criteria).length > 0) cohort.portfolioCriteria = criteria;
  return cohort;
}

/**
 * State-filtered Lead Queue link. Reuses `buildLeadQueuePath`, the exact
 * helper the geography drill-down map calls on a state click
 * (`USChoroplethMap.tsx` → `navigate(leadQueuePath({ state }))`), so a Genie
 * answer and the map land on an identically-filtered queue.
 *
 * `cohort` carries the ANSWER'S own filters so the link opens the population
 * the row actually reports. Without it the link dropped every non-geographic
 * filter: measured live on paychex 2026-08-11, a row reading "WA — 5,847
 * in-the-money borrowers" linked to `/lead-queue?state=WA`, which opens all
 * 31,114 contactable WA borrowers. That is the same silent divergence the
 * cohort handoff was fixed for, on a surface nobody had checked.
 */
export function stateLeadQueuePath(state: string, cohort: GenieAnswerCohort = {}): string {
  return buildLeadQueuePath({
    geo: { state: state.trim().toUpperCase() },
    segmentFilter: cohort.segmentFilter,
    segmentFilterMode: cohort.segmentFilterMode ?? 'any',
    portfolioCriteria: cohort.portfolioCriteria,
  });
}

export function isLinkableSegmentCode(value: unknown): value is string {
  return typeof value === 'string' && LINKABLE_SEGMENT_CODES.has(value.trim().toLowerCase());
}

/**
 * Segment-filtered Lead Queue link for a row of a segment COMPARISON.
 *
 * A "which segment converts best" answer groups by segment, so it has no
 * single cohort and correctly gets no governed cohort action — but that left
 * the reader with a ranking they could not act on. Measured live on paychex
 * 2026-08-11: 14 of 23 swept questions answered with trusted SQL and offered
 * nothing to click, breaking the product flow at the "recommend" step.
 *
 * The row itself is the missing cohort. Each row of a segment breakdown IS one
 * segment, so linking the row to that segment's queue is a statement the
 * answer already made, not an inference about it. The answer's other filters
 * ride along for the same reason they do on a state row.
 */
export function segmentLeadQueuePath(
  segment: string,
  cohort: GenieAnswerCohort = {},
  row?: Record<string, unknown> | null,
): string {
  // A `state, segment_code, borrowers` row reports the INTERSECTION, so the
  // segment cell has to carry the row's own state or it opens that segment
  // nationwide — 5,847 WA in-the-money borrowers becoming every in-the-money
  // borrower in the country (adversarial review 2026-08-11). The state comes
  // from the row, never from the answer's overall state list, for the same
  // reason the state cell's link does not carry the answer's other states.
  const rowState = row
    ? Object.entries(row).find(([key]) => STATE_COLUMNS.has(key.trim().toLowerCase()))?.[1]
    : undefined;
  return buildLeadQueuePath({
    geo: isStateCode(rowState) ? { state: (rowState as string).trim().toUpperCase() } : {},
    segmentFilter: [segment.trim().toLowerCase()],
    segmentFilterMode: 'any',
    portfolioCriteria: cohort.portfolioCriteria,
  });
}

/**
 * Resolve a table cell to an in-app route, or null when it should render as
 * plain text. `city` intentionally returns null: no route in `app.tsx`
 * supports a city filter (`lead-queue.filters.ts` has no city param), and a
 * link that silently drops the filter misleads the reader — which is exactly
 * why `cohort` exists for the state case.
 */
export function genieCellHref(
  column: string,
  value: unknown,
  cohort: GenieAnswerCohort = {},
  row?: Record<string, unknown> | null,
): string | null {
  const key = column.trim().toLowerCase();
  if (BORROWER_ID_COLUMNS.has(key) && isMaskedBorrowerId(value)) {
    return borrower360Path(value as string);
  }
  if (STATE_COLUMNS.has(key) && isStateCode(value)) {
    return stateLeadQueuePath(value as string, cohort);
  }
  if (SEGMENT_COLUMNS.has(key) && isLinkableSegmentCode(value)) {
    return segmentLeadQueuePath(value as string, cohort, row);
  }
  return null;
}
