import { useQuery } from '@tanstack/react-query';
import { getJson } from './apiTransport';

/**
 * The Lead Queue's audit-free change signal (audit 2026-09-21 `states-09`):
 * GET /api/workspace/queue-version answers an opaque version of the human
 * decision ledgers (approvals, assignments, dispositions, LO outcomes) and
 * writes no audit row, unlike a re-read of /api/leads (VIEW_LEADS).
 *
 * Lazy by construction: only the Lead Queue route imports this module, and
 * the fetcher calls the transport directly, so the initial `api` object and
 * queryKeys.ts are unchanged. The key is local and deliberately NOT under
 * ['mip', 'workspace'], so no workspace invalidation ever refetches it.
 */

export const QUEUE_VERSION_KEY = ['mip', 'queue-version'] as const;
/** One poll a minute while the queue is open (60 s, stated in ms). */
export const QUEUE_VERSION_POLL_MS = 60_000;

/** The route's body (backend/schemas/queue_version.py): 32 lowercase hex. */
export interface QueueVersionBody {
  version: string;
}

export function fetchQueueVersion(signal?: AbortSignal): Promise<string> {
  return getJson<QueueVersionBody>('/api/workspace/queue-version', signal).then((body) => body.version);
}

/**
 * Polls while `enabled`. TanStack pauses the interval while the document is
 * hidden (refetchIntervalInBackground false); no focus refetch; no retry.
 * Errors are silent by design: the signal is a courtesy, never a callout.
 *
 * The query deliberately does NOT hand TanStack's AbortSignal to the
 * transport: an own-write re-read (refetch, cancelRefetch) still discards a
 * poll in flight, whose pre-write answer must never become the baseline, but
 * that small audit-free GET finishes instead of failing as an aborted request
 * in the middle of a bulk approval run.
 */
export function useQueueVersion({ enabled }: { enabled: boolean }) {
  return useQuery({
    queryKey: QUEUE_VERSION_KEY,
    queryFn: () => fetchQueueVersion(),
    enabled,
    refetchInterval: QUEUE_VERSION_POLL_MS,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
    retry: false,
    staleTime: 55_000,
  });
}
