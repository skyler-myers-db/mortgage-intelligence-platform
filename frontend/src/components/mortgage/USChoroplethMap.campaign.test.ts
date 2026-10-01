/**
 * campaignPrefillPath: the map's "Start campaign" target (moved out of
 * USChoroplethMap.tsx, wave 5a d2 step 0). These pin what the component
 * produced before the move.
 */
import { describe, expect, it } from 'vitest';
import type { GeoAssignmentOverlayResponse, GeoAssignmentOverlayUnit } from '../../lib/api';
import { campaignPrefillPath, type MapCampaignInputs } from './USChoroplethMap.campaign';

const unit = (unit_id: string, lead_count: number, unattended_count: number): GeoAssignmentOverlayUnit => ({
  unit_id,
  lead_count,
  assigned_count: lead_count - unattended_count,
  unattended_count,
  covering_officer_count: 1,
  covering_officers: [],
});

const overlay = (level: 'state' | 'zip', units: GeoAssignmentOverlayUnit[]): GeoAssignmentOverlayResponse => ({
  level,
  state: level === 'zip' ? 'TX' : null,
  county_fips: null,
  units,
  total_leads: units.reduce((sum, u) => sum + u.lead_count, 0),
  total_assigned: units.reduce((sum, u) => sum + u.assigned_count, 0),
  total_unattended: units.reduce((sum, u) => sum + u.unattended_count, 0),
  lead_definition: 'score >= threshold',
});

function inputs(overrides: Partial<MapCampaignInputs> = {}): MapCampaignInputs {
  return {
    activeStateCode: 'TX',
    current: { zip: null },
    overlayOn: false,
    overlayData: null,
    overlayByUnit: {},
    segmentFilter: undefined,
    segmentFilterMode: 'any',
    ...overrides,
  };
}

const params = (path: string | null) => new URLSearchParams((path ?? '').split('?')[1] ?? '');

describe('campaignPrefillPath (d2 step 0 characterization)', () => {
  it('is null with no drilled state, and null while a ZIP is selected', () => {
    expect(campaignPrefillPath(inputs({ activeStateCode: null }))).toBeNull();
    expect(campaignPrefillPath(inputs({ current: { zip: '77002' } }))).toBeNull();
  });

  it('is null for a geography it cannot encode (an unknown segment code)', () => {
    expect(campaignPrefillPath(inputs({ segmentFilter: ['not-a-segment'] }))).toBeNull();
  });

  it('links the drilled state with the segment filter and mode', () => {
    const path = campaignPrefillPath(inputs({ segmentFilter: ['itm', 'equity'], segmentFilterMode: 'all' }));
    expect(path?.startsWith('/portfolio-builder?')).toBe(true);
    const query = params(path);
    expect(query.get('states')).toBe('TX');
    expect(query.get('prefill_level')).toBe('state');
    expect(query.get('prefill_segments')).toBe('itm,equity');
    expect(query.get('prefill_segment_mode')).toBe('all');
    expect(query.has('prefill_lead_count')).toBe(false);
  });

  it('carries the state unit counts from a state-level overlay', () => {
    const units = [unit('TX', 40, 12), unit('AZ', 9, 1)];
    const path = campaignPrefillPath(inputs({
      overlayOn: true,
      overlayData: overlay('state', units),
      overlayByUnit: { tx: units[0], az: units[1] },
    }));
    expect(params(path).get('prefill_lead_count')).toBe('40');
    expect(params(path).get('prefill_unattended_count')).toBe('12');
  });

  it('carries the overlay totals when the ZIP grid is on screen', () => {
    const units = [unit('77002', 30, 10), unit('77003', 5, 2)];
    const path = campaignPrefillPath(inputs({ overlayOn: true, overlayData: overlay('zip', units) }));
    expect(params(path).get('prefill_lead_count')).toBe('35');
    expect(params(path).get('prefill_unattended_count')).toBe('12');
  });

  it('leaves the counts out when the overlay is off or the state has no unit', () => {
    const units = [unit('AZ', 9, 1)];
    const off = campaignPrefillPath(inputs({ overlayOn: false, overlayData: overlay('state', units) }));
    expect(params(off).has('prefill_lead_count')).toBe(false);
    const missing = campaignPrefillPath(inputs({ overlayOn: true, overlayData: overlay('state', units), overlayByUnit: { az: units[0] } }));
    expect(params(missing).has('prefill_lead_count')).toBe(false);
  });
});
