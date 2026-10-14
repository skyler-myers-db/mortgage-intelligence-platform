/**
 * @vitest-environment happy-dom
 *
 * Per-tab chunks on Analytics (2026-09-21 audit bundle-06, W5e item 2b),
 * driven on the rendered route. Executive is the default tab and ships in the
 * route chunk; Geography, Economics, Segments and Signals load
 * analytics.sections, the Economics scatter loads analytics.equity-scatter,
 * and Approval funnel and Sales ops load their own modules. The lazy modules
 * are mocked with SYNCHRONOUS factories that record when they are imported.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { installLocalStorage } from '../test/installLocalStorage';

const state = vi.hoisted(() => ({
  imported: [] as string[],
  reads: [] as string[],
  data: new Map<string, unknown>(),
}));

vi.mock('../lib/useWarmingUpRetry', () => ({
  useWarmingUpRetry: (_fetcher: unknown, opts: { queryKey: readonly unknown[]; enabled?: boolean }) => {
    const scope = String(opts.queryKey[2]);
    if (opts.enabled !== false) state.reads.push(scope);
    return {
      data: opts.enabled === false ? null : (state.data.get(scope) ?? null),
      warmingUp: null,
      error: null,
      isFetching: false,
      isPlaceholderData: false,
      dataUpdatedAt: null,
      errorUpdatedAt: null,
      manualRetry: vi.fn(),
    };
  },
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

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: {},
}));

vi.mock('./analytics.sections', () => {
  state.imported.push('sections');
  return {
    GeographyView: () => <div data-testid="geography-view" />,
    EconomicsView: () => <div data-testid="economics-view" />,
    SegmentsView: () => <div data-testid="segments-view" />,
    SignalsView: () => <div data-testid="signals-view" />,
    preloadEquityScatter: () => {
      state.imported.push('equity-scatter');
      return Promise.resolve();
    },
  };
});

vi.mock('./analytics.approval-funnel', () => {
  state.imported.push('approval-funnel');
  return { ApprovalFunnelSection: () => <div data-testid="approval-funnel-view" /> };
});

vi.mock('./analytics.sales-ops', () => {
  state.imported.push('sales-ops');
  return { SalesOpsSection: () => <div data-testid="sales-ops-view" /> };
});

import AnalyticsRoute from './analytics';
import { warmChunk } from './analytics.lazyView';

const tabButton = (label: string): HTMLButtonElement => {
  const found = [...document.querySelectorAll<HTMLButtonElement>('button[role="tab"]')]
    .find((button) => button.textContent?.trim() === label);
  if (!found) throw new Error(`no ${label} tab`);
  return found;
};

async function settle(): Promise<void> {
  for (let i = 0; i < 5; i += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

describe('Analytics per-tab chunks', () => {
  let root: Root;
  let queryClient: QueryClient;

  const render = async (path: string) => {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={[path]}>
            <AnalyticsRoute />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  };

  beforeEach(() => {
    installLocalStorage();
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    state.reads.length = 0;
    state.data.clear();
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    document.body.innerHTML = '';
  });

  // Runs first: the lazy modules have not been imported by any earlier case.
  it('loads no other tab module for the Executive default', async () => {
    await render('/analytics');
    await settle();
    expect(state.imported).toEqual([]);
    expect(document.querySelector('[data-testid="geography-view"]')).toBeNull();
  });

  it('a tab switch shows that tab’s skeleton at once, then its view once the chunk lands', async () => {
    // A non-empty read: an all-empty one renders the tab's empty state instead.
    state.data.set('geography', { state_opportunities: [{ state: 'IL' }], state_avm_values: [], top_zips: [] });
    await render('/analytics');
    act(() => tabButton('Geography').click());
    const skeleton = document.querySelector('[data-analytics-skeleton]');
    expect(skeleton, 'the keyed Suspense boundary shows the tab skeleton at once').not.toBeNull();
    expect(skeleton?.querySelector('h2')?.textContent).toBe('Geography analytics');
    await settle();
    expect(document.querySelector('[data-testid="geography-view"]')).not.toBeNull();
    expect(document.querySelector('[data-analytics-skeleton]')).toBeNull();
    expect(state.imported).toContain('sections');
  });

  it('opening Economics loads the sections and scatter chunks with its read', async () => {
    state.data.set('economics', { rate_spread_histogram: [] });
    await render('/analytics?view=economics');
    await settle();
    expect(state.imported).toEqual(expect.arrayContaining(['sections', 'equity-scatter']));
    expect(document.querySelector('[data-testid="economics-view"]')).not.toBeNull();
  });

  it('focusing or hovering a tab warms its chunk and starts no read', async () => {
    await render('/analytics');
    const readsBefore = [...state.reads];
    await act(async () => {
      tabButton('Approval funnel').focus();
      tabButton('Sales ops').dispatchEvent(new PointerEvent('pointerover', { bubbles: true }));
    });
    await settle();
    expect(state.imported).toEqual(expect.arrayContaining(['approval-funnel', 'sales-ops']));
    // Warming is chunks only: no tab read was added by the focus or the hover.
    expect(state.reads.filter((scope) => !readsBefore.includes(scope))).toEqual([]);
    expect(document.querySelector('[data-testid="approval-funnel-view"]')).toBeNull();
  });
});

describe('warmChunk', () => {
  afterEach(() => {
    Reflect.deleteProperty(navigator, 'connection');
  });

  it('starts the chunk on intent, and nothing while the browser asks to save data', () => {
    const preload = vi.fn(() => Promise.resolve());
    warmChunk(preload);
    expect(preload).toHaveBeenCalledTimes(1);
    Object.defineProperty(navigator, 'connection', { configurable: true, value: { saveData: true } });
    warmChunk(preload);
    expect(preload).toHaveBeenCalledTimes(1);
    warmChunk(undefined);
  });
});
