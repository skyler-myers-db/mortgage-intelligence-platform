/**
 * buildMapTableRows: the "View as table" rows the map derives (moved out of
 * USChoroplethMap.tsx, wave 5a d2 step 0). These pin the rows the component
 * produced before the move: state rows (populated only), ZIP rows, the extra
 * column in the overlay and rate modes, and a drill button only in filter mode.
 */
import { describe, expect, it, vi } from 'vitest';
import type { GeoAssignmentOverlayUnit } from '../../lib/api';
import type { StateRollup, ZipRollup } from '../../types';
import type { MapScenarioView } from './rateScenario.logic';
import { buildChoroplethScale } from './USChoroplethMap.scale';
import { buildMapTableRows, type MapTableInputs } from './USChoroplethMap.table';
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
    ...overrides,
  };
}

describe('buildMapTableRows (d2 step 0 characterization)', () => {
  it('emits one row per populated drawn state with the rollup facts', () => {
    const rows = buildMapTableRows(inputs());
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
    expect(buildMapTableRows(inputs({ stateFacts: null }))).toEqual([]);
    expect(buildMapTableRows(inputs({ usaMap: null }))).toEqual([]);
  });

  it('emits one row per ZIP at the ZIP level', () => {
    const zipFacts: Record<string, ZipRollup> = {
      '85004': { zip: '85004', state: 'AZ', addressable_borrowers: 90, avg_opportunity_score: 66, top_segment_code: null },
    };
    const rows = buildMapTableRows(inputs({ level: 'zip', zipFacts, scale: buildChoroplethScale([90]) }));
    expect(rows).toEqual([
      { id: '85004', name: '85004', count: 90, avgScore: 66, topSegment: undefined, extra: undefined, cls: 4 },
    ]);
  });

  it('carries the overlay value in the extra column at both levels', () => {
    const overlayByUnit = { az: unit('AZ', 7), '85004': unit('85004', 3) };
    const state = buildMapTableRows(inputs({ overlayActive: true, overlayByUnit, scale: buildChoroplethScale([7]) }));
    expect(state.map((row) => [row.id, row.extra])).toEqual([['az', 7], ['ca', null]]);
    const zipFacts: Record<string, ZipRollup> = {
      '85004': { zip: '85004', state: 'AZ', addressable_borrowers: 90, avg_opportunity_score: 66 },
    };
    const zip = buildMapTableRows(inputs({ level: 'zip', overlayActive: true, overlayByUnit, zipFacts }));
    expect(zip[0].extra).toBe(3);
  });

  it('carries the scenario count in the extra column in rate mode', () => {
    const shownScenario: MapScenarioView = { step: -50, ratePct: 5.8, inTheMoneyById: { az: 12, ca: 40 }, total: 52 };
    const rows = buildMapTableRows(inputs({ shownScenario, scale: buildChoroplethScale([12, 40]) }));
    expect(rows.map((row) => [row.id, row.extra])).toEqual([['az', 12], ['ca', 40]]);
  });

  it('gives a state row a drill button in filter mode only, and it drills with focus', () => {
    const activateState = vi.fn();
    const rows = buildMapTableRows(inputs({ activateState }));
    rows[0].onOpen?.();
    expect(activateState).toHaveBeenCalledWith(USA.locations[0], true, true);
    const navigateRows = buildMapTableRows(inputs({ drillBehavior: 'navigate' }));
    expect(navigateRows.every((row) => row.onOpen === undefined)).toBe(true);
  });
});
