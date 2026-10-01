import { ROUTES } from './routeMeta';

/**
 * Where an old `/admin-config?audit_…` explorer link now points
 * (D-audit-reads-c3): the same search (a router `location.search`, so it
 * starts with `?`), verbatim, on the ledger page opened on the explorer. Null
 * when the search carries no explorer parameter (a plain /admin-config or
 * /admin-config#audit stays on Admin, which shows the ledger link card).
 *
 * Its own module so app.tsx's admin gate (initial bundle) does not pull the
 * lazily used lib/auditLinks into the initial chunk; auditLinks re-exports it
 * and pins its literals to these (auditLinks.test.ts).
 */
export const LEGACY_LEDGER_PARAM_PREFIX = 'audit_';
export const LEDGER_EXPLORER_HASH = '#audit';

/** A query key that starts with the explorer prefix. */
const EXPLORER_PARAM_RE = new RegExp(`[?&]${LEGACY_LEDGER_PARAM_PREFIX}`);

export function legacyLedgerRedirect(search: string): string | null {
  return EXPLORER_PARAM_RE.test(search) ? `${ROUTES.auditLedger.pattern}${search}${LEDGER_EXPLORER_HASH}` : null;
}
