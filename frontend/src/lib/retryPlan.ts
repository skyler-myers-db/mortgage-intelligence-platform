import { dependencyLabel } from './api';

/**
 * Cycle-13 cadence plan, derived from the 503 body's `reason` field.
 * Kept separate from the React hook so the app bootstrap can configure
 * QueryClient retries without pulling route-level hook code into the
 * initial bundle.
 */
export interface RetryPlan {
  /** Known backend reason code, or an unknown forward-compatible string. */
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

/** Map a transient backend reason + dependency to a retry plan. */
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
  //
  // `rate_limited` and `dependency_saturated` are not planned here: only the
  // backpressure middleware emits them, always as a 429, and the planner runs
  // only for isWarmingUpError (a 503). A 429 surfaces with `retryAfterMs`
  // and the screen counts that wait down (audit states-08); the branches
  // that used to sit here were unreachable.
  return {
    reason,
    label: `${depName} warming up`,
    intervalMs: defaults.intervalMs,
    maxAttempts: defaults.maxAttempts,
    stop: false,
  };
}
