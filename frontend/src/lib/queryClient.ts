import { QueryClient } from '@tanstack/react-query';
import { isWarmingUpError } from './api';
import { planForReason } from './retryPlan';

export const DEFAULT_QUERY_STALE_MS = 30_000;
export const DEFAULT_QUERY_GC_MS = 5 * 60_000;

export function createMipQueryClient(): QueryClient {
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
