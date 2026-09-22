// @vitest-environment happy-dom

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { DegradedBanner, type HealthPayload } from './DegradedBanner';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * Truthful banner copy (audit 2026-09-21 `states-03`). The banner used to say
 * "This page refreshes every 3 seconds". The page never refreshed: only the
 * health check repeats. What recovers the panels is HealthProvider's refetch on
 * the down → up edge (healthRecovery.test.tsx), so that is what the copy says.
 */
describe('DegradedBanner copy', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  async function renderDegraded(pollIntervalDegradedMs?: number): Promise<string> {
    const fetchHealth = async (): Promise<HealthPayload> => ({
      status: 'degraded',
      dependencies: { warehouse: 'down', lakebase: 'up' },
    });
    await act(async () => {
      root.render(<DegradedBanner fetchHealth={fetchHealth} pollIntervalDegradedMs={pollIntervalDegradedMs} />);
    });
    await act(async () => {
      await Promise.resolve();
    });
    return container.querySelector('.degraded-banner__sub')?.textContent ?? '';
  }

  it('says the connection is re-checked, not that the page refreshes', async () => {
    const sub = await renderDegraded();

    expect(container.querySelector('.degraded-banner__title')?.textContent).toBe(
      'Reconnecting to analytics warehouse',
    );
    expect(sub).toBe(
      'Checking the connection every 3 seconds. Panels that could not load will reload on their own once it is back.',
    );
    expect(sub).not.toMatch(/page refreshes/i);
  });

  it('states the cadence it is actually given', async () => {
    expect(await renderDegraded(5000)).toContain('every 5 seconds');
  });
});
