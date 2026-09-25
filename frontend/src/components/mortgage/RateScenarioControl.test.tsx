/**
 * @vitest-environment happy-dom
 */
/**
 * RateScenarioControl (audit wow-stage-1): the lazy scrubber and its status
 * lines. Native keyboard steps are proven in the fixture spec (a real
 * browser); here the input's own change events drive it.
 */
import { act, useDeferredValue, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RateSensitivityResponse } from '../../types/rateScenario';
import type { GeoRead } from './useChoroplethLiveFacts';
import { indexRateScenario, scenarioView, type RateScenarioIndex } from './rateScenario.logic';
import RateScenarioControl from './RateScenarioControl';

const appMocks = vi.hoisted(() => ({ setDrawer: vi.fn() }));
vi.mock('../AppContext', () => ({
  useApp: () => ({ setDrawer: appMocks.setDrawer, showEvidence: true, showConfidence: true }),
}));

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const STEPS = [-100, -75, -50, -25, 0, 25, 50, 75, 100];
const RESPONSE: RateSensitivityResponse = {
  built: true,
  steps_bps: STEPS,
  scenario_market_rate_pct: [5.3, 5.55, 5.8, 6.05, 6.3, 6.55, 6.8, 7.05, 7.3],
  base_market_rate_pct: 6.3,
  thresholds: { min_spread_bps: 75, min_equity_pct: 15 },
  states: [
    {
      state: 'IL',
      addressable: 5_000,
      rate_movable: 4_000,
      in_the_money: [2_000, 1_800, 1_600, 1_400, 1_200, 1_000, 800, 600, 400],
      contactable_in_the_money: [200, 180, 160, 140, 120, 100, 80, 60, 40],
    },
  ],
  provenance: {
    gold_source: 'mip.gold.rate_sensitivity_rollup',
    book_source: 'b',
    rule_source: 'r',
    contactable_source: 'c',
    book_as_of: '2026-07-14T12:00:00Z',
    note: 'n',
  },
};
const INDEX = indexRateScenario(RESPONSE) as RateScenarioIndex;

function read(overrides: Partial<GeoRead<RateSensitivityResponse>> = {}): GeoRead<RateSensitivityResponse> {
  return { data: RESPONSE, warmingUp: null, error: null, loading: false, updating: false, retry: vi.fn(), ...overrides };
}

/** The map's arrangement: the control gets the input step, the fill a deferred copy. */
function Harness({ onShown }: { onShown: (step: number) => void }) {
  const [step, setStep] = useState(0);
  const shown = useDeferredValue(step);
  onShown(shown);
  return (
    <RateScenarioControl rate={{ read: read(), index: INDEX, view: null, step, onStepChange: setStep, scope: null }} />
  );
}

describe('RateScenarioControl', () => {
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

  const slider = () => document.querySelector<HTMLInputElement>('input[type="range"]');

  function change(value: number) {
    const input = slider();
    if (!input) throw new Error('slider');
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
      setter?.call(input, String(value));
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
  }

  it('is a labelled native range in basis points with the server rate in its output', () => {
    act(() => root.render(<Harness onShown={() => undefined} />));
    const input = slider();
    expect(input?.min).toBe('-100');
    expect(input?.max).toBe('100');
    expect(input?.step).toBe('25');
    expect(input?.value).toBe('0');
    const label = document.querySelector(`label[for="${input?.id}"]`);
    expect(label?.textContent).toBe('Par rate move');
    expect(document.querySelector('output')?.textContent).toBe('6.30%');
    expect(input?.getAttribute('aria-valuetext')).toBe("Par rate 6.30%, today's rate: 1,200 borrowers in the money.");
    expect(document.querySelector('.rate-lever__meta')?.textContent).toContain("today's par 6.30%");
  });

  it('moves the thumb, the output, the value text and the headline with the input', () => {
    act(() => root.render(<Harness onShown={() => undefined} />));
    change(-50);
    expect(slider()?.value).toBe('-50');
    expect(document.querySelector('output')?.textContent).toBe('5.80%');
    expect(slider()?.getAttribute('aria-valuetext')).toBe('Par rate 5.80%, down 50 basis points: 1,600 borrowers in the money.');
    expect(document.querySelector('.rate-lever__sentence')?.textContent).toBe(
      "If the 30-year par rate were 0.50 points lower (5.80%), 1,600 borrowers would clear this refresh's refi screen: 400 more than today; 160 of them are contactable.",
    );
    // An off-grid value (a pointer between marks) snaps to the nearest step.
    change(60);
    expect(slider()?.value).toBe('50');
  });

  it('Reset returns to today', () => {
    act(() => root.render(<Harness onShown={() => undefined} />));
    change(100);
    expect(slider()?.value).toBe('100');
    const reset = [...document.querySelectorAll('button')].find((button) => button.textContent === 'Reset to today');
    act(() => reset?.dispatchEvent(new MouseEvent('click', { bubbles: true })));
    expect(slider()?.value).toBe('0');
    expect(document.querySelector('output')?.textContent).toBe('6.30%');
  });

  it('the thumb follows the input step, never the deferred view the fill is painted from', () => {
    // The map hands the control its input step and the (lagging) view at the
    // deferred step: while they differ, the thumb, output and value text
    // must already show the input.
    act(() =>
      root.render(
        <RateScenarioControl
          rate={{
            read: read(),
            index: INDEX,
            view: scenarioView(INDEX, 0),
            step: -100,
            onStepChange: () => undefined,
            scope: null,
          }}
        />,
      ),
    );
    expect(slider()?.value).toBe('-100');
    expect(document.querySelector('output')?.textContent).toBe('5.30%');
    expect(slider()?.getAttribute('aria-valuetext')).toBe(
      'Par rate 5.30%, down 100 basis points: 2,000 borrowers in the money.',
    );
  });

  it('the deferred copy catches up with the input', () => {
    const shown: number[] = [];
    act(() => root.render(<Harness onShown={(step) => shown.push(step)} />));
    change(-100);
    expect(slider()?.value).toBe('-100');
    expect(shown[shown.length - 1]).toBe(-100);
  });

  it('opens the evidence drawer on the gold grid from the headline chip', () => {
    act(() => root.render(<Harness onShown={() => undefined} />));
    const chip = document.querySelector<HTMLButtonElement>('.rate-lever__headline .evidence-chip');
    act(() => chip?.dispatchEvent(new MouseEvent('click', { bubbles: true })));
    expect(appMocks.setDrawer).toHaveBeenCalledWith(
      expect.objectContaining({ assetPath: 'mip.gold.rate_sensitivity_rollup', lineageFamily: 'rate_spread' }),
    );
  });

  it('draws no slider and no number while warming, failed or not built, and says why', () => {
    const render = (r: GeoRead<RateSensitivityResponse>, index: RateScenarioIndex | null) =>
      act(() =>
        root.render(
          <RateScenarioControl rate={{ read: r, index, view: null, step: 0, onStepChange: () => undefined, scope: null }} />,
        ),
      );
    render(
      read({
        data: null,
        warmingUp: { dependency: 'warehouse', label: 'Warehouse warming up', attempt: 2, maxAttempts: 6, correlationId: null },
      }),
      null,
    );
    expect(document.body.textContent).toContain('Rate scenarios: Warehouse warming up. Retrying automatically (attempt 2 of 6).');
    expect(document.querySelector('[role="status"]')).not.toBeNull();
    expect(slider()).toBeNull();
    expect(document.body.textContent).not.toMatch(/\d{2,}/);

    const retry = vi.fn();
    render(read({ data: null, error: new Error('boom'), retry }), null);
    expect(document.body.textContent).toContain('Rate scenarios could not load.');
    const button = [...document.querySelectorAll('button')].find((b) => b.textContent === 'Retry');
    act(() => button?.dispatchEvent(new MouseEvent('click', { bubbles: true })));
    expect(retry).toHaveBeenCalledTimes(1);
    expect(slider()).toBeNull();

    render(read({ data: { ...RESPONSE, built: false, states: [] } }), null);
    expect(document.body.textContent).toContain(
      'Rate scenarios are not built yet: the gold refresh job builds them (deploy or Admin > Data operations).',
    );
    expect(slider()).toBeNull();
  });
});
