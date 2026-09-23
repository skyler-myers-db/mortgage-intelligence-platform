/**
 * Deep links into the audit explorer (audit flow-04 phase 1, 2026-09-21).
 *
 * Every audit_event_id the product shows is a link that opens the explorer
 * on that one row. The explorer lives on the admin-gated /admin-config
 * route, so callers that know the actor is not an admin render the id as
 * text instead (the route would answer with its access-denied surface).
 */

export const AUDIT_EXPLORER_PATH = '/admin-config';
export const AUDIT_EXPLORER_HASH = '#audit';
export const AUDIT_EVENT_ID_PARAM = 'audit_event_id';
/** The explorer's correlation filter (`AdminAuditExplorer.params.ts` owns the rest). */
export const AUDIT_CORRELATION_ID_PARAM = 'audit_correlation_id';

/** `/admin-config?audit_event_id=<id>#audit` for one ledger row. */
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
