/**
 * Provider-optional reads of the shared session query (D-audit-reads-d,
 * audit flow-04).
 *
 * The refusal card and the audit-link sites render inside the app's
 * QueryClientProvider, but many of their tests (GenieAnswer, the collapsed
 * turn, ask-genie, the refusal card) render a refused answer with no
 * provider at all, where `useQuery` would throw. These hooks read the cached
 * `/api/session` entry when a client exists and never fetch: no provider, or
 * a session that has not loaded yet, reads as `undefined`, so every gate
 * below fails closed (hash-only report, mono text instead of a ledger link).
 */
import { QueryClientContext, type QueryClient } from '@tanstack/react-query';
import { useContext, useMemo, useSyncExternalStore } from 'react';
import type { SessionResponse } from '../types';
import { canReadAuditLedger, sessionQueryOptions } from './sessionQuery';

const NO_CLIENT_UNSUBSCRIBE = () => {};

function subscribeTo(client: QueryClient | undefined) {
  return (onChange: () => void) => (client ? client.getQueryCache().subscribe(onChange) : NO_CLIENT_UNSUBSCRIBE);
}

/** The cached session, or `undefined` without a provider or before it loads. */
export function useOptionalSession(): SessionResponse | undefined {
  const client = useContext(QueryClientContext);
  const subscribe = useMemo(() => subscribeTo(client), [client]);
  const snapshot = () => client?.getQueryData<SessionResponse>(sessionQueryOptions().queryKey);
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

/** The effective consented refusal-text capture switch; false when unknown. */
export function useRefusalTextCapture(): boolean {
  return useOptionalSession()?.refusal_text_capture_enabled === true;
}

/** Whether audit event ids may link to /audit-ledger: administrators and auditors. */
export function useAuditLinkAccess(): boolean {
  return canReadAuditLedger(useOptionalSession());
}
