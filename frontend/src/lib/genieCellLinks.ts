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
 * State-filtered Lead Queue link. Reuses `buildLeadQueuePath`, the exact
 * helper the geography drill-down map calls on a state click
 * (`USChoroplethMap.tsx` → `navigate(leadQueuePath({ state }))`), so a Genie
 * answer and the map land on an identically-filtered queue.
 */
export function stateLeadQueuePath(state: string): string {
  return buildLeadQueuePath({ geo: { state: state.trim().toUpperCase() } });
}

/**
 * Resolve a table cell to an in-app route, or null when it should render as
 * plain text. `city` intentionally returns null: no route in `app.tsx`
 * supports a city filter (`lead-queue.filters.ts` has no city param), and a
 * link that silently drops the filter misleads the reader.
 */
export function genieCellHref(column: string, value: unknown): string | null {
  const key = column.trim().toLowerCase();
  if (BORROWER_ID_COLUMNS.has(key) && isMaskedBorrowerId(value)) {
    return borrower360Path(value as string);
  }
  if (STATE_COLUMNS.has(key) && isStateCode(value)) {
    return stateLeadQueuePath(value as string);
  }
  return null;
}
