/**
 * @vitest-environment happy-dom
 *
 * Administration -> Field performance (D-platform-process-d2 step 12): the
 * '<20 samples' floor, the rating chips, the RUM-off empty state that reads
 * nothing, a 503 in the describeApiError vocabulary with Retry, the 7/28
 * toggle, an explicit Refresh as the only re-read, and no refetch on focus.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { preloadAsyncFailure } from '../components/ui/AsyncState';
import type { FieldPerformanceResponse } from '../lib/apiClients/fieldPerformance';
import { ApiError } from '../lib/apiTransport';
import { mount } from '../test/render';

const mocks = vi.hoisted(() => ({
  fetchFieldPerformance: vi.fn(),
  config: { data: undefined as { rum_enabled: boolean } | undefined, isError: false },
}));

vi.mock('../lib/apiClients/fieldPerformance', () => ({
  fetchFieldPerformance: mocks.fetchFieldPerformance,
}));
vi.mock('../lib/configOptionsQuery', () => ({
  useConfigOptionsQuery: () => mocks.config,
}));

const { default: FieldPerformancePanel } = await import('./admin-config.field-performance');

// The lazy failure half and the panel's graph transform once, up front: under
// full-suite load the first transform alone can pass the 5 s default.
vi.setConfig({ testTimeout: 30_000 });
beforeAll(async () => {
  await preloadAsyncFailure();
}, 60_000);

const cell = (p75: number | null, rating: 'good' | 'needs_improvement' | 'poor' | null, samples: number) => ({
  p75,
  rating,
  samples,
  floor_applied: p75 === null,
});

const RESPONSE: FieldPerformanceResponse = {
  days: 7,
  since: '2026-09-30',
  browser_telemetry: 'on',
  builds: ['abc123def456'],
  vitals: [
    { route: '/lead-queue', lcp: cell(1840, 'good', 412), inp: cell(320, 'needs_improvement', 380), cls: cell(0.31, 'poor', 401) },
    { route: '/glossary', lcp: cell(null, null, 7), inp: cell(null, null, 3), cls: cell(null, null, 0) },
  ],
  interactions: [
    {
      route: '/lead-queue', interaction_target: 'lead-row', samples: 210,
      inp: cell(240, 'needs_improvement', 210), input_delay: cell(14, 'good', 210),
      processing: cell(160, 'good', 210), presentation: cell(66, 'good', 210),
    },
  ],
  client_errors: [
    { route: '/borrower-360/:id', error_name: 'TypeError', error_kind: 'render', boundary: 'drawer', count: 3 },
  ],
};

function client(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } });
}

async function render(queryClient = client()) {
  const mounted = await mount(
    <QueryClientProvider client={queryClient}>
      <FieldPerformancePanel />
    </QueryClientProvider>,
  );
  return { ...mounted, queryClient };
}

async function settle(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

function buttonNamed(container: HTMLElement, name: RegExp): HTMLButtonElement {
  const button = [...container.querySelectorAll('button')].find(
    (candidate) => name.test(candidate.getAttribute('aria-label') ?? '') || name.test(candidate.textContent ?? ''),
  );
  if (!button) throw new Error(`no button ${name}`);
  return button;
}

beforeEach(() => {
  mocks.fetchFieldPerformance.mockReset();
  mocks.fetchFieldPerformance.mockResolvedValue(RESPONSE);
  mocks.config.data = { rum_enabled: true };
  mocks.config.isError = false;
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('FieldPerformancePanel', () => {
  it('shows a CWV chip per rating and the floor cell, with RUM on in the header', async () => {
    const { container } = await render();
    await settle();

    expect(mocks.fetchFieldPerformance).toHaveBeenCalledTimes(1);
    expect(mocks.fetchFieldPerformance.mock.calls[0][0]).toBe(7);
    expect(container.querySelector('.surface__hdr .chip')?.textContent).toBe('Browser telemetry On');
    const leadQueue = [...container.querySelectorAll('tbody tr')].find((row) => row.textContent?.startsWith('/lead-queue'));
    const chips = [...(leadQueue?.querySelectorAll('.chip') ?? [])];
    expect(chips.map((chip) => [chip.textContent, chip.className])).toEqual([
      ['1.84 s', 'chip chip--success'],
      ['320 ms', 'chip chip--warning'],
      ['0.310', 'chip chip--danger'],
    ]);
    const glossary = [...container.querySelectorAll('tbody tr')].find((row) => row.textContent?.startsWith('/glossary'));
    expect([...(glossary?.querySelectorAll('.field-perf__floor') ?? [])].map((floor) => floor.textContent)).toEqual([
      '<20 samples',
      '<20 samples',
      '<20 samples',
    ]);
    expect(glossary?.querySelector('.chip')).toBeNull();
    expect(container.textContent).toContain('INP by interaction');
    expect(container.textContent).toContain('lead-row');
    expect(container.textContent).toContain('Client errors');
    expect(container.textContent).toContain('TypeError');
  });

  it('says telemetry is off, offers how to turn it on, and reads nothing', async () => {
    mocks.config.data = { rum_enabled: false };
    const { container } = await render();
    await settle();

    expect(mocks.fetchFieldPerformance).not.toHaveBeenCalled();
    expect(container.querySelector('.surface__hdr .chip')?.textContent).toBe('Browser telemetry Off');
    const empty = container.querySelector('[data-empty-cause]');
    expect(empty?.textContent).toContain('Browser telemetry is off for this deployment.');
    expect(empty?.textContent).toContain('MIP_RUM_ENABLED=0');
    expect(container.querySelector('.segmented')).toBeNull();
  });

  it('answers a 503 with the describeApiError copy and a Retry that reads once more', async () => {
    mocks.fetchFieldPerformance.mockRejectedValue(
      new ApiError('lakebase is temporarily unavailable', { path: '/api/admin/field-performance', status: 503, dependency: 'lakebase' }),
    );
    const { container } = await render();
    await settle();

    const alert = container.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain("Couldn't load field performance");
    expect(alert?.textContent).not.toContain('lakebase is temporarily unavailable');
    mocks.fetchFieldPerformance.mockResolvedValue(RESPONSE);
    await act(async () => buttonNamed(container, /^Retry loading field performance$/).click());
    await settle();

    expect(mocks.fetchFieldPerformance).toHaveBeenCalledTimes(2);
    expect(container.querySelector('[role="alert"]')).toBeNull();
    expect(container.querySelectorAll('tbody tr').length).toBeGreaterThan(0);
  });

  it('switches to 28 days on the toggle and re-reads only on Refresh, never on focus', async () => {
    const { container } = await render();
    await settle();
    const sevenDays = buttonNamed(container, /^7 days$/);
    expect(sevenDays.getAttribute('aria-pressed')).toBe('true');

    await act(async () => buttonNamed(container, /^28 days$/).click());
    await settle();
    expect(mocks.fetchFieldPerformance.mock.calls.map((call) => call[0])).toEqual([7, 28]);
    expect(buttonNamed(container, /^28 days$/).getAttribute('aria-pressed')).toBe('true');

    // TanStack's focus manager listens for visibilitychange on window; a
    // hidden-then-visible round trip is a window refocus.
    await act(async () => {
      Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
      window.dispatchEvent(new Event('visibilitychange'));
      Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
      window.dispatchEvent(new Event('visibilitychange'));
      window.dispatchEvent(new Event('focus'));
    });
    await settle();
    expect(mocks.fetchFieldPerformance).toHaveBeenCalledTimes(2);

    await act(async () => buttonNamed(container, /^Refresh field performance$/).click());
    await settle();
    expect(mocks.fetchFieldPerformance.mock.calls.map((call) => call[0])).toEqual([7, 28, 28]);
  });

  it('says so when the window holds no measurement yet', async () => {
    mocks.fetchFieldPerformance.mockResolvedValue({ ...RESPONSE, vitals: [], interactions: [], client_errors: [] });
    const { container } = await render();
    await settle();
    expect(container.querySelector('[data-empty-cause]')?.textContent).toContain('No field measurements in this window yet.');
  });
});
