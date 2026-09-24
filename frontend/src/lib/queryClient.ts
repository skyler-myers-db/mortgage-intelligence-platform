import { QueryClient } from '@tanstack/react-query';
import { isWarmingUpError } from './api';
import { clientFailureReason } from './apiTransport';
import { installOnlineManager } from './connectivity';
import { planForReason } from './retryPlan';

export const DEFAULT_QUERY_STALE_MS = 30_000;
export const DEFAULT_QUERY_GC_MS = 5 * 60_000;

export function createMipQueryClient(): QueryClient {
  // Offline pauses queries and their retries (networkMode 'online', the
  // default) instead of burning the retry budget against a dead network;
  // see lib/connectivity.
  installOnlineManager();
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: DEFAULT_QUERY_STALE_MS,
        gcTime: DEFAULT_QUERY_GC_MS,
        // Many read endpoints deliberately write VIEW_* audit rows.
        // Automatic focus refetch would inflate governance evidence and
        // re-read actor-scoped data after an identity swap, so queries must
        // opt into focus refetch explicitly when it is safe.
        refetchOnWindowFocus: false,
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
}
