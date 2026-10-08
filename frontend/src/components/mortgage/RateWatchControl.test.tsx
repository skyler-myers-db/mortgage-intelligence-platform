/**
 * @vitest-environment happy-dom
 */
/**
 * "Watch this level" (audit 2026-09-21 `wow-ai-2`) inside the Rate Lever:
 * shown only at a non-zero step on a usable grid; the click posts the step
 * only; pessimistic; the success line shows the SERVER's level, the scheduler
 * state and takes focus; 4xx/5xx copy; a 429 goes to the shell toast with
 * its Retry-After; a new step resets it; nothing is read on render or hover.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, useEffect, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../lib/apiTransport';
import { queryKeys } from '../../lib/queryKeys';
import type { GrowthAgentRateWatchResponse } from '../../types/growthAgent';
import type { RateSensitivityResponse } from '../../types/rateScenario';
import type { GeoRead } from './useChoroplethLiveFacts';
import { indexRateScenario, type RateScenarioIndex } from './rateScenario.logic';
import RateScenarioControl from './RateScenarioControl';
import { WATCH_DESCRIPTION, WATCH_FAILED_MESSAGE, WATCH_REFUSED_MESSAGE } from './RateWatchControl';

const mocks = vi.hoisted(() => ({ saveRateWatch: vi.fn(), toastInfo: vi.fn(), setDrawer: vi.fn() }));
vi.mock('../../lib/apiClients/growthAgentRateWatch', () => ({
  GROWTH_AGENT_RATE_WATCH_PATH: '/api/growth-agent/rate-watch',
  growthAgentRateWatchApi: { saveRateWatch: (...args: unknown[]) => mocks.saveRateWatch(...args) },
}));
vi.mock('../../lib/toast', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/toast')>()),
  toast: { info: (...args: unknown[]) => mocks.toastInfo(...args), error: vi.fn(), success: vi.fn() },
}));
vi.mock('../AppContext', () => ({
  useApp: () => ({ setDrawer: mocks.setDrawer, showEvidence: true, showConfidence: true }),
}));

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const RESPONSE: RateSensitivityResponse = {
  built: true,
  steps_bps: [-100, -75, -50, -25, 0, 25, 50, 75, 100],
  scenario_market_rate_pct: [5.3, 5.55, 5.8, 6.05, 6.3, 6.55, 6.8, 7.05, 7.3],
  base_market_rate_pct: 6.3,
  thresholds: { min_spread_bps: 75, min_equity_pct: 15 },
  states: [
    {
      state: 'IL', addressable: 5_000, rate_movable: 4_000,
      in_the_money: [2_000, 1_800, 1_600, 1_400, 1_200, 1_000, 800, 600, 400],
      contactable_in_the_money: [200, 180, 160, 140, 120, 100, 80, 60, 40],
    },
  ],
  provenance: {
    gold_source: 'mip.gold.rate_sensitivity_rollup', book_source: 'b', rule_source: 'r', contactable_source: 'c',
    book_as_of: '2026-07-14T12:00:00Z', note: 'n',
  },
};
const INDEX = indexRateScenario(RESPONSE) as RateScenarioIndex;

const SAVED: GrowthAgentRateWatchResponse = {
  monitor: {
    monitor_id: '5a1e0000-0000-4000-8000-000000000001',
    workflow_id: 'rate_trigger_watch',
    name: 'Rate watch - 30-year par at or below 5.79 percent',
    cadence: 'daily',
    status: 'active',
    criteria: {},
    route: '/?map_mode=rate&rate_step=-50',
    actionable_total: 0,
    source_assets: [],
    last_run_id: null,
  },
  replayed: false,
  // The server's level, deliberately not the displayed 5.80%: the shown grid may be a day-old copy.
  watch_level_pct: 5.79,
  watch_direction: 'at_or_below',
  latest_print_pct: 6.12,
  latest_print_week: '2026-09-28',
  scheduler: { state: 'paused', reason: 'job_schedule' },
};

function read(overrides: Partial<GeoRead<RateSensitivityResponse>> = {}): GeoRead<RateSensitivityResponse> {
  return { data: RESPONSE, warmingUp: null, error: null, loading: false, updating: false, lastGoodAt: null, retry: vi.fn(), ...overrides };
}

let setStepFromTest: (step: number) => void = () => undefined;

function Harness({ initialStep, index = INDEX }: { initialStep: number; index?: RateScenarioIndex | null }) {
  const [step, setStep] = useState(initialStep);
  useEffect(() => {
    setStepFromTest = setStep;
  }, [setStep]);
  return (
    <RateScenarioControl
      rate={{ read: read(), index, view: null, step, onStepChange: setStep, scope: null, stateName: (id) => id }}
    />
  );
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe('RateWatchControl', () => {
  let root: Root;
  let client: QueryClient;
  let fetchSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    vi.resetAllMocks();
    fetchSpy = vi.spyOn(globalThis, 'fetch');
    client = new QueryClient();
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    fetchSpy.mockRestore();
  });

  function render(initialStep: number, index: RateScenarioIndex | null = INDEX) {
    act(() => root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <Harness initialStep={initialStep} index={index} />
        </MemoryRouter>
      </QueryClientProvider>,
    ));
  }
  const watchButton = () =>
    Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find((b) => /^(Watch |Saving)/.test(b.textContent ?? ''));
  async function click(button: HTMLElement | undefined) {
    await act(async () => {
      button?.click();
      await Promise.resolve();
    });
  }

  it('is offered only at a non-zero step of a usable grid, described, and reads nothing', () => {
    render(0);
    expect(watchButton()).toBeUndefined();
    act(() => setStepFromTest(-50));
    const button = watchButton();
    expect(button?.textContent).toBe('Watch 5.80%');
    expect(button?.classList.contains('btn--ghost')).toBe(true);
    expect(button?.classList.contains('btn--sm')).toBe(true);
    expect(button?.closest('.rate-lever__actions')).not.toBeNull();
    expect(document.getElementById(button?.getAttribute('aria-describedby') ?? '')?.textContent).toBe(WATCH_DESCRIPTION);
    expect(button?.hasAttribute('title')).toBe(false);
    act(() => {
      button?.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
      button?.dispatchEvent(new FocusEvent('focus'));
    });
    expect(mocks.saveRateWatch).not.toHaveBeenCalled();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('is not offered while the grid is not built', () => {
    render(-50, null);
    expect(watchButton()).toBeUndefined();
  });

  it('posts the step, the cadence and an id only, and is pessimistic until the server answers', async () => {
    const pending = deferred<GrowthAgentRateWatchResponse>();
    mocks.saveRateWatch.mockReturnValue(pending.promise);
    render(-50);
    const button = watchButton();
    button?.focus();
    await click(button);
    expect(mocks.saveRateWatch).toHaveBeenCalledTimes(1);
    const [body] = mocks.saveRateWatch.mock.calls[0] as [Record<string, unknown>];
    expect(Object.keys(body).sort()).toEqual(['cadence', 'request_id', 'step_bps']);
    expect(body).toMatchObject({ step_bps: -50, cadence: 'daily' });
    expect(typeof body.request_id).toBe('string');

    const saving = watchButton();
    expect(saving?.textContent).toBe('Saving…');
    expect(saving?.getAttribute('aria-disabled')).toBe('true');
    expect(saving?.disabled).toBe(false);
    expect(document.activeElement).toBe(saving);
    expect(document.querySelector('.rate-lever__watching')).toBeNull();
    await click(saving);
    expect(mocks.saveRateWatch).toHaveBeenCalledTimes(1);

    const invalidate = vi.spyOn(client, 'invalidateQueries');
    await act(async () => {
      pending.resolve(SAVED);
      await pending.promise;
    });
    const status = document.querySelector<HTMLElement>('.rate-lever__watching');
    expect(status?.getAttribute('role')).toBe('status');
    expect(status?.textContent).toBe('Watching 5.79% · scheduled runs off · Latest print 6.12%');
    expect(document.activeElement).toBe(status);
    expect(watchButton()).toBeUndefined();
    const link = Array.from(document.querySelectorAll('a')).find((a) => a.textContent === 'Saved monitors');
    expect(link?.getAttribute('href')).toBe('/ask-genie?tab=monitors');
    expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys.growthAgentWatchlistSummary() });
    expect(queryKeys.growthAgentWatchlistSummary()).toEqual(['mip', 'growth-agent', 'monitors', 'summary']);
  });

  it.each([
    [new ApiError('The Rate Lever grid changed; move the slider again.', { path: '/x', status: 409 }), WATCH_REFUSED_MESSAGE],
    [new ApiError('step_bps must move the par rate', { path: '/x', status: 422 }), WATCH_REFUSED_MESSAGE],
    [new ApiError('Lakebase unavailable', { path: '/x', status: 503 }), WATCH_FAILED_MESSAGE],
    [new Error('boom'), WATCH_FAILED_MESSAGE],
  ])('a failed save shows governed copy, never the server text (%#)', async (error, message) => {
    mocks.saveRateWatch.mockRejectedValue(error);
    render(-50);
    await click(watchButton());
    expect(document.querySelector('[role="alert"]')?.textContent).toBe(message);
    expect(watchButton()?.textContent).toBe('Watch 5.80%');
    expect(mocks.toastInfo).not.toHaveBeenCalled();
  });

  it('a 429 is a shell toast with a Retry behind the Retry-After countdown', async () => {
    mocks.saveRateWatch.mockRejectedValue(
      new ApiError('Too many requests', { path: '/x', status: 429, reason: 'rate_limited', retryAfterMs: 12_000 }),
    );
    render(-50);
    await click(watchButton());
    expect(mocks.toastInfo).toHaveBeenCalledTimes(1);
    const [, options] = mocks.toastInfo.mock.calls[0] as [string, { action: { label: string; onAction: () => void }; retryAfterMs: number }];
    expect(options.action.label).toBe('Retry');
    expect(options.retryAfterMs).toBe(12_000);
    expect(document.querySelector('[role="alert"]')).toBeNull();
    mocks.saveRateWatch.mockResolvedValue(SAVED);
    await act(async () => {
      options.action.onAction();
      await Promise.resolve();
    });
    expect(mocks.saveRateWatch).toHaveBeenCalledTimes(2);
  });

  it('a new step resets the control', async () => {
    mocks.saveRateWatch.mockResolvedValue(SAVED);
    render(-50);
    await click(watchButton());
    expect(document.querySelector('.rate-lever__watching')).not.toBeNull();
    act(() => setStepFromTest(-75));
    expect(document.querySelector('.rate-lever__watching')).toBeNull();
    expect(watchButton()?.textContent).toBe('Watch 5.55%');
    act(() => setStepFromTest(0));
    expect(watchButton()).toBeUndefined();
  });
});
