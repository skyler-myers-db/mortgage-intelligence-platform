/**
 * @vitest-environment happy-dom
 *
 * The Analytics view tabs must expose their visible label as their accessible
 * name. A 2026-08-08 UX walk reported the tab elements as having EMPTY
 * accessible names (they were `.filter` chips holding an icon and a
 * `.filter__value` span). Since the 2026-09-21 audit (visual-05, M part) the
 * tabs are the prototype's `.layout-tabs` tablist with text-only buttons, as
 * in design_files/Module 0 Prototype.html:958-960: the button's own text is
 * the name. This pins that: the day someone wraps the label in a hidden span,
 * swaps it for a pseudo-element or brings back a chip value, the tabs would
 * go nameless or noisy, and that must fail here rather than in a screen
 * reader.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
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
    states: [
      { state_code: 'IL', state_name: 'Illinois', display_order: 1, is_default_state: true },
    ],
  }),
}));

vi.mock('../lib/api', () => ({ api: {} }));

import AnalyticsRoute from './analytics';

describe('Analytics view tabs accessibility', () => {
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  it('names every tab with its visible label, text only, on the .layout-tabs tablist', async () => {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/analytics']}>
            <AnalyticsRoute />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const tabs = [...document.querySelectorAll<HTMLButtonElement>('button[role="tab"]')];
    expect(tabs.length).toBe(TABS.length);
    expect(tabs.map((tab) => tab.textContent?.trim())).toEqual(TABS.map((tab) => tab.label));

    for (const tab of tabs) {
      // Text content, not an aria-label override: the accessible name comes
      // from the label the user can see.
      expect(tab.getAttribute('aria-label')).toBeNull();
      expect(tab.textContent?.trim()).not.toBe('');
      // Text-only, as in the prototype: no glyph and no chip value span.
      expect(tab.querySelector('svg')).toBeNull();
      expect(tab.querySelector('.filter__value')).toBeNull();
      expect(tab.classList.contains('filter')).toBe(false);
    }

    const tablist = document.querySelector('[role="tablist"]');
    expect(tablist?.getAttribute('aria-label')).toBe('Analytics views');
    expect(tablist?.className).toBe('layout-tabs analytics-tabs');
    const selected = tabs.filter((tab) => tab.getAttribute('aria-selected') === 'true');
    expect(selected.length).toBe(1);
    expect(selected[0].className, 'the selected tab wears the prototype is-active').toBe('is-active');
  });
});
