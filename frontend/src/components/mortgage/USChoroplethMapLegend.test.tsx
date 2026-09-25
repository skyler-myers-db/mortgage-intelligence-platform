/**
 * @vitest-environment happy-dom
 */
/**
 * The legend's break row (audit dataviz-02 follow-up): a break that prints
 * like the one before it is printed once, while every break keeps its exact
 * value for the tooltip and for tests that read the classes back.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RateSensitivityResponse } from '../../types/rateScenario';
import RateScenarioControl from './RateScenarioControl';
import { buildChoroplethScale } from './USChoroplethMap.scale';
import { USChoroplethMapLegend, type LegendRate } from './USChoroplethMapLegend';
import { indexRateScenario, scenarioView } from './rateScenario.logic';
import type { GeoRead } from './useChoroplethLiveFacts';

vi.mock('../AppContext', () => ({
  useApp: () => ({ setDrawer: () => undefined, showEvidence: true, showConfidence: true }),
}));

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('USChoroplethMapLegend break row', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
  });

  function renderLegend(counts: number[]) {
    const scale = buildChoroplethScale(counts);
    act(() => {
      root.render(
        <USChoroplethMapLegend
          overlayOn={false}
          overlayData={null}
          overlayLoading={false}
          overlayError={null}
          totalCount={counts.reduce((sum, count) => sum + count, 0)}
          scale={scale}
          segmentCaption="marketable population"
        />,
      );
    });
    return [...document.querySelectorAll('.map-legend__break')].map((el) => ({
      text: el.textContent,
      value: Number(el.getAttribute('data-break')),
      title: el.getAttribute('title'),
    }));
  }

  it('prints a tied break once and keeps its exact value', () => {
    // max 3 under the sqrt scale: breaks 1, 1, 2 (class 2 is empty).
    const breaks = renderLegend([3, 2, 1]);
    expect(breaks.map((b) => b.value)).toEqual([1, 1, 2]);
    expect(breaks.map((b) => b.text)).toEqual(['1', '', '2']);
    expect(breaks.map((b) => b.title)).toEqual(['1', '1', '2']);
  });

  it('prints distinct breaks as compact values with the exact value in the title', () => {
    const breaks = renderLegend([21480, 17920, 14650, 11230, 9040, 6710, 4980, 3543]);
    expect(breaks.map((b) => b.text)).toEqual(['1.3K', '5.4K', '12.1K']);
    expect(breaks.map((b) => b.title)).toEqual(['1,343', '5,370', '12,083']);
  });
});

const GRID: RateSensitivityResponse = {
  built: true,
  steps_bps: [-100, -75, -50, -25, 0, 25, 50, 75, 100],
  scenario_market_rate_pct: [5.3, 5.55, 5.8, 6.05, 6.3, 6.55, 6.8, 7.05, 7.3],
  base_market_rate_pct: 6.3,
  thresholds: { min_spread_bps: 75, min_equity_pct: 15 },
  states: [
    { state: 'IL', addressable: 5_000, rate_movable: 4_000, in_the_money: [2_000, 1_800, 1_600, 1_400, 1_200, 1_000, 800, 600, 400] },
    { state: 'TX', addressable: 3_000, rate_movable: 2_000, in_the_money: [900, 800, 700, 600, 500, 400, 300, 200, 100] },
  ],
  provenance: { gold_source: 'g', book_source: 'b', rule_source: 'r', contactable_source: 'c', note: 'n' },
};

describe('USChoroplethMapLegend in rate mode', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
  });

  function read(overrides: Partial<GeoRead<RateSensitivityResponse>>): GeoRead<RateSensitivityResponse> {
    return { data: null, warmingUp: null, error: null, loading: false, updating: false, retry: () => undefined, ...overrides };
  }

  async function renderRate(rate: LegendRate) {
    await act(async () => {
      root.render(
        <USChoroplethMapLegend
          overlayOn={false}
          overlayData={null}
          overlayLoading={false}
          overlayError={null}
          totalCount={8_000}
          scale={buildChoroplethScale([5_000, 3_000])}
          segmentCaption="marketable population"
          rate={rate}
        />,
      );
    });
    return {
      label: [...document.querySelectorAll('.map-legend__lever-note .chip')].map((chip) => chip.textContent),
      total: document.querySelector('.map-legend__value')?.textContent,
      slider: document.querySelector('input[type="range"]'),
      lever: document.querySelector('.map-legend__lever')?.textContent ?? '',
      header: document.querySelector('.map-legend__header')?.textContent ?? '',
    };
  }

  const index = indexRateScenario(GRID);
  // The map hands the legend the control once its lazy chunk has loaded.
  const base = {
    index: null,
    view: null,
    step: 0,
    onStepChange: () => undefined,
    scope: null,
    control: RateScenarioControl,
    controlFailed: false,
  };

  it('shows the scenario label and no number while the read is loading', async () => {
    const out = await renderRate({ ...base, read: read({ loading: true }) });
    expect(out.label).toEqual(['Scenario, not a forecast']);
    expect(out.header).toContain('In the money at scenario');
    expect(out.total).toBe('—');
    expect(out.slider).toBeNull();
  });

  it('shows the label, why, and no number while warming, failed or not built', async () => {
    const warming = { dependency: 'warehouse', label: 'Warehouse warming up', attempt: 1, maxAttempts: 6, correlationId: null };
    for (const [rateRead, why] of [
      [read({ warmingUp: warming }), 'Rate scenarios: Warehouse warming up.'],
      [read({ error: new Error('boom') }), 'Rate scenarios could not load.'],
      [read({ data: { ...GRID, built: false, states: [], steps_bps: [], scenario_market_rate_pct: [] } }), 'Rate scenarios are not built yet'],
    ] as const) {
      const out = await renderRate({ ...base, read: rateRead });
      expect(out.label).toEqual(['Scenario, not a forecast']);
      expect(out.lever).toContain(why);
      expect(out.lever).toContain("The Lead Queue and campaigns use today's par rate.");
      expect(out.total).toBe('—');
      expect(out.slider).toBeNull();
      expect(out.lever.replace(/attempt \d of \d/, '')).not.toMatch(/\d/);
    }
  });

  it('says a control chunk that failed to load could not load, with Reload, and draws no number', async () => {
    const out = await renderRate({ ...base, read: read({ data: GRID }), control: null, controlFailed: true });
    expect(out.label).toEqual(['Scenario, not a forecast']);
    expect(out.lever).toContain('Rate scenarios could not load. Showing borrower counts.');
    expect(out.total).toBe('—');
    expect(out.slider).toBeNull();
    const reload = [...document.querySelectorAll('.map-legend__lever button')].map((button) => button.textContent);
    expect(reload).toEqual(['Reload']);
    expect(out.lever).not.toMatch(/\d/);
  });

  it('recounts the book at the shown step once the grid is up, with the label', async () => {
    if (!index) throw new Error('index');
    const out = await renderRate({ ...base, read: read({ data: GRID }), index, view: scenarioView(index, -100) });
    expect(out.label).toEqual(['Scenario, not a forecast']);
    expect(out.total).toBe('2,900');
    expect(out.slider).not.toBeNull();
    expect(document.querySelector('.map-legend__caption')?.textContent).toContain(
      'borrowers in the money at the scenario par rate',
    );
  });

  it('recounts the drilled state and says ZIP tiles keep borrowers', async () => {
    if (!index) throw new Error('index');
    const out = await renderRate({
      ...base,
      read: read({ data: GRID }),
      index,
      view: scenarioView(index, 0),
      scope: { id: 'tx', name: 'Texas' },
    });
    expect(out.total).toBe('500');
    expect(document.querySelector('.map-legend__caption')?.textContent).toContain(
      'ZIP tiles show borrowers; scenarios are computed per state.',
    );
  });
});
