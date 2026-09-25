/**
 * @vitest-environment happy-dom
 */
/**
 * useChoroplethLiveFacts (audit runtime-06 map slice, wow-stage-1): the read
 * layer under the geography hero.
 *
 *  - the Rate Lever read is disabled outside rate mode (never on load, never
 *    prefetched) and issued once when the mode is on;
 *  - a changed cohort keeps the previous state rollups as a labelled
 *    placeholder (`updating`), and a FINAL error ends it: the error is what
 *    renders, never the previous cohort presented as current;
 *  - ZIP rollups keep the previous payload only for the same drilled state.
 */
import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { StateRollupResponse, ZipRollupResponse } from '../../types';
import { useChoroplethLiveFacts, type UseChoroplethLiveFactsInput } from './useChoroplethLiveFacts';

const mocks = vi.hoisted(() => ({
  stateRollups: vi.fn(),
  zipRollups: vi.fn(),
  assignmentOverlay: vi.fn(),
  rateSensitivity: vi.fn(),
}));

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: { stateRollups: mocks.stateRollups, zipRollups: mocks.zipRollups, assignmentOverlay: mocks.assignmentOverlay },
}));
vi.mock('../../lib/apiClients/rateScenario', () => ({ rateScenarioApi: { rateSensitivity: mocks.rateSensitivity } }));
vi.mock('./USStateMapData', () => ({ loadUsaStateMap: () => new Promise(() => undefined) }));

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function states(addressable: number): StateRollupResponse {
  return {
    rollups: [{ state: 'IL', addressable, in_the_money: 1, top_tier_opportunities: 1, avg_score: 70 }],
    snapshot_date: '2026-07-14',
  };
}

function zips(state: string, zip: string): ZipRollupResponse {
  return {
    state,
    fips_5: null,
    snapshot_date: '2026-07-14',
    rollups: [{ zip, state, county_fips_5: null, addressable_borrowers: 10, avg_opportunity_score: 70, top_segment_code: 'itm' }],
  };
}

type Facts = ReturnType<typeof useChoroplethLiveFacts>;
const seen: { facts: Facts | null } = { facts: null };

/** Publishes the hook's latest result after each commit. */
function Probe(props: UseChoroplethLiveFactsInput) {
  const facts = useChoroplethLiveFacts(props);
  useEffect(() => {
    seen.facts = facts;
  });
  return null;
}

const INPUT: UseChoroplethLiveFactsInput = {
  drillState: null,
  segmentFilterMode: 'any',
  overlayOn: false,
  rateOn: false,
};

describe('useChoroplethLiveFacts', () => {
  let root: Root;
  let client: QueryClient;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    client = new QueryClient({ defaultOptions: { queries: { gcTime: Infinity } } });
    seen.facts = null;
  });

  afterEach(() => {
    act(() => root.unmount());
    client.clear();
    vi.clearAllMocks();
  });

  async function render(input: UseChoroplethLiveFactsInput) {
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <Probe {...input} />
        </QueryClientProvider>,
      );
    });
  }

  async function until(check: () => boolean) {
    for (let i = 0; i < 100 && !check(); i += 1) {
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 2));
      });
    }
    expect(check()).toBe(true);
  }

  it('reads the rate grid only while the rate colouring is on', async () => {
    mocks.stateRollups.mockResolvedValue(states(100));
    mocks.rateSensitivity.mockResolvedValue({ built: false, steps_bps: [], scenario_market_rate_pct: [], thresholds: {}, states: [], provenance: {} });
    await render(INPUT);
    await until(() => seen.facts?.states.data?.il !== undefined);
    await render({ ...INPUT, overlayOn: true });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
    expect(mocks.rateSensitivity).not.toHaveBeenCalled();
    expect(seen.facts?.rate.data).toBeNull();
    expect(seen.facts?.rate.loading).toBe(false);

    await render({ ...INPUT, rateOn: true });
    await until(() => seen.facts?.rate.data !== null);
    expect(mocks.rateSensitivity).toHaveBeenCalledTimes(1);
  });

  it('keeps the previous cohort as a labelled placeholder, and a final error replaces it with the error', async () => {
    mocks.stateRollups.mockResolvedValueOnce(states(100));
    await render(INPUT);
    await until(() => seen.facts?.states.data?.il?.addressable === 100);
    expect(seen.facts?.states.updating).toBe(false);

    const next = deferred<StateRollupResponse>();
    mocks.stateRollups.mockReturnValueOnce(next.promise);
    await render({ ...INPUT, segmentFilter: ['itm'] });
    await until(() => mocks.stateRollups.mock.calls.length === 2);
    // The previous cohort stays up, labelled, not loading.
    expect(seen.facts?.states.data?.il?.addressable).toBe(100);
    expect(seen.facts?.states.updating).toBe(true);
    expect(seen.facts?.states.loading).toBe(false);

    await act(async () => {
      next.reject(new Error('rollup failed'));
    });
    await until(() => seen.facts?.states.error !== null);
    expect(seen.facts?.states.data).toBeNull();
    expect(seen.facts?.states.updating).toBe(false);
    expect(seen.facts?.states.error?.message).toBe('rollup failed');
  });

  it('keeps ZIP tiles for the same drilled state only, never under another state', async () => {
    mocks.stateRollups.mockResolvedValue(states(100));
    mocks.zipRollups.mockImplementation(({ state }: { state: string }) =>
      Promise.resolve(zips(state, state === 'IL' ? '60611' : '77002')),
    );
    await render({ ...INPUT, drillState: 'IL' });
    await until(() => seen.facts?.zips.data?.['60611'] !== undefined);

    // Same state, new cohort: IL's tiles stay up as the placeholder.
    const sameState = deferred<ZipRollupResponse>();
    mocks.zipRollups.mockReturnValueOnce(sameState.promise);
    await render({ ...INPUT, drillState: 'IL', segmentFilter: ['itm'] });
    await until(() => seen.facts?.zips.updating === true);
    expect(Object.keys(seen.facts?.zips.data ?? {})).toEqual(['60611']);
    await act(async () => sameState.resolve(zips('IL', '60612')));
    await until(() => seen.facts?.zips.data?.['60612'] !== undefined);

    // Another state: nothing of Illinois is painted while Texas loads.
    const texas = deferred<ZipRollupResponse>();
    mocks.zipRollups.mockReturnValueOnce(texas.promise);
    await render({ ...INPUT, drillState: 'TX', segmentFilter: ['itm'] });
    await until(() => seen.facts?.zips.loading === true);
    expect(seen.facts?.zips.data).toBeNull();
    expect(seen.facts?.zips.updating).toBe(false);
    await act(async () => texas.resolve(zips('TX', '77002')));
    await until(() => seen.facts?.zips.data?.['77002'] !== undefined);
  });
});
