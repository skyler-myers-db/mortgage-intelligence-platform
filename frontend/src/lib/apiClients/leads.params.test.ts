/**
 * Golden URLs of `api.leadsPage` (W5a, tables-06): the query builder was
 * extracted into the pure `leadsQueryParams` so the audit-free facet counts
 * build the SAME filter params. These literals were captured from the
 * pre-extraction code and pin the wire byte for byte.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '../api';
import type { LeadQueryOptions } from '../apiTypes';

type Case = [name: string, segment: string | undefined, geo: Parameters<typeof api.leadsPage>[2], opts: LeadQueryOptions, url: string];

const CASES: Case[] = [
  ['bare queue', undefined, undefined, { growthAgentProof: null }, '/api/v1/leads'],
  ['segment + limit', 'itm', undefined, { limit: 50, growthAgentProof: null }, '/api/v1/leads?segment=itm&limit=50'],
  [
    'multi-segment all + funnel',
    undefined,
    undefined,
    { segmentCodes: ['itm', 'equity'], segmentMode: 'all', funnelStage: 'approved', growthAgentProof: null },
    '/api/v1/leads?segment_codes=itm%2Cequity&segment_mode=all&funnel_stage=approved',
  ],
  [
    'every geography key',
    undefined,
    {
      state: 'IL',
      zip: '60617',
      county: '17031',
      counties: ['17031', '17043'],
      states: ['IL', 'TX'],
      zips: ['60617', '75217'],
      cities: ['CHICAGO~IL', 'FORT LAUDERDALE~FL'],
      borrowerIds: ['B-0123456789ABC'],
    },
    { growthAgentProof: null },
    '/api/v1/leads?state=IL&zip=60617&county=17031&counties=17031%2C17043&states=IL%2CTX&zips=60617%2C75217'
      + '&cities=CHICAGO%7EIL%2CFORT+LAUDERDALE%7EFL&borrower_ids=B-0123456789ABC',
  ],
  [
    'workflow, lender, cohort',
    undefined,
    undefined,
    {
      targetLenderRef: 'Competitor B',
      cohortId: '11111111-1111-1111-1111-111111111111',
      approvalStatus: 'pending',
      outreachStatus: 'none',
      assignedTo: 'lo.one@summit.example',
      agedDays: 7,
      growthAgentProof: null,
    },
    '/api/v1/leads?target_lender_ref=Competitor+B&cohort_id=11111111-1111-1111-1111-111111111111'
      + '&approval_status=pending&outreach_status=none&assigned_to=lo.one%40summit.example&aged_days=7',
  ],
  [
    'any statuses are omitted',
    undefined,
    undefined,
    { approvalStatus: 'any', outreachStatus: 'any', agedDays: null, growthAgentProof: null },
    '/api/v1/leads',
  ],
  [
    'portfolio criteria incl. the public bounds, empty values dropped',
    'retention',
    { state: 'CA' },
    {
      portfolioCriteria: {
        occupancy: 'Owner-occupied',
        marketing_eligibility: 'Any',
        min_opportunity_score: '70',
        max_rate_spread_bps: '-25',
        recency: '',
        consent_status: null,
      },
      growthAgentProof: null,
    },
    '/api/v1/leads?segment=retention&state=CA&occupancy=Owner-occupied&marketing_eligibility=Any'
      + '&min_opportunity_score=70&max_rate_spread_bps=-25',
  ],
  [
    'a Growth Agent proof',
    'itm',
    undefined,
    {
      growthAgentProof: {
        runId: '11111111-1111-4111-8111-111111111111',
        actionableTotal: 1,
        cohortFingerprint: 'a'.repeat(64),
        snapshotId: 's-1',
        toolResultHash: 'b'.repeat(64),
        growthHandoff: 'signed.token',
      },
    },
    '/api/v1/leads?segment=itm&include_identity_proof=true&growth_handoff=signed.token',
  ],
];

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('leadsPage query (golden)', () => {
  it.each(CASES)('%s', async (_name, segment, geo, opts, url) => {
    const seen: string[] = [];
    vi.stubGlobal('fetch', async (path: string) => {
      seen.push(path);
      return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    await api.leadsPage(segment, undefined, geo, opts).catch(() => undefined);
    expect(seen[0]).toBe(url);
  });
});
