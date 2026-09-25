import { useEffect, useState, useSyncExternalStore, type ReactNode } from 'react';
import { useQueryClient, type MutationCache } from '@tanstack/react-query';
import { QUEUE_VERSION_POLL_MS, QUEUE_VERSION_SERVER_TTL_MS, useQueueVersion } from '../lib/queueVersion';
import { FetchedAt, RefreshButton } from '../components/ui/FetchedAt';

/**
 * The Lead Queue's freshness in its table header (audit 2026-09-21
 * `states-09`): "Fetched 3m ago · Refresh", or "Queue updated · Refresh" once
 * the audit-free queue version moves away from the version the rows were
 * read against.
 *
 *   - Baseline: the version known when the leads query last settled (its
 *     dataUpdatedAt changed), else the first version polled after.
 *   - It NEVER re-reads /api/leads by itself (that read writes a VIEW_LEADS
 *     audit row): no invalidation, no refetch on a version change, focus
 *     refetch stays off. Refresh is the reader's explicit click.
 *   - Own writes: an outreach or sales mutation from THIS tab never reads as
 *     someone else's change. The server answers the version from a cache up
 *     to QUEUE_VERSION_SERVER_TTL_MS old, so a read right after the write can
 *     still be the PRE-write version; only a read ASKED FOR at least that
 *     long after the latest own write may absorb it. Until one lands the pill
 *     stays off (a Refresh in between keeps the write pending), and one
 *     re-read is scheduled for then (a bulk run's rows push it out, so the
 *     run re-reads once). A queue write made shortly before the queue
 *     mounted (an approve on Borrower 360, then back) counts as made at
 *     mount. Documented race: another actor's change landing in that window
 *     is absorbed; FetchedAt still shows the age.
 *   - The version is global (single-tenant deploy), so the pill says the
 *     queue was updated, never that this reader's rows changed.
 */

/** When an own write's re-read is due: past the server cache, plus a second for the server's own read. */
export const OWN_WRITE_REREAD_MS = QUEUE_VERSION_SERVER_TTL_MS + 1_000;
/**
 * A write this old can still be missing from a version the queue meets at
 * mount: a cached answer is re-used for up to a poll, and it was up to a
 * server cache old when it was read.
 */
const PRE_MOUNT_WRITE_WINDOW_MS = QUEUE_VERSION_POLL_MS + QUEUE_VERSION_SERVER_TTL_MS;

interface OwnWrites {
  count: number;
  /** Date.now() of the latest own write (a pre-mount write counts as made at mount). */
  at: number;
}

interface OwnWriteStore {
  subscribe: (listener: () => void) => () => void;
  getSnapshot: () => OwnWrites;
}

function isQueueWrite(key: readonly unknown[] | undefined): boolean {
  return key?.[0] === 'mip' && (key[1] === 'outreach' || key[1] === 'sales');
}

/** Successful outreach / sales mutations in this tab, counted from the MutationCache. */
function createOwnWriteStore(cache: MutationCache): OwnWriteStore {
  const mountedAt = Date.now();
  const recentWrite = cache.getAll().some((mutation) => (
    mutation.state.status === 'success'
    && isQueueWrite(mutation.options.mutationKey)
    && mutation.state.submittedAt >= mountedAt - PRE_MOUNT_WRITE_WINDOW_MS
  ));
  let snapshot: OwnWrites = recentWrite ? { count: 1, at: mountedAt } : { count: 0, at: 0 };
  const listeners = new Set<() => void>();
  let unsubscribeCache: (() => void) | null = null;
  return {
    getSnapshot: () => snapshot,
    subscribe: (listener) => {
      listeners.add(listener);
      if (!unsubscribeCache) {
        unsubscribeCache = cache.subscribe((event) => {
          if (event.type !== 'updated' || event.action.type !== 'success') return;
          if (!isQueueWrite(event.mutation.options.mutationKey)) return;
          snapshot = { count: snapshot.count + 1, at: Date.now() };
          listeners.forEach((notify) => notify());
        });
      }
      return () => {
        listeners.delete(listener);
        if (listeners.size === 0 && unsubscribeCache) {
          unsubscribeCache();
          unsubscribeCache = null;
        }
      };
    },
  };
}

interface Baseline {
  /** The leads query's dataUpdatedAt this baseline belongs to. */
  leadsAt: number | null;
  /** The own-write count it has absorbed. */
  writes: number;
  version: string | null;
}

interface LeadQueueFreshnessInput {
  /** A settled payload is on screen (not a placeholder), and the query is resolved. */
  enabled: boolean;
  dataUpdatedAt: number | null;
  isFetching: boolean;
  /** The leads query's manualRetry: one GET /leads, one VIEW_LEADS row. */
  onRefresh: () => void;
}

const SUBJECT = 'ranked borrowers';

export function useLeadQueueFreshness({ enabled, dataUpdatedAt, isFetching, onRefresh }: LeadQueueFreshnessInput): ReactNode {
  const queryClient = useQueryClient();
  const [store] = useState(() => createOwnWriteStore(queryClient.getMutationCache()));
  const ownWrites = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  const versionQuery = useQueueVersion({ enabled });
  const reading = versionQuery.data ?? null;
  const latest = reading?.version ?? null;
  // Only a reading asked for a full server-cache TTL after the latest own
  // write is certain to include it; an earlier one may be the cached
  // PRE-write version, which the next poll would flag as a change.
  const coversOwnWrites = reading !== null && reading.requestedAt >= ownWrites.at + QUEUE_VERSION_SERVER_TTL_MS;
  const absorbed = coversOwnWrites ? ownWrites.count : null;

  // Derived during render (no setState in an effect): re-baseline when the
  // rows settle anew (own writes a covering reading has not absorbed stay
  // pending), when a covering version answers after an own write, or on the
  // first version polled after the rows.
  const [baseline, setBaseline] = useState<Baseline>({ leadsAt: dataUpdatedAt, writes: 0, version: latest });
  if (baseline.leadsAt !== dataUpdatedAt) {
    setBaseline({ leadsAt: dataUpdatedAt, writes: absorbed ?? baseline.writes, version: latest });
  } else if (baseline.writes !== ownWrites.count) {
    if (absorbed !== null) setBaseline({ leadsAt: dataUpdatedAt, writes: absorbed, version: latest });
  } else if (baseline.version === null && latest !== null) {
    setBaseline({ ...baseline, version: latest });
  }

  // Own writes pending: one re-read once the server cache has turned over
  // (each new write pushes it out, so a bulk run re-reads once).
  const pending = baseline.writes !== ownWrites.count;
  const { refetch } = versionQuery;
  useEffect(() => {
    if (!enabled || !pending) return undefined;
    const timer = setTimeout(() => void refetch(), Math.max(0, ownWrites.at + OWN_WRITE_REREAD_MS - Date.now()));
    return () => clearTimeout(timer);
  }, [enabled, pending, ownWrites.at, refetch]);

  const changed = enabled
    && !pending
    && baseline.version !== null
    && latest !== null
    && latest !== baseline.version;
  return (
    <>
      {/* Announced once when the queue changes; empty (and out of the flex
          row, .sr-only is absolute) otherwise. Never an alert. */}
      <span className="sr-only" role="status">
        {changed ? 'The queue was updated. Refresh to read the current queue.' : ''}
      </span>
      {changed ? (
        <span className="fetched-at" data-testid="queue-updated">
          <span className="fetched-at__changed">Queue updated ·</span>
          <RefreshButton subject={SUBJECT} isFetching={isFetching} onRefresh={onRefresh} />
        </span>
      ) : (
        <FetchedAt at={dataUpdatedAt} subject={SUBJECT} isFetching={isFetching} onRefresh={onRefresh} />
      )}
    </>
  );
}
