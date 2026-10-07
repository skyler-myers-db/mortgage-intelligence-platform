/**
 * buildMapTableRows: the "View as table" rows the map derives (moved out of
 * USChoroplethMap.tsx, wave 5a d2 step 0): state rows (populated only), ZIP
 * rows, the extra column in the overlay and rate modes, a drill button only
 * in filter mode, and (dataviz-10) the drawn states the map skips as their
 * own group.
 */
import { describe, expect, it, vi } from 'vitest';
import type { GeoAssignmentOverlayUnit } from '../../lib/api';
import type { StateRollup, ZipRollup } from '../../types';
import type { MapScenarioView } from './rateScenario.logic';
import { buildChoroplethScale } from './USChoroplethMap.scale';
import { EMPTY_GROUP_LABEL, buildMapTableRows, offMapCaption, type MapTableInputs } from './USChoroplethMap.table';
import type { UsaSvgMap } from './USChoroplethMap.utils';

const USA: UsaSvgMap = {
  label: 'United States',
  viewBox: '0 0 100 100',
  locations: [
    { id: 'az', name: 'Arizona', path: 'M0,0Z' },
    { id: 'ca', name: 'California', path: 'M1,1Z' },
    { id: 'wy', name: 'Wyoming', path: 'M2,2Z' },
  ],
};

const rollup = (state: string, addressable: number, extra: Partial<StateRollup> = {}): StateRollup => ({
  state,
  addressable,
  contactable: Math.round(addressable / 10),
  in_the_money: 1,
  top_tier_opportunities: 1,
  avg_score: 70,
  top_segment_code: 'itm',
  ...extra,
});

const STATE_FACTS: Record<string, StateRollup> = { az: rollup('AZ', 400), ca: rollup('CA', 1600) };

const unit = (unit_id: string, unattended_count: number): GeoAssignmentOverlayUnit => ({
  unit_id,
  lead_count: unattended_count + 5,
  assigned_count: 5,
  unattended_count,
  covering_officer_count: 1,
  covering_officers: [],
});

function inputs(overrides: Partial<MapTableInputs> = {}): MapTableInputs {
  return {
    shownScenario: null,
    overlayActive: false,
    overlayByUnit: {},
    level: 'state',
    stateFacts: STATE_FACTS,
    usaMap: USA,
    scale: buildChoroplethScale([400, 1600]),
    drillBehavior: 'filter',
    activateState: vi.fn(),
    zipFacts: null,
    footprintStates: { az: '04', ca: '06' },
    ...overrides,
  };
}

describe('buildMapTableRows (d2 step 0 characterization)', () => {
  it('emits one row per populated drawn state with the rollup facts', () => {
    const { rows } = buildMapTableRows(inputs());
    expect(rows.map((row) => row.id)).toEqual(['az', 'ca']);
    expect(rows[1]).toMatchObject({
      name: 'California',
      count: 1600,
      avgScore: 70,
      topSegment: 'Prime Refi Candidates',
      contactable: 160,
      extra: undefined,
      cls: 4,
    });
    expect(buildMapTableRows(inputs({ stateFacts: null }))).toEqual({ rows: [], offMap: [], empty: [] });
    expect(buildMapTableRows(inputs({ usaMap: null }))).toEqual({ rows: [], offMap: [], empty: [] });
  });

  it('emits one row per ZIP at the ZIP level', () => {
    const zipFacts: Record<string, ZipRollup> = {
      '85004': { zip: '85004', state: 'AZ', addressable_borrowers: 90, avg_opportunity_score: 66, top_segment_code: null },
    };
    const { rows, empty } = buildMapTableRows(inputs({ level: 'zip', zipFacts, scale: buildChoroplethScale([90]) }));
    expect(empty).toEqual([]);
    expect(rows).toEqual([
      { id: '85004', name: '85004', count: 90, avgScore: 66, topSegment: undefined, extra: undefined, cls: 4 },
    ]);
  });

  it('carries the overlay value in the extra column at both levels', () => {
    const overlayByUnit = { az: unit('AZ', 7), '85004': unit('85004', 3) };
    const state = buildMapTableRows(inputs({ overlayActive: true, overlayByUnit, scale: buildChoroplethScale([7]) })).rows;
    expect(state.map((row) => [row.id, row.extra])).toEqual([['az', 7], ['ca', null]]);
    const zipFacts: Record<string, ZipRollup> = {
      '85004': { zip: '85004', state: 'AZ', addressable_borrowers: 90, avg_opportunity_score: 66 },
    };
    const zip = buildMapTableRows(inputs({ level: 'zip', overlayActive: true, overlayByUnit, zipFacts })).rows;
    expect(zip[0].extra).toBe(3);
  });

  it('carries the scenario count in the extra column in rate mode', () => {
    const shownScenario: MapScenarioView = {
      step: -50, ratePct: 5.8, inTheMoneyById: { az: 12, ca: 40 }, total: 52, todayById: null, changeById: null, contactableById: null, totalChange: null,
    };
    const { rows } = buildMapTableRows(inputs({ shownScenario, scale: buildChoroplethScale([12, 40]) }));
    expect(rows.map((row) => [row.id, row.extra])).toEqual([['az', 12], ['ca', 40]]);
  });

  // wow-stage-1: change versus today and the contactable subset at the step ride on every state row.
  it('carries change versus today and the contactable subset on drawn, not-drawn and empty rows', () => {
    const shownScenario: MapScenarioView = {
      step: -50,
      ratePct: 5.8,
      inTheMoneyById: { az: 12, ca: 40, vi: 7, wy: 0 },
      total: 59,
      todayById: { az: 10, ca: 30, vi: 6, wy: 0 },
      changeById: { az: 2, ca: 10, vi: 1, wy: 0 },
      contactableById: { az: 3, ca: null, vi: null, wy: 0 },
      totalChange: 13,
    };
    const { rows, offMap, empty } = buildMapTableRows(inputs({ shownScenario, scale: buildChoroplethScale([12, 40]) }));
    expect(rows.map((row) => [row.id, row.extra, row.change, row.scenarioContactable])).toEqual([
      ['az', 12, 2, 3],
      ['ca', 40, 10, null],
    ]);
    expect(offMap.map((row) => [row.id, row.change, row.scenarioContactable])).toEqual([['vi', 1, null]]);
    expect(empty.map((row) => [row.id, row.change, row.scenarioContactable])).toEqual([['wy', 0, 0]]);
    // Every grid id lands in exactly one group, so the groups sum to the whole-book change.
    expect([...rows, ...offMap, ...empty].reduce((sum, row) => sum + (row.change ?? 0), 0)).toBe(shownScenario.totalChange);
  });

  it('leaves the scenario cells off outside rate mode', () => {
    const { rows } = buildMapTableRows(inputs());
    expect(rows[0]).not.toHaveProperty('change');
    expect(rows[0]).not.toHaveProperty('scenarioContactable');
  });

  it('gives a state row a drill button in filter mode only, and it drills with focus', () => {
    const activateState = vi.fn();
    const { rows, empty } = buildMapTableRows(inputs({ activateState }));
    rows[0].onOpen?.();
    expect(activateState).toHaveBeenCalledWith(USA.locations[0], true, true);
    expect(empty.every((row) => row.onOpen === undefined)).toBe(true);
    const navigateRows = buildMapTableRows(inputs({ drillBehavior: 'navigate' })).rows;
    expect(navigateRows.every((row) => row.onOpen === undefined)).toBe(true);
  });

  it('lists the drawn states with no borrowers in their own group, with why (dataviz-10)', () => {
    const facts = { ...STATE_FACTS, wy: rollup('WY', 0) };
    const { rows, empty } = buildMapTableRows(inputs({ stateFacts: facts }));
    // Addressable 0 is not populated, whatever the rollup carries.
    expect(rows.map((row) => row.id)).toEqual(['az', 'ca']);
    expect(empty).toEqual([
      { id: 'wy', name: 'Wyoming', count: null, avgScore: null, extra: undefined, cls: null, note: 'Outside Cotality evaluation scope' },
    ]);
    const inFootprint = buildMapTableRows(inputs({ footprintStates: { az: '04', ca: '06', wy: '56' } })).empty;
    expect(inFootprint[0].note).toBe(EMPTY_GROUP_LABEL);
    // The overlay is segment-agnostic: an empty-group state can carry unattended leads.
    const overlay = buildMapTableRows(inputs({ overlayActive: true, overlayByUnit: { wy: unit('WY', 4) } })).empty;
    expect(overlay[0].extra).toBe(4);
  });

  it('lists ids the map cannot draw from every source the mode reads (wow-stage-1)', () => {
    const facts = { ...STATE_FACTS, pr: rollup('PR', 300) };
    const borrowers = buildMapTableRows(inputs({ stateFacts: facts })).offMap;
    expect(borrowers).toEqual([
      { id: 'pr', name: 'PR', count: 300, avgScore: 70, topSegment: 'Prime Refi Candidates', contactable: 30, extra: undefined, cls: null },
    ]);
    expect(offMapCaption(borrowers, 'count')).toBe('Includes 300 in PR (not drawn on the map)');

    // Rate mode adds the grid's ids; unattended mode the overlay units'.
    const shownScenario: MapScenarioView = {
      step: 0, ratePct: 6.3, inTheMoneyById: { az: 1, vi: 7 }, total: 8, todayById: null, changeById: null, contactableById: null, totalChange: null,
    };
    const rate = buildMapTableRows(inputs({ stateFacts: facts, shownScenario })).offMap;
    expect(rate.map((row) => [row.name, row.count, row.extra])).toEqual([['PR', 300, null], ['VI', null, 7]]);
    expect(offMapCaption(rate, 'extra')).toBe('Includes 7 in PR, VI (not drawn on the map)');
    const overlay = buildMapTableRows(inputs({ overlayActive: true, overlayByUnit: { gu: unit('GU', 2) } })).offMap;
    expect(overlay.map((row) => [row.name, row.extra])).toEqual([['GU', 2]]);

    expect(offMapCaption([], 'count')).toBeNull();
    expect(buildMapTableRows(inputs({ level: 'zip', zipFacts: {} })).offMap).toEqual([]);
  });
});

