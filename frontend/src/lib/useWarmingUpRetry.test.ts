import { describe, expect, it } from 'vitest';
import { planForReason } from './useWarmingUpRetry';

/**
 * planForReason — cycle-13 retry-cadence decision table.
 *
 * Before cycle 13 the hook retried 6× / 5s regardless of why the
 * backend returned a 503. After cycle 13 the backend classifies the
 * failure in `reason ∈ {warming_up, breaker_open, retries_exhausted}`
 * and the client picks a cadence that respects the classification:
 *
 *   - "warming_up":        5s × 6 = 30s window (default).
 *   - "breaker_open":      30s × 2 = 60s window. Matches the server's
 *                          CircuitBreaker cooldown so we don't hammer
 *                          an open breaker.
 *   - "retries_exhausted": stop immediately; surface the error.
 *   - "rate_limited":      30s × 2 fallback when the exact Retry-After
 *                          header is unavailable at this layer.
 *   - "dependency_saturated": short bounded retry for concurrent overload.
 */

const DEFAULTS = { intervalMs: 5000, maxAttempts: 6 };

describe('planForReason', () => {
  it('breaker_open -> 30s interval, 2 attempts, keep retrying', () => {
    const plan = planForReason('breaker_open', 'warehouse', DEFAULTS);
    // 30s interval gives the backend CircuitBreaker its full cooldown
    // window before we probe again. 2 attempts = 60s total, which
    // covers one cooldown + a half-open probe.
    expect(plan.intervalMs).toBe(30_000);
    expect(plan.maxAttempts).toBe(2);
    expect(plan.stop).toBe(false);
    expect(plan.label.toLowerCase()).toContain('circuit breaker');
  });

  it('retries_exhausted -> stop=true, maxAttempts=0', () => {
    const plan = planForReason('retries_exhausted', 'lakebase', DEFAULTS);
    // Backend burned its internal retry budget. One more client fetch
    // is not going to help — surface the failure immediately.
    expect(plan.stop).toBe(true);
    expect(plan.maxAttempts).toBe(0);
    expect(plan.label.toLowerCase()).toContain('lakebase');
  });

  it('rate_limited -> backpressure cadence, not warming cadence', () => {
    const plan = planForReason('rate_limited', 'warehouse', DEFAULTS);
    expect(plan.intervalMs).toBe(30_000);
    expect(plan.maxAttempts).toBe(2);
    expect(plan.stop).toBe(false);
    expect(plan.label.toLowerCase()).toContain('budget');
  });

  it('dependency_saturated -> short bounded concurrency cadence', () => {
    const plan = planForReason('dependency_saturated', 'warehouse', DEFAULTS);
    expect(plan.intervalMs).toBe(5000);
    expect(plan.maxAttempts).toBe(3);
    expect(plan.stop).toBe(false);
    expect(plan.label.toLowerCase()).toContain('saturated');
  });

  it('warming_up -> defaults (5s × 6)', () => {
    const plan = planForReason('warming_up', 'warehouse', DEFAULTS);
    expect(plan.intervalMs).toBe(5000);
    expect(plan.maxAttempts).toBe(6);
    expect(plan.stop).toBe(false);
    expect(plan.label.toLowerCase()).toContain('warming up');
  });

  it('null reason (older backend) -> defaults, not stop', () => {
    const plan = planForReason(null, 'warehouse', DEFAULTS);
    expect(plan.intervalMs).toBe(5000);
    expect(plan.maxAttempts).toBe(6);
    expect(plan.stop).toBe(false);
  });

  it('unknown reason -> defaults, not stop', () => {
    const plan = planForReason('brand-new-reason', 'warehouse', DEFAULTS);
    expect(plan.stop).toBe(false);
    expect(plan.intervalMs).toBe(5000);
  });

  it('respects a custom default interval/attempts', () => {
    const plan = planForReason('warming_up', 'warehouse', {
      intervalMs: 2000,
      maxAttempts: 3,
    });
    expect(plan.intervalMs).toBe(2000);
    expect(plan.maxAttempts).toBe(3);
  });

  it('breaker_open cadence delta vs warming_up is 6x — the reason for the fix', () => {
    // Documented guardrail: the whole point of R6-05 was that retrying
    // every 5s burns the retry budget before the 30s breaker cooldown
    // elapses. The ratio must stay at least 6x or the fix regresses.
    const breaker = planForReason('breaker_open', 'warehouse', DEFAULTS);
    const warming = planForReason('warming_up', 'warehouse', DEFAULTS);
    expect(breaker.intervalMs / warming.intervalMs).toBeGreaterThanOrEqual(6);
  });
});
