import { useCallback, useMemo } from 'react';
import { useQuery, type QueryKey } from '@tanstack/react-query';
import {
  ApiError,
  dependencyLabel,
  isWarmingUpError,
} from './api';

/**
 * useWarmingUpRetry — shared cold-start retry loop for any API call that
 * reads live Unity Catalog / Lakebase data.
 *
 * Cold-boot symptom (2026-04-23): Databricks SQL warehouses auto-suspend
 * when idle and take ~30–60s to warm. During that window the backend's
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
 *   - runs `fetcher(signal)` on mount and when `deps` change.
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
 * unmount or when `deps` change (prevents setState-after-unmount and
 * prevents a stale response from clobbering a newer one).
 */

/**
 * Cycle-13 cadence plan, derived from the 503 body's `reason` field.
 * Exported so tests can assert the branching without reaching into the
 * hook's internal state.
 */
export interface RetryPlan {
  /** "warming_up" | "breaker_open" | "retries_exhausted" | unknown. */
  reason: string | null;
  /** Human label for the WarmingUpBlock header. */
  label: string;
  /** Interval between attempts, in ms. */
  intervalMs: number;
  /** Maximum attempt count (1-indexed ceiling). */
  maxAttempts: number;
  /** When true, do not schedule another attempt — surface the error. */
  stop: boolean;
}

/** Map a 503 reason + dependency to a retry plan. */
export function planForReason(
  reason: string | null,
  dependency: string | null,
  defaults: { intervalMs: number; maxAttempts: number },
): RetryPlan {
  const depName = dependencyLabel(dependency);
  if (reason === 'breaker_open') {
    // Backend CircuitBreaker cools for 30s before it will probe the
    // dependency again. Retrying every 5s just posts 5 requests into
    // the open breaker and burns our attempt budget. Pace ourselves
    // to the cooldown and probe at the boundary + once more.
    return {
      reason,
      label: `${depName} circuit breaker cooling`,
      intervalMs: 30_000,
      maxAttempts: 2,
      stop: false,
    };
  }
  if (reason === 'retries_exhausted') {
    // Backend already burned its internal retry budget on this call.
    // Client-side retry cannot fix that — surface the error so the
    // user sees a real failure state (with a Retry button) instead of
    // a 30s warming-up facade.
    return {
      reason,
      label: `${depName} unavailable`,
      intervalMs: defaults.intervalMs,
      maxAttempts: 0,
      stop: true,
    };
  }
  // "warming_up", null, or any unknown reason -> default cadence.
  return {
    reason,
    label: `${depName} warming up`,
    intervalMs: defaults.intervalMs,
    maxAttempts: defaults.maxAttempts,
    stop: false,
  };
}

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
}

export interface UseWarmingUpRetryResult<T> {
  data: T | null;
  /** Non-null while in a 503 warming-up loop. */
  warmingUp: WarmingUpState | null;
  /** Non-null after warming-up attempts are exhausted OR on a genuine error. */
  error: ApiError | Error | null;
  /** Force a retry — resets attempt counter. */
  manualRetry: () => void;
}

export interface UseWarmingUpRetryOpts {
  /** Default 6. */
  maxAttempts?: number;
  /** Interval between warming-up retries in ms. Default 5000. */
  intervalMs?: number;
  /** If false, the hook is a no-op (used when an id param is missing). */
  enabled?: boolean;
  /** Stable cache key. Pass a semantic key so unrelated fetchers never collide. */
  queryKey?: QueryKey;
  /** Query freshness window. Default comes from the app QueryClient. */
  staleTime?: number;
  /** Override focus refetch for expensive one-shot calls such as Genie answers. */
  refetchOnWindowFocus?: boolean;
}

export function useWarmingUpRetry<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  deps: unknown[],
  opts: UseWarmingUpRetryOpts = {},
): UseWarmingUpRetryResult<T> {
  const maxAttempts = opts.maxAttempts ?? 6;
  const intervalMs = opts.intervalMs ?? 5000;
  const enabled = opts.enabled ?? true;

  const queryKey = useMemo<QueryKey>(
    () => opts.queryKey ?? ['warming-up-retry', ...deps],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [opts.queryKey, ...deps],
  );

  const query = useQuery<T, ApiError | Error>({
    queryKey,
    enabled,
    queryFn: ({ signal }) => fetcher(signal),
    staleTime: opts.staleTime,
    refetchOnWindowFocus: opts.refetchOnWindowFocus,
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
    if (query.data !== undefined) return null;
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
    };
  }, [failureReason, intervalMs, maxAttempts, query.data, query.failureCount]);

  const manualRetry = useCallback(() => {
    void query.refetch();
  }, [query]);

  return {
    data: query.data ?? null,
    warmingUp,
    error: query.error ?? null,
    manualRetry,
  };
}
