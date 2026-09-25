import { useEffect, useState, useSyncExternalStore, type ReactNode } from 'react';
import { useQueryClient, type MutationCache } from '@tanstack/react-query';
import { useQueueVersion } from '../lib/queueVersion';
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
 *   - Own writes: when an outreach or sales mutation from THIS tab succeeds,
 *     the version is read once more and that answer becomes the baseline, so
 *     a reader's own approval never reads as someone else's change.
 *     Documented race: another actor's change landing between the own write
 *     and that read is absorbed; FetchedAt still shows the age.
 *   - The version is global (single-tenant deploy), so the pill says the
 *     queue was updated, never that this reader's rows changed.
 */

interface OwnWrites {
  count: number;
  /** Date.now() of the latest own write. */
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
  let snapshot: OwnWrites = { count: 0, at: 0 };
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
  const latest = versionQuery.data ?? null;
  const versionAt = versionQuery.dataUpdatedAt;

  // Derived during render (no setState in an effect): re-baseline when the
  // rows settle anew, when a version answers after an own write, or on the
  // first version polled after the rows.
  const [baseline, setBaseline] = useState<Baseline>({ leadsAt: dataUpdatedAt, writes: ownWrites.count, version: latest });
  if (baseline.leadsAt !== dataUpdatedAt) {
    setBaseline({ leadsAt: dataUpdatedAt, writes: ownWrites.count, version: latest });
  } else if (baseline.writes !== ownWrites.count) {
    if (latest !== null && versionAt >= ownWrites.at) {
      setBaseline({ leadsAt: dataUpdatedAt, writes: ownWrites.count, version: latest });
    }
  } else if (baseline.version === null && latest !== null) {
    setBaseline({ ...baseline, version: latest });
  }

  // An own write: ask for the version once more (the answer re-baselines).
  const { refetch } = versionQuery;
  useEffect(() => {
    if (ownWrites.count === 0 || !enabled) return;
    void refetch();
  }, [ownWrites.count, enabled, refetch]);

  const changed = enabled
    && baseline.writes === ownWrites.count
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
