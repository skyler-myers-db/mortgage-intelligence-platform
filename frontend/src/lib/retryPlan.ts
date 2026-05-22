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
  if (reason === 'rate_limited') {
    // Backpressure 429s carry an exact Retry-After header that api.ts honors
    // before an ApiError is thrown. If a caller still reaches this planner
    // (for example through a future 503-compatible body), pace retries at the
    // expensive-read bucket horizon instead of falling back to warming cadence.
    return {
      reason,
      label: `${depName} request budget cooling`,
      intervalMs: 30_000,
      maxAttempts: 2,
      stop: false,
    };
  }
  if (reason === 'dependency_saturated') {
    // Concurrency saturation is usually shorter-lived than a token-bucket
    // rate limit, but should still use a distinct label/cadence so UI copy
    // doesn't describe it as warehouse auto-suspend warming.
    return {
      reason,
      label: `${depName} concurrency saturated`,
      intervalMs: Math.max(2_000, Math.min(defaults.intervalMs, 5_000)),
      maxAttempts: Math.min(defaults.maxAttempts, 3),
      stop: false,
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
