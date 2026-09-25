import type { ReactNode } from 'react';
import type { WarmingUpState } from '../../lib/useWarmingUpRetry';
import { isAbortError } from '../../lib/apiTransport';
import { lazyModule, useLazyModule } from '../mortgage/useLazyModule';
import { WarmingUpBlock } from './WarmingUpBlock';

/**
 * AsyncState / AsyncStatus — the one loading / warming / error / empty
 * surface for a query (audit 2026-09-21 `states-04` slice 2, `states-03`
 * part a, `states-v2`, `states-08` part 3). Routes used to hand-roll each
 * state with raw error text; this renders the shared vocabulary:
 *
 *   AsyncStatus, at most one of:
 *     1. warming with no data yet   -> WarmingUpBlock (it steps aside for
 *                                      the banner by itself);
 *     2. a failure                  -> AsyncFailure (AsyncFailure.tsx): the
 *                                      calm bannered status under a banner
 *                                      that already names the outage, else a
 *                                      role=alert callout in the
 *                                      describeApiError vocabulary;
 *     3. nothing.
 *   AsyncState adds the data slot: children(data) when there is a payload,
 *   `empty` ONLY for a settled, measured zero, else `loading`.
 *
 * The failure half is a separate chunk loaded only once a failure needs it,
 * so the natural load of every route carries none of it. While it loads (or
 * if it cannot) a neutral line says the panel could not load.
 */

/** The useWarmingUpRetry result, or an adapter of the same shape. */
export interface AsyncQuery<T> {
  data: T | null;
  warmingUp: WarmingUpState | null;
  error: Error | null;
  manualRetry: () => void;
  isFetching: boolean;
  isPlaceholderData: boolean;
  errorUpdatedAt?: number | null;
}

interface AsyncStatusProps {
  query: AsyncQuery<unknown>;
  /** What this panel shows, sentence case: "Ranked borrowers". */
  subject: string;
  /** The 422 action; without it an invalid-filter error offers no button. */
  onClearFilters?: () => void;
  compact?: boolean;
}

const FAILURE = lazyModule(() => import('./AsyncFailure'));

/** Load the failure half ahead of need (a server render, a test); later mounts render it at once. */
export const preloadAsyncFailure = (): Promise<unknown> => FAILURE.load();

export function AsyncStatus({ query, subject, onClearFilters, compact }: AsyncStatusProps) {
  const { error } = query;
  const failure = useLazyModule(FAILURE, error !== null && !isAbortError(error)).module;
  // TanStack keeps `failureReason` after the last retry, so `warmingUp`
  // outlives the loop; once `error` is set the honest surface is the error.
  const warming = error === null ? query.warmingUp : null;
  if (warming && query.data === null) {
    return <WarmingUpBlock state={warming} title={`${subject} loading`} compact={compact} />;
  }
  if (error === null || isAbortError(error)) return null;
  if (!failure) {
    return (
      <div className="status-callout" role="status" data-async-status="pending">
        {subject} could not load.
      </div>
    );
  }
  return (
    <failure.AsyncFailure
      error={error}
      subject={subject}
      onRetry={query.manualRetry}
      onClearFilters={onClearFilters}
      errorUpdatedAt={query.errorUpdatedAt ?? null}
    />
  );
}

interface AsyncStateProps<T> {
  query: AsyncQuery<T>;
  subject: string;
  /** The skeleton for a first load. */
  loading: ReactNode;
  /** True for a payload with nothing in it (a measured zero, once settled). */
  isEmpty: (data: T) => boolean;
  empty: ReactNode;
  onClearFilters?: () => void;
  children: (data: T) => ReactNode;
}

export function AsyncState<T>({ query, subject, loading, isEmpty, empty, onClearFilters, children }: AsyncStateProps<T>) {
  const status = <AsyncStatus query={query} subject={subject} onClearFilters={onClearFilters} compact />;
  const { data, error } = query;
  const warming = error === null && query.warmingUp !== null;
  if (data !== null && !isEmpty(data)) {
    return (
      <>
        {status}
        {children(data)}
      </>
    );
  }
  if (data !== null && !query.isPlaceholderData) {
    // EmptyState only for a MEASURED zero: settled, not warming, no error.
    if (error === null && !warming) return <>{empty}</>;
    if (error !== null) return status;
    return <>{loading}</>;
  }
  // No payload yet, or a placeholder zero (the previous filters' rows).
  if (data === null && error !== null) return status;
  // The DegradedBanner can suppress the warming block, so the skeleton keeps
  // the slot honest while warming (states-v1).
  return (
    <>
      {data === null && warming ? status : null}
      {loading}
    </>
  );
}
