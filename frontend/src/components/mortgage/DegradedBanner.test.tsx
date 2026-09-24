// @vitest-environment happy-dom

import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it, vi } from 'vitest';
import { HealthProvider } from '../HealthProvider';
import { DegradedBanner, degradedDependency, shouldUseStandaloneHealth } from './DegradedBanner';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

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

describe('a resuming warehouse (audit delivery-01)', () => {
  it('is never the degraded dependency', () => {
    expect(
      degradedDependency({
        status: 'ok',
        dependencies: { warehouse: 'resuming', lakebase: 'up', genie: 'up' },
        circuit_breakers: { warehouse: 'closed', lakebase: 'closed', genie: 'closed' },
      }),
    ).toBeNull();
  });

  it('renders no banner through the shared health poll', async () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    const fetchHealth = vi.fn(async () => ({
      status: 'ok',
      mode: 'live',
      dependencies: { warehouse: 'resuming', lakebase: 'up', genie: 'up' },
      circuit_breakers: { warehouse: 'closed', lakebase: 'closed', genie: 'closed' },
    }));
    await act(async () => {
      root.render(
        <HealthProvider debounceUpMs={0} fetchHealth={fetchHealth}>
          <DegradedBanner onReload={() => undefined} />
        </HealthProvider>,
      );
    });

    expect(fetchHealth).toHaveBeenCalledTimes(1);
    expect(container.querySelector('.degraded-banner')).toBeNull();
    act(() => root.unmount());
    container.remove();
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
