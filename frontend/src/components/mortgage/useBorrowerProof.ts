/**
 * useBorrowerProof — the ONE proof query in the app (critic fix 16).
 *
 * Every GET /api/borrowers/{id}/proof writes a VIEW_BORROWER_PROOF audit row,
 * so the read may fire only from an explicit act: opening the proof drawer,
 * clicking a Score anatomy disclosure, or "Try again". Never on mount, on a
 * remount, on a reconnect, on focus, on staleness, and never after
 * invalidateOperationalQueries marks ['mip', 'borrower', ...] invalidated on
 * every approve.
 *
 * - staleTime 'static' (query-core 5.100.10): once data exists the query is
 *   never stale, invalidated or not, so an enable flip, a key change back to a
 *   cached borrower, a remount and a reconnect are all pure cache reads.
 *   Infinity is NOT enough: Query.isStaleByTime reports an invalidated query
 *   stale before it compares time, and shouldFetchOptionally then refetches
 *   on an enabled false -> true flip or a key change regardless of
 *   refetchOnMount (queryObserver.js).
 * - refetchOnMount / refetchOnReconnect / refetchOnWindowFocus false: the
 *   global client leaves refetchOnReconnect at its default (true).
 * - Retries stay at the client default: a warming 503 writes no audit row.
 *
 * A cache read is this hook with `enabled` false: it observes the cached
 * proof (keeping it from garbage collection) and never fetches. Only the cold
 * state or an explicit `refetch()` reaches the network.
 */
import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';

export function useBorrowerProof(borrowerId: string, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.borrowerProof(borrowerId),
    queryFn: ({ signal }) => api.borrowerProof(borrowerId, signal),
    enabled: enabled && borrowerId !== '',
    staleTime: 'static',
    refetchOnMount: false,
    refetchOnReconnect: false,
    refetchOnWindowFocus: false,
  });
}
