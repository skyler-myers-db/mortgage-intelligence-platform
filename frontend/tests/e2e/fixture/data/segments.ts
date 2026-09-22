/**
 * Segment and geography fixtures: the six segment cards and the
 * state -> county -> ZIP drill, plus the assignment overlay.
 *
 * State rollups are built from `reference.STATES`, so the map total, the
 * portfolio KPIs and the analytics funnel cannot disagree.
 */
import type {
  CountyRollupResponse,
  SegmentSummary,
  StateRollupResponse,
  ZipRollupResponse,
} from '../../../../src/types';
import type { GeoAssignmentOverlayResponse } from '../../../../src/lib/apiTypes';
import { fixture, json, type FixtureEntry } from '../mockApi';
import { LEADS } from './borrowers';
import { SNAPSHOT_DATE, STATES, TOTALS, stateByCode } from './reference';

function mix(count: number, shares: ReadonlyArray<readonly [string, number]>) {
  return shares.map(([value, share]) => ({ value, count: Math.round(count * share) }));
}

function segment(
  code: SegmentSummary['code'],
  name: string,
  count: number,
  contactable: number,
  delta: string,
  avgScore: number,
  description: string,
): SegmentSummary {
  return {
    code,
    name,
    count,
    contactable,
    delta,
    avg_score: avgScore,
    description,
    color: `var(--seg-${code})`,
    source_status: 'connected',
    source_name: null,
    loan_product_mix: mix(count, [['conventional', 0.58], ['fha', 0.24], ['jumbo', 0.12], ['unknown', 0.06]]),
    origination_channel_mix: mix(count, [['loan_officer', 0.62], ['digital', 0.25], ['unknown', 0.13]]),
  };
}

export const SEGMENTS: readonly SegmentSummary[] = [
  segment('itm', 'Prime Refi Candidates', TOTALS.inTheMoney, TOTALS.contactableInTheMoney, '+18%', 82, 'Lien rate >= 75 bps above par and equity >= 15%.'),
  segment('listed', 'Listed for Sale', 1840, 214, '+9%', 77, 'Current active or under-contract Cotality MLS listing.'),
  segment('permit', 'HELOC Intent', 2405, 301, '+11%', 80, 'Cotality HELOC propensity score indicates equity-credit demand.'),
  segment('investor', 'Investor / Multi-Property', 1892, 187, '+6%', 79, 'Owner Link shows 2+ properties or repeat behavior.'),
  segment('equity', 'Home Equity Candidate', 6320, 742, '+14%', 76, 'Strong equity and prior cash-out / HELOC propensity.'),
  segment('retention', 'Retention Risk', 3471, 398, '+4%', 88, 'Current-customer or recapture signals worth reviewing before the borrower shops alternatives.'),
];

/** Twelve ZIP tiles whose addressable counts sum back to the state tile. */
const ZIP_WEIGHTS = [18, 15, 13, 11, 9, 8, 7, 6, 5, 4, 3, 1] as const;
const ZIP_SEGMENTS = ['itm', 'equity', 'listed'] as const;

export const segmentFixtures: FixtureEntry[] = [
  fixture('GET', '/api/segments', ({ query }) => {
    const selected = (query.get('segment_codes') ?? '').split(',').filter(Boolean);
    const divisor = selected.length === 0 ? 1 : query.get('segment_mode') === 'all' ? 8 : 3;
    return json<SegmentSummary[]>(
      SEGMENTS.map((row) => ({
        ...row,
        count: Math.max(1, Math.round(row.count / divisor)),
        contactable: Math.max(1, Math.round((row.contactable ?? 0) / divisor)),
      })),
    );
  }),
  fixture('GET', '/api/geo/state-rollups', () =>
    json<StateRollupResponse>({
      rollups: STATES.map((state) => ({
        state: state.code,
        addressable: state.addressable,
        contactable: state.contactable,
        in_the_money: state.inTheMoney,
        top_tier_opportunities: state.topTier,
        avg_score: state.avgScore,
        zip_unassigned_count: 0,
        top_segment_code: state.topSegment,
      })),
      snapshot_date: SNAPSHOT_DATE,
    }),
  ),
  fixture('GET', '/api/geo/county-rollups', ({ query }) => {
    const state = stateByCode(query.get('state')) ?? STATES[0];
    return json<CountyRollupResponse>({
      state: state.code,
      rollups: [
        {
          fips_5: state.fips,
          state: state.code,
          county_name: state.county,
          addressable_borrowers: state.addressable,
          in_the_money_borrowers: state.inTheMoney,
          high_opportunity_borrowers: state.topTier,
          avg_opportunity_score: state.avgScore,
          top_segment_code: state.topSegment,
        },
      ],
      snapshot_date: SNAPSHOT_DATE,
      scope_note: 'The Cotality share carries one county per state in this footprint.',
    });
  }),
  fixture('GET', '/api/geo/zip-rollups', ({ query }) => {
    const state = stateByCode(query.get('state')) ?? STATES[0];
    const sample = LEADS.find((lead) => lead.state === state.code);
    let remaining = state.addressable;
    const rollups = ZIP_WEIGHTS.map((weight, index) => {
      const isLast = index === ZIP_WEIGHTS.length - 1;
      const share = isLast ? remaining : Math.round((state.addressable * weight) / 100);
      remaining -= share;
      return {
        zip: String(Number(state.zip) + index).padStart(5, '0'),
        state: state.code,
        county_fips_5: null,
        addressable_borrowers: share,
        avg_opportunity_score: state.avgScore - index,
        top_segment_code: ZIP_SEGMENTS[index % ZIP_SEGMENTS.length],
        sample_borrower_id: index === 0 ? sample?.borrower_id ?? null : null,
      };
    });
    return json<ZipRollupResponse>({ state: state.code, fips_5: null, rollups, snapshot_date: SNAPSHOT_DATE });
  }),
  fixture('GET', '/api/geo/assignment-overlay', ({ query }) => {
    const level = query.get('level');
    const units = STATES.map((state) => {
      const assigned = Math.round(state.contactable * 0.4);
      return {
        unit_id: state.code,
        lead_count: state.contactable,
        assigned_count: assigned,
        unattended_count: state.contactable - assigned,
        covering_officer_count: 2,
        covering_officers: ['Loan Officer A', 'Loan Officer B'],
      };
    });
    const totalAssigned = units.reduce((sum, unit) => sum + unit.assigned_count, 0);
    return json<GeoAssignmentOverlayResponse>({
      level: level === 'county' || level === 'zip' ? level : 'state',
      state: query.get('state'),
      county_fips: query.get('county_fips'),
      units,
      total_leads: TOTALS.contactable,
      total_assigned: totalAssigned,
      total_unattended: TOTALS.contactable - totalAssigned,
      lead_definition: 'Marketing-eligible borrowers in mip.gold.borrower_360 without an active assignment.',
    });
  }),
];
