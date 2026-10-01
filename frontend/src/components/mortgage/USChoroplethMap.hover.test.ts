/**
 * buildMapCard: the hover / focus card content for one unit
 * (D-dataviz-geo-d1). Counts are never coerced and officers are named only
 * for the selected unit.
 */
import { describe, expect, it } from 'vitest';
import type { GeoAssignmentOverlayUnit } from '../../lib/api';
import type { StateRollup, ZipRollup } from '../../types';
import type { MapScenarioView } from './rateScenario.logic';
import {
  SOURCE_IN_SCOPE,
  SOURCE_OUT_OF_SCOPE,
  SOURCE_ZIP,
  buildMapCard,
  type MapCardInputs,
} from './USChoroplethMap.hover';

const rollup = (state: string, extra: Partial<StateRollup> = {}): StateRollup => ({
  state,
  addressable: 2148,
  contactable: 748,
  in_the_money: 1,
  top_tier_opportunities: 1,
  avg_score: 82,
  zip_unassigned_count: 12,
  top_segment_code: 'equity',
  ...extra,
});

const unit = (unit_id: string): GeoAssignmentOverlayUnit => ({
  unit_id,
  lead_count: 40,
  assigned_count: 28,
  unattended_count: 12,
  covering_officer_count: 2,
  covering_officers: ['LO One', 'LO Two'],
});

const ZIP: ZipRollup = { zip: '77002', state: 'TX', addressable_borrowers: 1200, avg_opportunity_score: 82, top_segment_code: 'itm' };

function inputs(overrides: Partial<MapCardInputs> = {}): MapCardInputs {
  return {
    usaMap: {
      label: 'United States',
      viewBox: '0 0 1 1',
      locations: [
        { id: 'tx', name: 'Texas', path: 'M0Z' },
        { id: 'wy', name: 'Wyoming', path: 'M1Z' },
      ],
    },
    stateFacts: { tx: rollup('TX') },
    overlayByUnit: null,
    footprintStates: { tx: '48' },
    selectedId: null,
    drillStateName: 'Texas',
    zipFacts: { '77002': ZIP },
    selectedZip: null,
    scenario: null,
    ...overrides,
  };
}

describe('buildMapCard (D-dataviz-geo-d1)', () => {
  it('builds a state card with the in-scope source and both disclosures', () => {
    expect(buildMapCard('state', 'tx', inputs())).toEqual({
      name: 'Texas',
      count: 2148,
      avgScore: 82,
      topSegment: 'Home Equity Candidate',
      sourceHint: SOURCE_IN_SCOPE,
      contactable: 748,
      zipUnassigned: 12,
      overlay: undefined,
    });
  });

  it('builds an honest out-of-scope card for a state with no rollup outside the footprint', () => {
    const card = buildMapCard('state', 'wy', inputs());
    expect(card).toMatchObject({ name: 'Wyoming', count: null, avgScore: null, sourceHint: SOURCE_OUT_OF_SCOPE });
    expect(card?.contactable).toBeNull();
  });

  it('keeps a contactable of 0 as 0 and a missing one as null', () => {
    expect(buildMapCard('state', 'tx', inputs({ stateFacts: { tx: rollup('TX', { contactable: 0 }) } }))?.contactable).toBe(0);
    expect(buildMapCard('state', 'tx', inputs({ stateFacts: { tx: rollup('TX', { contactable: undefined }) } }))?.contactable).toBeNull();
  });

  it('carries the overlay counts, naming officers for the selected unit only', () => {
    const overlay = inputs({ overlayByUnit: { tx: unit('TX'), '77002': unit('77002') } });
    expect(buildMapCard('state', 'tx', overlay)?.overlay).toEqual({
      leadCount: 40,
      assignedCount: 28,
      unattendedCount: 12,
      coveringOfficerCount: 2,
      coveringOfficers: undefined,
    });
    expect(buildMapCard('state', 'tx', { ...overlay, selectedId: 'tx' })?.overlay?.coveringOfficers).toEqual(['LO One', 'LO Two']);
    expect(buildMapCard('zip', '77002', overlay)?.overlay?.coveringOfficers).toBeUndefined();
    expect(buildMapCard('zip', '77002', { ...overlay, selectedZip: '77002' })?.overlay?.coveringOfficers).toEqual(['LO One', 'LO Two']);
  });

  it('builds a ZIP card from the drilled state rollup, with the ZIP source and no state disclosures', () => {
    const card = buildMapCard('zip', '77002', inputs());
    expect(card).toEqual({
      name: 'ZIP 77002, Texas',
      count: 1200,
      avgScore: 82,
      topSegment: 'Prime Refi Candidates',
      sourceHint: SOURCE_ZIP,
      overlay: undefined,
    });
    expect(card).not.toHaveProperty('zipUnassigned');
  });

  // wow-stage-1: the Rate Lever's rows ride on the STATE card only.
  const SCENARIO: MapScenarioView = {
    step: -50,
    ratePct: 5.8,
    inTheMoneyById: { tx: 1_300 },
    total: 1_300,
    todayById: { tx: 1_000 },
    changeById: { tx: 300 },
    contactableById: { tx: null },
    totalChange: 300,
  };

  it('fills the scenario on a state card from the shown view, contactable null when not reported', () => {
    expect(buildMapCard('state', 'tx', inputs({ scenario: SCENARIO }))?.scenario).toEqual({
      step: -50,
      ratePct: 5.8,
      today: 1_000,
      atStep: 1_300,
      change: 300,
      contactableAtStep: null,
    });
    const reported = { ...SCENARIO, contactableById: { tx: 90 } };
    expect(buildMapCard('state', 'tx', inputs({ scenario: reported }))?.scenario?.contactableAtStep).toBe(90);
    // Off the grid, or without a step 0 to compare with: no scenario rows.
    expect(buildMapCard('state', 'wy', inputs({ scenario: SCENARIO }))?.scenario).toBeUndefined();
    const noToday = { ...SCENARIO, todayById: null, changeById: null, contactableById: null, totalChange: null };
    expect(buildMapCard('state', 'tx', inputs({ scenario: noToday }))?.scenario).toBeUndefined();
  });

  it('never puts scenario rows on a ZIP card', () => {
    const card = buildMapCard('zip', '77002', inputs({ scenario: SCENARIO }));
    expect(card).not.toHaveProperty('scenario');
  });

  it('is null for a unit that is not on the stage', () => {
    expect(buildMapCard('state', 'zz', inputs())).toBeNull();
    expect(buildMapCard('zip', '99999', inputs())).toBeNull();
    expect(buildMapCard('zip', '77002', inputs({ zipFacts: null }))).toBeNull();
  });
});
