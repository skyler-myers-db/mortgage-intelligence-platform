/**
 * @vitest-environment happy-dom
 */
/**
 * The map's colouring and Rate Lever step in the URL (D-dataviz-geo-d2
 * 2(iii), deviation:map-mode-url): parse, write, keep other params, replace
 * the history entry, and snap like the control does.
 */
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useLocation, useNavigationType } from 'react-router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { nearestStep } from './rateScenario.recount';
import {
  parseMapMode,
  parseRateStep,
  snapStep,
  useMapModeParams,
  withMapMode,
  type MapModeParams,
} from './useMapModeParams';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('map mode and step params', () => {
  it('parses a mode, falling back to borrowers for anything else', () => {
    expect(parseMapMode('rate')).toBe('rate');
    expect(parseMapMode('unattended')).toBe('unattended');
    for (const raw of [null, '', 'borrowers', 'RATE', 'heat', 'rate ']) expect(parseMapMode(raw)).toBe('borrowers');
  });

  it('honours an integer step in rate mode only; anything else reads 0', () => {
    expect(parseRateStep('-50', 'rate')).toBe(-50);
    expect(parseRateStep('25', 'rate')).toBe(25);
    expect(parseRateStep('-50', 'unattended')).toBe(0);
    expect(parseRateStep('-50', 'borrowers')).toBe(0);
    for (const raw of [null, '', '12.5', '1e2', 'abc', '--5', '99999999999999999999']) expect(parseRateStep(raw, 'rate')).toBe(0);
  });

  it('round-trips through the URL and keeps every other param', () => {
    const base = new URLSearchParams('geo_state=TX&segment=itm');
    const rate = withMapMode(base, 'rate', -50);
    expect(rate.toString()).toBe('geo_state=TX&segment=itm&map_mode=rate&rate_step=-50');
    expect(parseMapMode(rate.get('map_mode'))).toBe('rate');
    expect(parseRateStep(rate.get('rate_step'), 'rate')).toBe(-50);
    // Step 0 and any other mode drop rate_step; borrowers drops map_mode.
    expect(withMapMode(rate, 'rate', 0).toString()).toBe('geo_state=TX&segment=itm&map_mode=rate');
    expect(withMapMode(rate, 'unattended', -50).toString()).toBe('geo_state=TX&segment=itm&map_mode=unattended');
    expect(withMapMode(rate, 'borrowers', -50).toString()).toBe('geo_state=TX&segment=itm');
  });

  it('snaps like the control: the nearest grid step, ties to the lower one', () => {
    const grids = [[-100, -75, -50, -25, 0, 25, 50, 75, 100], [-60, -30, 0, 30, 60], [0], []];
    for (const steps of grids) {
      for (let value = -130; value <= 130; value += 1) {
        expect(snapStep(steps, value), `${steps.join(',')} @ ${value}`).toBe(nearestStep(steps, value));
      }
    }
    expect(snapStep([-50, -25], -37.5)).toBe(-50);
  });
});

describe('useMapModeParams', () => {
  let root: Root;
  let latest: MapModeParams | null = null;
  let seen: { search: string; action: string } = { search: '', action: '' };

  function Probe() {
    latest = useMapModeParams();
    const location = useLocation();
    const action = useNavigationType();
    seen = { search: location.search, action };
    return null;
  }

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
  });

  const render = (entry: string) =>
    act(() => root.render(createElement(MemoryRouter, { key: entry, initialEntries: [entry] }, createElement(Probe))));

  it('reads the link and writes with replace, keeping other params', () => {
    render('/?geo_state=TX&map_mode=rate&rate_step=-50');
    expect(latest?.mode).toBe('rate');
    expect(latest?.step).toBe(-50);

    act(() => latest?.setStep(-75));
    expect(seen).toEqual({ search: '?geo_state=TX&map_mode=rate&rate_step=-75', action: 'REPLACE' });
    act(() => latest?.setMode('unattended'));
    expect(seen).toEqual({ search: '?geo_state=TX&map_mode=unattended', action: 'REPLACE' });
    expect(latest?.step).toBe(0);
    act(() => latest?.setMode('borrowers'));
    expect(seen).toEqual({ search: '?geo_state=TX', action: 'REPLACE' });
  });

  it('keeps the step when rate mode is picked again from a link that carries it', () => {
    render('/?map_mode=rate&rate_step=25');
    act(() => latest?.setMode('rate'));
    expect(seen.search).toBe('?map_mode=rate&rate_step=25');
    expect(latest?.step).toBe(25);
  });

  it('falls back to borrowers for an invalid mode and to 0 for a bad step', () => {
    render('/?map_mode=heat&rate_step=-50');
    expect(latest?.mode).toBe('borrowers');
    expect(latest?.step).toBe(0);
    render('/?map_mode=rate&rate_step=-12.5');
    expect(latest?.mode).toBe('rate');
    expect(latest?.step).toBe(0);
  });
});
