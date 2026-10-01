import { queryOptions, useQuery } from '@tanstack/react-query';
import type { SessionResponse } from '../types';
import { api } from './api';

/**
 * The one `/api/session` read the shell observes (AppContext, RouteNav,
 * IdentityMenu and the admin route gate share its key). Zero-dependency on
 * the server, never retried: a failed check fails closed (no Admin tab, no
 * approve) instead of waiting out a retry plan. main.tsx seeds it from the
 * boot prime with these same options (lib/bootPrime).
 */
export function sessionQueryOptions() {
  return queryOptions<SessionResponse>({
    queryKey: ['session', 'access'],
    queryFn: ({ signal }) => api.session(signal),
    retry: false,
  });
}

/**
 * Whether a session may read the audit ledger: the same fail-closed decision
 * the ledger reads enforce server-side (`require_audit_reader`, D-audit-reads-c3):
 * an administrator or a configured read-only auditor. A missing session
 * (pending, errored, an older backend without the key) reads as no access.
 */
export function canReadAuditLedger(session: SessionResponse | undefined): boolean {
  return session?.can_access_admin === true || session?.can_read_audit === true;
}

/** `canReadAuditLedger` of the shared session read; false while pending or errored. */
export function useAuditLedgerAccess(): boolean {
  const session = useQuery(sessionQueryOptions());
  return canReadAuditLedger(session.data);
}

/**
 * The demo-only presenter flag (D-shell-deviations-e1): gates demo
 * affordances, never an authorization input. False while the session is
 * pending or errored, so a customer never sees a demo surface by accident.
 */
export function usePresenterMode(): boolean {
  const session = useQuery(sessionQueryOptions());
  return session.data?.presenter_mode === true;
}
