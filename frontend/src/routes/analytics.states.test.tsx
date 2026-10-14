/**
 * @vitest-environment happy-dom
 *
 * Analytics' shared states on the rendered route (2026-09-21 audit states-09,
 * states-v2, delivery-05; W5e w5-analytics item 5): the hero's FetchedAt for
 * the active data tab (Executive: the OLDER of its two reads), one Refresh =
 * one manualRetry per read, the stale marker over data whose refresh failed,
 * and the EmptyState only for a settled, measured zero. The tab views are
 * stubbed with SYNCHRONOUS factories; the reads are controlled per scope.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { installLocalStorage } from '../test/installLocalStorage';

interface ReadState {
  data: unknown;
  error: Error | null;
  isFetching: boolean;
  isPlaceholderData: boolean;
  dataUpdatedAt: number | null;
}

const reads = vi.hoisted(() => ({
  state: new Map<string, ReadState>(),
  retries: new Map<string, number>(),
}));

vi.mock('../lib/useWarmingUpRetry', () => ({
  useWarmingUpRetry: (_fetcher: unknown, opts: { queryKey: readonly unknown[]; enabled?: boolean }) => {
    const scope = String(opts.queryKey[2]);
    const state = opts.enabled === false ? undefined : reads.state.get(scope);
    return {
      data: state?.data ?? null,
      warmingUp: null,
      error: state?.error ?? null,
      isFetching: state?.isFetching ?? false,
      isPlaceholderData: state?.isPlaceholderData ?? false,
      dataUpdatedAt: state?.dataUpdatedAt ?? null,
      errorUpdatedAt: null,
      manualRetry: () => reads.retries.set(scope, (reads.retries.get(scope) ?? 0) + 1),
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

vi.mock('./analytics.executive', () => ({
  ExecutiveView: () => <div data-testid="executive-view" />,
}));

vi.mock('./analytics.sections', () => ({
  GeographyView: () => <div data-testid="geography-view" />,
  EconomicsView: () => <div data-testid="economics-view" />,
  SegmentsView: () => <div data-testid="segments-view" />,
  SignalsView: () => <div data-testid="signals-view" />,
  preloadEquityScatter: () => Promise.resolve(),
}));

vi.mock('./analytics.approval-funnel', () => ({
  ApprovalFunnelSection: () => <div data-testid="approval-funnel-view" />,
}));

vi.mock('./analytics.sales-ops', () => ({
  SalesOpsSection: () => <div data-testid="sales-ops-view" />,
}));

import AnalyticsRoute from './analytics';
import { preloadAsyncFailure } from '../components/ui/AsyncState';

const HOUR = 3_600_000;
const NOW = Date.now();

const EXECUTIVE = { totals: { addressable_borrowers: 89_553, snapshot_date: '2026-10-13' } };
const ZERO_EXECUTIVE = { totals: { addressable_borrowers: 0, snapshot_date: '2026-10-13' } };
const EMPTY_GEOGRAPHY = { state_opportunities: [], state_avm_values: [], top_zips: [] };

function settled(data: unknown, at: number): ReadState {
  return { data, error: null, isFetching: false, isPlaceholderData: false, dataUpdatedAt: at };
}

async function settle(): Promise<void> {
  for (let i = 0; i < 6; i += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

const heroTime = (): string | null =>
  document.querySelector('.proto-hero__actions [data-testid="fetched-at"] time')?.getAttribute('datetime') ?? null;

describe('Analytics tab states', () => {
  let root: Root;
  let queryClient: QueryClient;

  beforeAll(async () => {
    await preloadAsyncFailure();
    await import('../components/ui/StaleDataNote');
  });

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
    await settle();
  };

  beforeEach(() => {
    installLocalStorage();
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    reads.state.clear();
    reads.retries.clear();
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    document.body.innerHTML = '';
  });

  it('Executive: the hero shows the OLDER of the executive and rate-window fetches, before Ask Genie', async () => {
    reads.state.set('executive', settled(EXECUTIVE, NOW - HOUR));
    reads.state.set('rate-window', settled({ weeks: [] }, NOW - 3 * HOUR));
    await render('/analytics');
    expect(heroTime()).toBe(new Date(NOW - 3 * HOUR).toISOString());
    const actions = [...document.querySelector('.proto-hero__actions')!.children];
    expect(actions[0].getAttribute('data-testid'), 'FetchedAt comes first').toBe('fetched-at');
    expect(actions[1].textContent).toContain('Ask Genie');
  });

  it('Executive Refresh re-reads both reads exactly once', async () => {
    reads.state.set('executive', settled(EXECUTIVE, NOW - HOUR));
    reads.state.set('rate-window', settled({ weeks: [] }, NOW - HOUR));
    await render('/analytics');
    const refresh = document.querySelector<HTMLButtonElement>('[data-testid="fetched-at"] button')!;
    expect(refresh.getAttribute('aria-label')).toBe('Refresh executive analytics');
    act(() => refresh.click());
    expect(Object.fromEntries(reads.retries)).toEqual({ executive: 1, 'rate-window': 1 });
  });

  it('Geography: its own read is the age, and Refresh re-reads only it', async () => {
    reads.state.set('geography', settled({ state_opportunities: [{}], state_avm_values: [], top_zips: [] }, NOW - 2 * HOUR));
    await render('/analytics?view=geography');
    expect(heroTime()).toBe(new Date(NOW - 2 * HOUR).toISOString());
    act(() => document.querySelector<HTMLButtonElement>('[data-testid="fetched-at"] button')!.click());
    expect(Object.fromEntries(reads.retries)).toEqual({ geography: 1 });
  });

  it('keeps the Refreshing… slot during a first load, so the hero keeps its row', async () => {
    reads.state.set('segments', { data: null, error: null, isFetching: true, isPlaceholderData: false, dataUpdatedAt: null });
    await render('/analytics?view=segments');
    const fetched = document.querySelector('[data-testid="fetched-at"]');
    expect(fetched?.textContent).toContain('Refreshing');
  });

  for (const [view, testid] of [['approval-funnel', 'approval-funnel-view'], ['sales-ops', 'sales-ops-view']] as const) {
    it(`${view}: shows no hero FetchedAt (the section states its own as-of)`, async () => {
      await render(`/analytics?view=${view}`);
      expect(document.querySelector(`[data-testid="${testid}"]`)).not.toBeNull();
      expect(document.querySelector('[data-testid="fetched-at"]')).toBeNull();
    });
  }

  it('a failed refresh over data keeps the data and shows the stale note with its age', async () => {
    reads.state.set('executive', { ...settled(EXECUTIVE, NOW - HOUR), error: new Error('503') });
    reads.state.set('rate-window', settled({ weeks: [] }, NOW - 5 * 60_000));
    await render('/analytics');
    expect(document.querySelector('[data-testid="executive-view"]')).not.toBeNull();
    const note = document.querySelector('[data-testid="stale-data-note"]');
    expect(note).not.toBeNull();
    expect(note?.querySelector('time')?.getAttribute('datetime')).toBe(new Date(NOW - HOUR).toISOString());
    expect(document.body.textContent).not.toContain('503');
  });

  it('an aborted read is not stale', async () => {
    const aborted = new DOMException('The operation was aborted.', 'AbortError');
    reads.state.set('geography', { ...settled({ state_opportunities: [{}], state_avm_values: [], top_zips: [] }, NOW - HOUR), error: aborted });
    await render('/analytics?view=geography');
    expect(document.querySelector('[data-testid="stale-data-note"]')).toBeNull();
  });

  it('a settled zero book shows the day-zero empty state; with filters it offers Clear filters', async () => {
    reads.state.set('executive', settled(ZERO_EXECUTIVE, NOW));
    await render('/analytics');
    expect(document.querySelector('[data-empty-cause="day-zero"]')).not.toBeNull();
    expect(document.querySelector('[data-testid="executive-view"]')).toBeNull();
    act(() => root.unmount());
    root = createRoot(document.getElementById('root') as HTMLElement);
    reads.state.set('geography', settled(EMPTY_GEOGRAPHY, NOW));
    await render('/analytics?view=geography&states=IL');
    const empty = document.querySelector('[data-empty-cause="filtered"]');
    expect(empty?.textContent).toContain('No borrowers match these filters.');
    expect([...(empty?.querySelectorAll('button') ?? [])].map((button) => button.textContent)).toEqual(['Clear filters']);
  });

  it('never shows the empty state over placeholder rows, a failure or a first load', async () => {
    reads.state.set('geography', { ...settled(EMPTY_GEOGRAPHY, NOW), isPlaceholderData: true });
    await render('/analytics?view=geography');
    expect(document.querySelector('[data-empty-cause]')).toBeNull();
    act(() => root.unmount());
    root = createRoot(document.getElementById('root') as HTMLElement);
    reads.state.set('geography', { ...settled(EMPTY_GEOGRAPHY, NOW), error: new Error('boom') });
    await render('/analytics?view=geography');
    expect(document.querySelector('[data-empty-cause]')).toBeNull();
    act(() => root.unmount());
    root = createRoot(document.getElementById('root') as HTMLElement);
    reads.state.set('geography', { data: null, error: null, isFetching: true, isPlaceholderData: false, dataUpdatedAt: null });
    await render('/analytics?view=geography');
    expect(document.querySelector('[data-empty-cause]')).toBeNull();
    expect(document.querySelector('[data-analytics-skeleton]')).not.toBeNull();
  });

  it('a first-load failure reads the shared vocabulary, never the transport message', async () => {
    reads.state.set('signals', { data: null, error: new Error('Failed to fetch'), isFetching: false, isPlaceholderData: false, dataUpdatedAt: null });
    await render('/analytics?view=signals');
    expect(document.body.textContent).not.toContain('Failed to fetch');
    expect(document.querySelector('[data-async-status]')).not.toBeNull();
  });

  it('a restored snapshot shows its own age while the fresh read runs', async () => {
    const restoredAt = performance.timeOrigin - 2 * HOUR;
    reads.state.set('executive', { ...settled(EXECUTIVE, restoredAt), isFetching: true });
    reads.state.set('rate-window', { ...settled({ weeks: [] }, restoredAt), isFetching: true });
    await render('/analytics');
    expect(heroTime()).toBe(new Date(restoredAt).toISOString());
    expect(document.querySelector('[data-testid="fetched-at"]')?.textContent).toContain('Refreshing');
    expect(document.querySelector('[data-testid="executive-view"]')).not.toBeNull();
  });
});
