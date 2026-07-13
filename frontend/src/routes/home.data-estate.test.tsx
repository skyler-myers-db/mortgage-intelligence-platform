/**
 * @vitest-environment happy-dom
 *
 * Pins that the Data-estate panel no longer renders on Home (relocated to the
 * Administration console 2026-07-10). Home leads with KPIs, geography, and the
 * approval queue; platform/source-readiness chrome lives in admin.
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// Hold Home in its warming-up state so the KPI row and portfolio summary are
// gated off; the point of this test is what is absent, not the KPI numbers.
vi.mock('../lib/useWarmingUpRetry', () => ({
  useWarmingUpRetry: () => ({
    data: null,
    warmingUp: { label: 'Warming up', attempt: 1, maxAttempts: 6 },
    error: null,
    manualRetry: vi.fn(),
  }),
}));

vi.mock('../components/AppContext', () => ({
  useApp: () => ({ lender: 'Summit Mortgage' }),
}));

vi.mock('../components/mortgage/USChoroplethMap', () => ({
  USChoroplethMap: () => <div data-testid="us-choropleth-map" />,
}));
vi.mock('../components/mortgage/PinnedInsights', () => ({
  PinnedInsights: () => <div data-testid="pinned-insights" />,
}));

vi.mock('../lib/api', () => ({
  api: { portfolioPreview: vi.fn() },
}));

import Home from './home';

describe('Home data estate relocation', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  it('does not render the data-estate panel or its skeleton on Home', () => {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={['/']}>
          <Home />
        </MemoryRouter>,
      );
    });

    expect(document.querySelector('.data-estate')).toBeNull();
    expect(document.body.textContent).not.toContain('Data estate under the hood');
    // Sanity: Home still renders its own operator content.
    expect(document.body.textContent).toContain('Approval queue');
  });

  it('keeps geography full width and removes the admin-only activity log', () => {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={['/']}>
          <Home />
        </MemoryRouter>,
      );
    });

    const map = document.querySelector('[data-testid="us-choropleth-map"]');
    expect(map).toBeTruthy();
    expect(map?.closest('.layoutA-grid')).toBeNull();
    expect(document.querySelector('[data-testid="agent-activity-log"]')).toBeNull();
    expect(document.body.textContent).not.toContain('Agent action audit log');
  });
});
