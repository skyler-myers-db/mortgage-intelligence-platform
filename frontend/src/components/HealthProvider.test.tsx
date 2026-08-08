// @vitest-environment happy-dom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createRoot, type Root } from 'react-dom/client';
import {
  HealthProvider,
  applyDownUpDebounce,
  computeDegraded,
  shouldPollFast,
  useHealth,
  useOptionalHealth,
} from './HealthProvider';

/**
 * HealthProvider — unit tests for the shared `/api/health` poll.
 *
 * The provider is the single source of truth for dependency state
 * across Topbar, DegradedBanner, and other dependency-aware surfaces (round-2
 * hole-finder #21, 2026-04-23). Before this provider each of those
 * components ran its own setInterval — 3x the load on /api/health
 * and inconsistent state on the same page.
 *
 * Tests here cover the deterministic logic (`computeDegraded`) and
 * static-rendering contracts. The live-polling cadence flip is
 * covered by the existing DegradedBanner integration tests + by the
 * e2e smoke that exercises the warmup/recovery flow end-to-end.
 */

describe('computeDegraded', () => {
  it('returns false for a null payload (initial probe in-flight)', () => {
    expect(computeDegraded(null)).toBe(false);
  });

  it('returns true when status is "degraded" even if all deps look up', () => {
    expect(
      computeDegraded({
        status: 'degraded',
        mode: 'live',
        dependencies: { warehouse: 'up', lakebase: 'up' },
      }),
    ).toBe(true);
  });

  it('returns true when warehouse dependency is down', () => {
    expect(
      computeDegraded({
        status: 'ok',
        mode: 'live',
        dependencies: { warehouse: 'down', lakebase: 'up' },
      }),
    ).toBe(true);
  });

  it('returns true when any circuit breaker is open', () => {
    expect(
      computeDegraded({
        status: 'ok',
        mode: 'live',
        dependencies: { warehouse: 'up' },
        circuit_breakers: { genie: 'open' },
      }),
    ).toBe(true);
  });

  it('returns true when Genie is down even if warehouse and Lakebase are up', () => {
    expect(
      computeDegraded({
        status: 'ok',
        mode: 'live',
        dependencies: { warehouse: 'up', lakebase: 'up', genie: 'down' },
      }),
    ).toBe(true);
  });

  it('returns false for an ok payload with closed breakers', () => {
    expect(
      computeDegraded({
        status: 'ok',
        mode: 'live',
        dependencies: { warehouse: 'up', lakebase: 'up' },
        circuit_breakers: { warehouse: 'closed', lakebase: 'closed', genie: 'closed' },
      }),
    ).toBe(false);
  });
});

/**
 * Cadence selection is deliberately narrower than `computeDegraded` — the
 * 2026-08-07 audit measured `/api/v1/health` 3-4x per route load because the
 * deployed build declares `status: "degraded"` for a deploy-shaped reason
 * while every dependency is up and every breaker is closed. What the UI SAYS
 * is unchanged; only how often it asks.
 */
describe('shouldPollFast', () => {
  const allUp = {
    dependencies: { warehouse: 'up' as const, lakebase: 'up' as const, genie: 'up' as const },
    circuit_breakers: { warehouse: 'closed' as const, lakebase: 'closed' as const, genie: 'closed' as const },
  };

  it('does not fast-poll a backend-declared degrade with every dependency up', () => {
    const payload = { status: 'degraded' as const, mode: 'live', ...allUp };
    // Still degraded for display…
    expect(computeDegraded(payload)).toBe(true);
    // …but nothing here recovers on a 3-second timescale.
    expect(shouldPollFast(payload)).toBe(false);
  });

  it('fast-polls a dependency that is actually down', () => {
    for (const dep of ['warehouse', 'lakebase', 'genie']) {
      expect(
        shouldPollFast({
          status: 'ok',
          mode: 'live',
          dependencies: { ...allUp.dependencies, [dep]: 'down' },
        }),
      ).toBe(true);
    }
  });

  it('fast-polls an open circuit breaker', () => {
    expect(
      shouldPollFast({
        status: 'ok',
        mode: 'live',
        dependencies: allUp.dependencies,
        circuit_breakers: { ...allUp.circuit_breakers, genie: 'open' },
      }),
    ).toBe(true);
  });

  it('stays on the slow cadence before the first probe resolves and when healthy', () => {
    expect(shouldPollFast(null)).toBe(false);
    expect(shouldPollFast({ status: 'ok', mode: 'live', ...allUp })).toBe(false);
  });
});

describe('applyDownUpDebounce', () => {
  // Pure-function tests for the down→up debounce. Cadence + React
  // wiring are covered by the SSR-contract block + the existing
  // DegradedBanner integration tests + the e2e smoke. 2026-05-04
  // follow-up to user feedback: "the reconnecting banner seems to
  // be up a *lot*."
  const RAW_DOWN = {
    status: 'degraded' as const,
    mode: 'live',
    dependencies: { warehouse: 'down', lakebase: 'up', genie: 'up' },
  };
  const RAW_UP = {
    status: 'ok' as const,
    mode: 'live',
    dependencies: { warehouse: 'up', lakebase: 'up', genie: 'up' },
  };

  it('first probe is trusted as-is (no prior to debounce against)', () => {
    const { payload, next } = applyDownUpDebounce(RAW_UP, {}, 1000, 5000);
    expect(payload.dependencies?.warehouse).toBe('up');
    expect(next.warehouse?.filtered).toBe('up');
    expect(next.warehouse?.pendingUpSince).toBeNull();
  });

  it('raw down flips immediately to down (real outages surface fast)', () => {
    const prior = { warehouse: { filtered: 'up' as const, pendingUpSince: null } };
    const { payload } = applyDownUpDebounce(RAW_DOWN, prior, 1000, 5000);
    expect(payload.dependencies?.warehouse).toBe('down');
  });

  it('raw up after a down stays "down" inside the debounce window', () => {
    const priorDown = { warehouse: { filtered: 'down' as const, pendingUpSince: null } };
    const r1 = applyDownUpDebounce(RAW_UP, priorDown, 1000, 5000);
    expect(r1.payload.dependencies?.warehouse).toBe('down');
    expect(r1.next.warehouse?.pendingUpSince).toBe(1000);
    // Second up probe 2s later — still inside the 5s window.
    const r2 = applyDownUpDebounce(RAW_UP, r1.next, 3000, 5000);
    expect(r2.payload.dependencies?.warehouse).toBe('down');
    expect(r2.next.warehouse?.pendingUpSince).toBe(1000); // pending preserved
  });

  it('raw up after the debounce window flips to up', () => {
    const priorDown = { warehouse: { filtered: 'down' as const, pendingUpSince: null } };
    const r1 = applyDownUpDebounce(RAW_UP, priorDown, 1000, 5000);
    expect(r1.payload.dependencies?.warehouse).toBe('down');
    // 5500ms later — past the 5000ms window.
    const r2 = applyDownUpDebounce(RAW_UP, r1.next, 6500, 5000);
    expect(r2.payload.dependencies?.warehouse).toBe('up');
    expect(r2.next.warehouse?.pendingUpSince).toBeNull();
  });

  it('a brief flap in the middle of debounce keeps it down + restarts pending', () => {
    const priorDown = { warehouse: { filtered: 'down' as const, pendingUpSince: null } };
    const r1 = applyDownUpDebounce(RAW_UP, priorDown, 1000, 5000);
    // Flap back to down at t=2000 — pending should clear.
    const r2 = applyDownUpDebounce(RAW_DOWN, r1.next, 2000, 5000);
    expect(r2.payload.dependencies?.warehouse).toBe('down');
    expect(r2.next.warehouse?.pendingUpSince).toBeNull();
    // Up again at t=3000 — must restart the debounce window from t=3000.
    const r3 = applyDownUpDebounce(RAW_UP, r2.next, 3000, 5000);
    expect(r3.next.warehouse?.pendingUpSince).toBe(3000);
    // At t=7000 (4s after restart) — still inside the new window.
    const r4 = applyDownUpDebounce(RAW_UP, r3.next, 7000, 5000);
    expect(r4.payload.dependencies?.warehouse).toBe('down');
    // At t=8500 — finally past the new window.
    const r5 = applyDownUpDebounce(RAW_UP, r4.next, 8500, 5000);
    expect(r5.payload.dependencies?.warehouse).toBe('up');
  });

  it('debounceMs=0 disables the debounce (instant flips both directions)', () => {
    const priorDown = { warehouse: { filtered: 'down' as const, pendingUpSince: null } };
    const { payload } = applyDownUpDebounce(RAW_UP, priorDown, 1000, 0);
    expect(payload.dependencies?.warehouse).toBe('up');
  });
});

describe('HealthProvider browser polling', () => {
  let container: HTMLDivElement;
  let root: Root;
  let visibilityState = 'visible';
  const originalVisibility = Object.getOwnPropertyDescriptor(
    Document.prototype,
    'visibilityState',
  );

  beforeEach(() => {
    vi.useFakeTimers();
    visibilityState = 'visible';
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => visibilityState,
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    if (originalVisibility) {
      Object.defineProperty(document, 'visibilityState', originalVisibility);
    } else {
      Reflect.deleteProperty(document, 'visibilityState');
    }
    vi.useRealTimers();
  });

  it('pauses health polling while the tab is hidden and probes on return', async () => {
    const fetchHealth = vi.fn(async () => ({
      status: 'ok' as const,
      mode: 'live',
      dependencies: { warehouse: 'up', lakebase: 'up', genie: 'up' },
    }));

    await act(async () => {
      root.render(
        <HealthProvider pollIntervalOkMs={8000} fetchHealth={fetchHealth}>
          <span>ready</span>
        </HealthProvider>,
      );
    });
    expect(fetchHealth).toHaveBeenCalledTimes(1);

    visibilityState = 'hidden';
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    expect(fetchHealth).toHaveBeenCalledTimes(1);

    visibilityState = 'visible';
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(fetchHealth).toHaveBeenCalledTimes(2);
  });

  it('keeps the slow cadence when the backend declares degraded but all deps are up', async () => {
    const fetchHealth = vi.fn(async () => ({
      status: 'degraded' as const,
      mode: 'live',
      dependencies: { warehouse: 'up', lakebase: 'up', genie: 'up' },
      circuit_breakers: { warehouse: 'closed', lakebase: 'closed', genie: 'closed' },
    }));

    await act(async () => {
      root.render(
        <HealthProvider pollIntervalOkMs={8000} pollIntervalDegradedMs={3000} fetchHealth={fetchHealth}>
          <span>ready</span>
        </HealthProvider>,
      );
    });
    expect(fetchHealth).toHaveBeenCalledTimes(1);

    // A 9s route-load window used to cost three more probes at the fast
    // cadence; on the slow cadence it costs one.
    for (let i = 0; i < 3; i += 1) {
      await act(async () => {
        vi.advanceTimersByTime(3_000);
      });
    }
    expect(fetchHealth).toHaveBeenCalledTimes(2);
  });

  it('still probes fast while a dependency is actually down', async () => {
    const fetchHealth = vi.fn(async () => ({
      status: 'degraded' as const,
      mode: 'live',
      dependencies: { warehouse: 'down', lakebase: 'up', genie: 'up' },
    }));

    await act(async () => {
      root.render(
        <HealthProvider
          pollIntervalOkMs={8000}
          pollIntervalDegradedMs={3000}
          debounceUpMs={0}
          fetchHealth={fetchHealth}
        >
          <span>ready</span>
        </HealthProvider>,
      );
    });
    expect(fetchHealth).toHaveBeenCalledTimes(1);

    for (let i = 0; i < 3; i += 1) {
      await act(async () => {
        vi.advanceTimersByTime(3_000);
      });
    }
    expect(fetchHealth).toHaveBeenCalledTimes(4);
  });
});

describe('HealthProvider SSR contract', () => {
  it('renders children even before the first poll resolves', () => {
    // The provider must never throw during SSR / static render — the
    // first render always precedes the poll and consumers read the
    // `health: null` snapshot without crashing.
    const html = renderToStaticMarkup(
      <HealthProvider fetchHealth={() => new Promise(() => { /* never resolves */ })}>
        <span data-testid="child">hi</span>
      </HealthProvider>,
    );
    expect(html).toContain('hi');
  });

  it('useOptionalHealth returns null when rendered outside a provider', () => {
    // DegradedBanner relies on this path when tests mount the banner
    // standalone with a custom fetcher. If useOptionalHealth ever
    // starts throwing, DegradedBanner's standalone mode breaks.
    function Probe() {
      const ctx = useOptionalHealth();
      return <span data-ctx={ctx === null ? 'null' : 'object'}>probe</span>;
    }
    const html = renderToStaticMarkup(<Probe />);
    expect(html).toContain('data-ctx="null"');
  });

  it('useHealth throws when rendered outside a provider', () => {
    // Misuse should fail loudly so it isn't silently polling twice on
    // the same page.
    function Probe() {
      useHealth();
      return <span>probe</span>;
    }
    expect(() => renderToStaticMarkup(<Probe />)).toThrow(
      /useHealth must be used inside <HealthProvider>/,
    );
  });
});
