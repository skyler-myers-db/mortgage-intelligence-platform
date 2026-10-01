/**
 * Deep links into the audit explorer (audit flow-04 phase 1, 2026-09-21).
 *
 * Every audit_event_id the product shows is a link that opens the explorer
 * on that one row. The explorer lives on /audit-ledger (D-audit-reads-c3,
 * 2026-10-01), gated to administrators and the read-only Auditor role, so
 * callers that know the actor can read neither render the id as text instead
 * (the route would answer with its access-denied surface). Only the path
 * moved from /admin-config: the parameters and the `#audit` hash are the
 * same, so an old admin link still lands (legacyLedgerRedirect below).
 */

export const AUDIT_EXPLORER_PATH = '/audit-ledger';
export const AUDIT_EXPLORER_HASH = '#audit';
export const AUDIT_EVENT_ID_PARAM = 'audit_event_id';
/** The explorer's correlation filter (`AdminAuditExplorer.params.ts` owns the rest). */
export const AUDIT_CORRELATION_ID_PARAM = 'audit_correlation_id';
/** Every explorer URL parameter carries this prefix (AdminAuditExplorer.params.ts). */
export const AUDIT_EXPLORER_PARAM_PREFIX = 'audit_';

/** `/audit-ledger?audit_event_id=<id>#audit` for one ledger row. */
export function auditEventHref(eventId: string): string {
  const params = new URLSearchParams();
  params.set(AUDIT_EVENT_ID_PARAM, eventId);
  return `${AUDIT_EXPLORER_PATH}?${params.toString()}${AUDIT_EXPLORER_HASH}`;
}

/** The explorer filtered to one correlation id (every row of one request). */
export function auditCorrelationHref(correlationId: string): string {
  const params = new URLSearchParams();
  params.set(AUDIT_CORRELATION_ID_PARAM, correlationId);
  return `${AUDIT_EXPLORER_PATH}?${params.toString()}${AUDIT_EXPLORER_HASH}`;
}

/**
 * Where an old `/admin-config?audit_…` explorer link now points: the same
 * search on the ledger page, opened on the explorer. Null when the search
 * carries no explorer parameter (a plain /admin-config or /admin-config#audit
 * stays on Admin, which shows the ledger link card).
 */
export function legacyLedgerRedirect(search: string): string | null {
  const params = new URLSearchParams(search);
  const carriesExplorerParam = [...params.keys()].some((key) => key.startsWith(AUDIT_EXPLORER_PARAM_PREFIX));
  if (!carriesExplorerParam) return null;
  // The search verbatim (no re-encoding), so the explorer parses exactly
  // the filters the old link carried.
  const query = search.startsWith('?') ? search : `?${search}`;
  return `${AUDIT_EXPLORER_PATH}${query}${AUDIT_EXPLORER_HASH}`;
}
