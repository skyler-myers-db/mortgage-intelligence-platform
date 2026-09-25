import { useCallback, useMemo } from 'react';
import { keepPreviousData, useQuery, type QueryKey } from '@tanstack/react-query';
import {
  ApiError,
  isWarmingUpError,
} from './api';
import { DEFAULT_QUERY_STALE_MS } from './queryClient';
import { planForReason, type RetryPlan } from './retryPlan';

/**
 * useWarmingUpRetry — shared cold-start retry loop for any API call that
 * reads live Unity Catalog / Lakebase data.
 *
 * Cold-boot symptom (2026-04-23): Databricks SQL warehouses auto-suspend
 * when idle; a serverless resume is usually 2–6 s but can run longer. During
 * that window the backend's
 * `_dependency_down_handler` returns HTTP 503 with
 * `{retryable: true, dependency: "warehouse", reason: "warming_up"}`.
 * `api.ts`'s `_fetchWithRetry` already retries 3× with backoff, but on a
 * genuinely cold warehouse that still isn't enough — the user sees a red
 * "Backend unavailable" banner on first nav.
 *
 * Cycle 13 added `reason ∈ {warming_up, breaker_open, retries_exhausted}`
 * to the 503 body so the client can pick a cadence that matches the
 * server's state:
 *   - "warming_up"        → 5s interval × 6 attempts (30s, the default).
 *   - "breaker_open"      → 30s interval × 2 attempts (60s — gives the
 *                           breaker its full cooldown plus a half-open
 *                           probe. Retrying every 5s against an open
 *                           breaker just burns the budget.)
 *   - "retries_exhausted" → 0 attempts; surface the error immediately.
 *                           Further client retries will not help.
 *   - null / unknown      → fall back to the "warming_up" cadence.
 *
 * The hook's public shape does NOT change — callers still read
 * `{data, warmingUp, error, manualRetry}`. `warmingUp.label` is the
 * only operator-visible tell that we're in the breaker branch
 * ("Circuit breaker cooling" vs "Warehouse warming up").
 *
 * Behavior otherwise:
 *   - runs `fetcher(signal)` on mount and whenever `opts.queryKey` changes.
 *   - on a retryable `ApiError`: show the warming-up block and schedule
 *     the next attempt at the reason-appropriate cadence.
 *   - on any other error: surface it as `error` and stop retrying.
 *   - on success at any attempt: clear any warming-up state.
 *
 * The caller owns how to render the three states (`data`, `warmingUp`,
 * `error`). `manualRetry()` is exposed so a visible "Retry" button can
 * kick the fetch back to attempt 1 after exhaustion.
 *
 * `AbortController` is wired so the in-flight fetch is cancelled on
 * unmount or when `opts.queryKey` changes (prevents setState-after-unmount and
 * prevents a stale response from clobbering a newer one).
 */

export { planForReason };
export type { RetryPlan };

export interface WarmingUpState {
  /** Which dependency returned 503 — "warehouse", "lakebase", "genie", … */
  dependency: string | null;
  /** Human-facing eyebrow label, e.g. "Warehouse warming up". */
  label: string;
  /** 1-indexed attempt counter (1 … maxAttempts). */
  attempt: number;
  /** Upper bound — 6 unless caller overrides. */
  maxAttempts: number;
  /** Correlation id from the 503 body, if any — for support tickets. */
  correlationId: string | null;
  /**
   * The plan's wait between attempts, in ms (audit states-08 part 4): the
   * block's "next try in N s". Optional so hand-built states still compile;
   * without it the block shows no next-try clock.
   */
  intervalMs?: number;
}

export interface UseWarmingUpRetryResult<T> {
  data: T | null;
  /** Non-null while in a 503 warming-up loop. */
  warmingUp: WarmingUpState | null;
  /** Non-null after warming-up attempts are exhausted OR on a genuine error. */
  error: ApiError | Error | null;
  /** Force a retry — resets attempt counter. */
  manualRetry: () => void;
  /**
   * True while ANY fetch is in flight — including a background refetch of
   * an unchanged key where `data` is still the previous (stale) payload.
   * Re-audit #3 (2026-06-12, P1): callers that gate mutations on
   * "`data === null` means loading" miss exactly that refetch window —
   * portfolio-builder's Save stayed clickable ~0s into a re-run build.
   * Gate on `isFetching` for anything that must not race an in-flight load.
   */
  isFetching: boolean;
  /** True when the rendered data is the previous key's payload during a refetch. */
  isPlaceholderData: boolean;
  /** When `data` was fetched (epoch ms), or null before any success: "Fetched 3 min ago". */
  dataUpdatedAt: number | null;
  /** When `error` was last set (epoch ms), or null: a 429's countdown starts here. */
  errorUpdatedAt: number | null;
}

export interface UseWarmingUpRetryOpts {
  /** Default 6. */
  maxAttempts?: number;
  /** Interval between warming-up retries in ms. Default 5000. */
  intervalMs?: number;
  /** If false, the hook is a no-op (used when an id param is missing). */
  enabled?: boolean;
  /**
   * Stable cache key, required. It is the ONLY thing that decides when the
   * fetch re-runs and which cache entry it lands in, so it must carry every
   * input the fetcher reads. Build it from the same object the fetcher reads
   * (see `lib/leadsQuery.ts`) rather than keeping a second hand-written list:
   * a second list is how the Lead Queue once served one city's rows under
   * another city's chip (audit runtime-02, 2026-09-21).
   */
  queryKey: QueryKey;
  /** Query freshness window. Default comes from the app QueryClient. */
  staleTime?: number;
  /** Override focus refetch for expensive one-shot calls such as Genie answers. */
  refetchOnWindowFocus?: boolean;
  /** Preserve the previous payload while a changed query key refetches. */
  keepPreviousData?: boolean;
  /**
   * Key-aware form of `keepPreviousData` (audit runtime-06): keep the previous
   * payload only when this predicate accepts the key it was fetched under.
   * The geography map keeps a drilled state's ZIP tiles while its cohort
   * re-reads, but never paints one state's tiles under another state. Takes
   * precedence over the boolean form.
   */
  keepPreviousWhen?: (previousKey: QueryKey) => boolean;
}

export function useWarmingUpRetry<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  opts: UseWarmingUpRetryOpts,
): UseWarmingUpRetryResult<T> {
  const maxAttempts = opts.maxAttempts ?? 6;
  const intervalMs = opts.intervalMs ?? 5000;
  const enabled = opts.enabled ?? true;

  const { queryKey, keepPreviousWhen } = opts;

  const query = useQuery<T, ApiError | Error>({
    queryKey,
    enabled,
    queryFn: ({ signal }) => fetcher(signal),
    staleTime: opts.staleTime ?? DEFAULT_QUERY_STALE_MS,
    refetchOnWindowFocus: opts.refetchOnWindowFocus,
    placeholderData: keepPreviousWhen
      ? (previous, previousQuery) =>
          previousQuery && keepPreviousWhen(previousQuery.queryKey) ? previous : undefined
      : opts.keepPreviousData
        ? keepPreviousData
        : undefined,
    retry: (failureCount, err) => {
      if (!isWarmingUpError(err)) return false;
      const plan = planForReason(err.reason, err.dependency, {
        intervalMs,
        maxAttempts,
      });
      if (plan.stop) return false;
      return failureCount < Math.max(0, plan.maxAttempts - 1);
    },
    retryDelay: (_failureCount, err) => {
      if (!isWarmingUpError(err)) return 0;
      return planForReason(err.reason, err.dependency, {
        intervalMs,
        maxAttempts,
      }).intervalMs;
    },
  });

  const failureReason = query.failureReason;
  const warmingUp = useMemo<WarmingUpState | null>(() => {
    if (query.data !== undefined && !query.isPlaceholderData) return null;
    if (!isWarmingUpError(failureReason)) return null;
    const plan = planForReason(failureReason.reason, failureReason.dependency, {
      intervalMs,
      maxAttempts,
    });
    if (plan.stop) return null;
    return {
      dependency: failureReason.dependency,
      label: plan.label,
      attempt: Math.min(query.failureCount + 1, Math.max(1, plan.maxAttempts)),
      maxAttempts: plan.maxAttempts,
      correlationId: failureReason.correlationId,
      intervalMs: plan.intervalMs,
    };
  }, [failureReason, intervalMs, maxAttempts, query.data, query.failureCount, query.isPlaceholderData]);

  // `refetch` is the observer's bound method, stable for the hook's life;
  // the result object around it is not (react-query returns a fresh tracked
  // result each render). Keying on it keeps `manualRetry`, and so the whole
  // returned object, stable across renders the query did not cause: a map
  // hover no longer re-renders every consumer of the reads (runtime-07).
  const { refetch } = query;
  const manualRetry = useCallback(() => {
    void refetch();
  }, [refetch]);

  return {
    data: query.data ?? null,
    warmingUp,
    error: query.error ?? null,
    manualRetry,
    isFetching: query.isFetching,
    isPlaceholderData: query.isPlaceholderData,
    dataUpdatedAt: query.dataUpdatedAt || null,
    errorUpdatedAt: query.errorUpdatedAt || null,
  };
}
