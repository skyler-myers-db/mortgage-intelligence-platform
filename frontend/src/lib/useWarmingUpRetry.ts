import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ApiError,
  dependencyLabel,
  isAbortError,
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
}

export function useWarmingUpRetry<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  deps: unknown[],
  opts: UseWarmingUpRetryOpts = {},
): UseWarmingUpRetryResult<T> {
  const maxAttempts = opts.maxAttempts ?? 6;
  const intervalMs = opts.intervalMs ?? 5000;
  const enabled = opts.enabled ?? true;

  const [data, setData] = useState<T | null>(null);
  const [warmingUp, setWarmingUp] = useState<WarmingUpState | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  // Keep the latest fetcher in a ref so the effect doesn't re-run on
  // every parent render. Callers typically pass an inline closure.
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const manualRetry = useCallback(() => {
    setReloadToken((n) => n + 1);
  }, []);

  useEffect(() => {
    if (!enabled) {
      setData(null);
      setWarmingUp(null);
      setError(null);
      return;
    }
    const ctrl = new AbortController();
    let cancelled = false;
    let timeoutId: ReturnType<typeof setTimeout> | null = null;

    const clearTimer = () => {
      if (timeoutId !== null) {
        clearTimeout(timeoutId);
        timeoutId = null;
      }
    };

    const runAttempt = async (attempt: number): Promise<void> => {
      if (cancelled) return;
      try {
        const result = await fetcherRef.current(ctrl.signal);
        if (cancelled) return;
        setData(result);
        setWarmingUp(null);
        setError(null);
      } catch (err: unknown) {
        if (cancelled || isAbortError(err)) return;
        if (isWarmingUpError(err)) {
          const plan = planForReason(err.reason, err.dependency, {
            intervalMs,
            maxAttempts,
          });
          // "retries_exhausted" (or any zero-budget plan): do not
          // schedule another attempt. The backend already retried and
          // failed; one more client fetch will not help.
          if (plan.stop || attempt >= plan.maxAttempts) {
            setWarmingUp(null);
            setError(err);
            return;
          }
          setWarmingUp({
            dependency: err.dependency,
            label: plan.label,
            attempt: attempt + 1,
            maxAttempts: plan.maxAttempts,
            correlationId: err.correlationId,
          });
          setError(null);
          timeoutId = setTimeout(() => {
            void runAttempt(attempt + 1);
          }, plan.intervalMs);
          return;
        }
        // Non-retryable error.
        setWarmingUp(null);
        setError(err instanceof Error ? err : new Error(String(err)));
      }
    };

    // Reset state on re-run so callers don't see stale data while the
    // next fetch is in flight (important when `deps` change — e.g.
    // borrower id switch).
    setData(null);
    setWarmingUp(null);
    setError(null);

    void runAttempt(1);

    return () => {
      cancelled = true;
      clearTimer();
      ctrl.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, reloadToken, ...deps]);

  return { data, warmingUp, error, manualRetry };
}
