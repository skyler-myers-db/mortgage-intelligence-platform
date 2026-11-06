import { QueryClient, type Query } from '@tanstack/react-query';
import { refetchableAfterNetworkLoss } from '../components/connectionState';
import { actorScopeStatus, subscribeActorScope } from './actorScope';
import { isWarmingUpError } from './api';
import { clientFailureReason } from './apiTransport';
import { installOnlineManager } from './connectivity';
import { planForReason } from './retryPlan';
import { queryKeys } from './queryKeys';

export const DEFAULT_QUERY_STALE_MS = 30_000;
export const DEFAULT_QUERY_GC_MS = 5 * 60_000;

/**
 * The app's reconnect refetch. Coming back online never re-reads data on
 * screen, nor an audited read the server answered: many reads write a VIEW_*
 * audit row, so a reconnect would add one with no act behind it, the reason
 * focus refetch is off too. That includes a 200 whose body was lost
 * mid-transfer, which fails offline or unreachable but carries its status,
 * unless its path is a reviewed audit-free read (lib/auditFreeReads), which
 * writes no row: that one is completed. So is a read whose last attempt got
 * no status at all (fetch() rejected, offline or unreachable), as TanStack's
 * default would. A query that waited offline ('paused') resumes on its own
 * whatever this says, and a query whose every page writes a row
 * (`meta.auditedPages`) is never refetched here. The rule is connectionState's
 * refetchableAfterNetworkLoss, the same one refetchUnreachableQueries uses.
 */
export function refetchOnReconnectAfterNetworkLoss(query: Query): boolean {
  return refetchableAfterNetworkLoss(query);
}

export function createMipQueryClient(): QueryClient {
  // Offline pauses queries and their retries (networkMode 'online', the
  // default) instead of burning the retry budget against a dead network;
  // see lib/connectivity.
  installOnlineManager();
  const client = new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: DEFAULT_QUERY_STALE_MS,
        gcTime: DEFAULT_QUERY_GC_MS,
        // Many read endpoints deliberately write VIEW_* audit rows.
        // Automatic focus refetch would inflate governance evidence and
        // re-read actor-scoped data after an identity swap, so queries must
        // opt into focus refetch explicitly when it is safe.
        refetchOnWindowFocus: false,
        // The same for reconnect: only a read the network lost before it
        // wrote a row (refetchOnReconnectAfterNetworkLoss).
        refetchOnReconnect: refetchOnReconnectAfterNetworkLoss,
        retry: (failureCount, error) => {
          // An ended session, no network, an unreachable app or an
          // unreadable body is never retried here (audit states-02): the
          // session dialog, the offline banner and "Connection lost" own
          // those, and a retry would only repeat the same failure.
          if (clientFailureReason(error)) return false;
          if (!isWarmingUpError(error)) return false;
          const plan = planForReason(error.reason, error.dependency, {
            intervalMs: 5_000,
            maxAttempts: 6,
          });
          if (plan.stop) return false;
          return failureCount < Math.max(0, plan.maxAttempts - 1);
        },
        retryDelay: (_failureCount, error) => {
          if (!isWarmingUpError(error)) return 0;
          const plan = planForReason(error.reason, error.dependency, {
            intervalMs: 5_000,
            maxAttempts: 6,
          });
          return plan.intervalMs;
        },
      },
      mutations: {
        retry: false,
      },
    },
  });
  // The Lead Queue's paged view (routes/lead-queue.pages.ts; the scope is
  // lib/leadsQuery LEADS_PAGED_SCOPE): every served page writes a VIEW_LEADS
  // row, so nothing but the reader's action re-reads it (D-audit-reads-a).
  client.setQueryDefaults(queryKeys.leads(['lead-queue-paged']), {
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    staleTime: Infinity,
  });
  return client;
}

type QueryPersistModule = Pick<typeof import('./queryPersist'), 'startQueryPersistence'>;

/**
 * The persisted aggregate cache (audit delivery-05, lib/queryPersist): the
 * only initial-JS part. The first time the actor gate (lib/actorScope) is
 * open, or at once if it already is, the lazy module is imported and started,
 * once per document; before that nothing is restored, so a snapshot never
 * renders for an actor the gate has not confirmed. `load` is a test seam.
 */
export function installQueryPersistence(
  queryClient: QueryClient,
  load: () => Promise<QueryPersistModule> = () => import('./queryPersist'),
): void {
  let started = false;
  let unsubscribe: (() => void) | null = null;
  const start = () => {
    if (started || actorScopeStatus() !== 'open') return;
    started = true;
    unsubscribe?.();
    load()
      .then((module) => module.startQueryPersistence(queryClient))
      .catch(() => undefined);
  };
  unsubscribe = subscribeActorScope(start);
  start();
}
