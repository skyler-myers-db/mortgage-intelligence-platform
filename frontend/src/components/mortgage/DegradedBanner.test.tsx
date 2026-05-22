import { describe, expect, it } from 'vitest';
import { degradedDependency, shouldUseStandaloneHealth } from './DegradedBanner';

/**
 * DegradedBanner dependency selection.
 *
 * The backend treats warehouse, Lakebase, and Genie as first-class health
 * dependencies. The visible banner must therefore explain a Genie outage too;
 * otherwise Topbar shows Degraded while the banner stays silent.
 */

describe('degradedDependency', () => {
  it('surfaces Genie as the degraded dependency when only Genie is down', () => {
    expect(
      degradedDependency({
        status: 'degraded',
        dependencies: { warehouse: 'up', lakebase: 'up', genie: 'down' },
        circuit_breakers: { warehouse: 'closed', lakebase: 'closed', genie: 'closed' },
      }),
    ).toBe('genie');
  });

  it('keeps warehouse and Lakebase precedence before breaker fallback', () => {
    expect(
      degradedDependency({
        status: 'degraded',
        dependencies: { warehouse: 'down', lakebase: 'up', genie: 'down' },
        circuit_breakers: { genie: 'open' },
      }),
    ).toBe('warehouse');
    expect(
      degradedDependency({
        status: 'degraded',
        dependencies: { warehouse: 'up', lakebase: 'down', genie: 'down' },
        circuit_breakers: { genie: 'open' },
      }),
    ).toBe('lakebase');
  });

  it('falls back to open circuit breakers when dependency probes are up', () => {
    expect(
      degradedDependency({
        status: 'degraded',
        dependencies: { warehouse: 'up', lakebase: 'up', genie: 'up' },
        circuit_breakers: { genie: 'open' },
      }),
    ).toBe('genie');
  });
});

describe('shouldUseStandaloneHealth', () => {
  it('uses the shared HealthProvider poll in production AppShell mounts', () => {
    expect(shouldUseStandaloneHealth(false, true)).toBe(false);
  });

  it('keeps standalone polling for isolated mounts and injected test fetchers', () => {
    expect(shouldUseStandaloneHealth(false, false)).toBe(true);
    expect(shouldUseStandaloneHealth(true, true)).toBe(true);
  });
});
