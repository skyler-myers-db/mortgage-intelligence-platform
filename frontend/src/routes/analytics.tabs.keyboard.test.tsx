/**
 * @vitest-environment happy-dom
 *
 * Analytics view tabs follow the APG tabs pattern (2026-09-21 audit a11y-02).
 * They used to be click-only buttons with role=tab: no arrow keys, every tab
 * in the tab order, and no tabpanel for aria-controls to name. The tabs now
 * run on the shared useTabs primitive; this drives the rendered route.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TABS } from './analytics.lib';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../lib/useWarmingUpRetry', () => ({
  useWarmingUpRetry: () => ({
    data: null,
    warmingUp: null,
    error: null,
    isFetching: false,
    manualRetry: vi.fn(),
  }),
}));

vi.mock('../lib/configOptionsQuery', () => ({
  useConfigOptionsQuery: () => ({ data: { target_lender_refs: ['All'] }, isError: false }),
}));

vi.mock('../components/FootprintProvider', () => ({
  useFootprint: () => ({
    ready: true,
    usingFallback: false,
    states: [{ state_code: 'IL', state_name: 'Illinois', display_order: 1, is_default_state: true }],
  }),
}));

vi.mock('../lib/api', () => ({ api: {} }));

import AnalyticsRoute from './analytics';

function LocationProbe() {
  return <output id="location-probe">{useLocation().search}</output>;
}
const search = (): string => document.getElementById('location-probe')?.textContent ?? '';

describe('Analytics view tabs keyboard', () => {
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(async () => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/analytics']}>
            <AnalyticsRoute />
            <LocationProbe />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    document.body.innerHTML = '';
  });

  const tab = (label: string): HTMLButtonElement => {
    const found = [...document.querySelectorAll<HTMLButtonElement>('button[role="tab"]')].find(
      (node) => node.textContent?.trim() === label,
    );
    if (!found) throw new Error(`tab ${label} not rendered`);
    return found;
  };
  const panel = (): HTMLElement | null => document.querySelector<HTMLElement>('[role="tabpanel"]');

  async function press(key: string): Promise<void> {
    await act(async () => {
      document.activeElement?.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
    });
  }

  it('keeps one tab stop and a tabpanel labelled by the selected tab', () => {
    const tabs = [...document.querySelectorAll<HTMLButtonElement>('button[role="tab"]')];
    expect(tabs).toHaveLength(TABS.length);
    expect(tabs.filter((node) => node.tabIndex === 0)).toEqual([tab('Executive')]);
    expect(tab('Executive').getAttribute('aria-controls')).toBe(panel()?.id);
    expect(panel()?.getAttribute('aria-labelledby')).toBe(tab('Executive').id);
    // The filters belong to the selected view's panel.
    expect(panel()?.querySelector('.analytics-filters')).not.toBeNull();
  });

  it('switches views with the arrow keys and Home, updating ?view=', async () => {
    tab('Executive').focus();
    await press('ArrowRight');
    expect(document.activeElement).toBe(tab('Geography'));
    expect(tab('Geography').getAttribute('aria-selected')).toBe('true');
    expect(search()).toBe('?view=geography');
    expect(panel()?.getAttribute('aria-labelledby')).toBe(tab('Geography').id);

    await press('ArrowRight');
    expect(document.activeElement).toBe(tab('Economics'));
    expect(search()).toBe('?view=economics');

    await press('Home');
    expect(document.activeElement).toBe(tab('Executive'));
    expect(tab('Executive').getAttribute('aria-selected')).toBe('true');
    expect(search()).toBe('');
  });
});
