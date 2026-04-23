import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { HealthProvider, computeDegraded, useHealth, useOptionalHealth } from './HealthProvider';

/**
 * HealthProvider — unit tests for the shared `/api/health` poll.
 *
 * The provider is the single source of truth for dependency state
 * across Topbar, AgentActivityLog, and DegradedBanner (round-2
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
