import { afterEach, describe, expect, it, vi } from 'vitest';
import type { LeadsRequest } from '../leadsQuery';
import { fetchLeadFacets, leadFacetsQuery } from './leadFacets';

const REQUEST: LeadsRequest = {
  segment: 'itm',
  geo: { state: 'IL', states: ['IL', 'TX'], zip: '60617' },
  opts: {
    segmentCodes: ['itm', 'equity'],
    segmentMode: 'all',
    approvalStatus: 'pending',
    portfolioCriteria: { product: 'HELOC', min_opportunity_score: '70' },
    limit: 500,
    growthAgentProof: {
      runId: 'r', actionableTotal: 1, cohortFingerprint: 'c', snapshotId: 's', toolResultHash: 't', growthHandoff: 'signed',
    },
  },
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('lead facet counts client', () => {
  it('leads with the dimension and drops its own params, the page size and any proof', () => {
    expect(leadFacetsQuery('state', REQUEST)).toBe(
      'dimension=state&segment=itm&segment_codes=itm%2Cequity&segment_mode=all&zip=60617'
        + '&approval_status=pending&product=HELOC&min_opportunity_score=70',
    );
    expect(leadFacetsQuery('segment', REQUEST)).toBe(
      'dimension=segment&state=IL&zip=60617&states=IL%2CTX&approval_status=pending&product=HELOC&min_opportunity_score=70',
    );
    expect(leadFacetsQuery('product', REQUEST)).not.toContain('product=');
    expect(leadFacetsQuery('approval', REQUEST)).not.toContain('approval_status');
    for (const dimension of ['state', 'segment', 'product', 'approval'] as const) {
      const query = leadFacetsQuery(dimension, REQUEST);
      expect(query).not.toContain('limit=');
      expect(query).not.toContain('growth_handoff');
      expect(query).not.toContain('include_identity_proof');
    }
  });

  it('GETs the audit-free facets path', async () => {
    const seen: string[] = [];
    vi.stubGlobal('fetch', async (path: string) => {
      seen.push(path);
      return new Response(JSON.stringify({ dimension: 'state', total_matching: 0, buckets: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    });
    await expect(fetchLeadFacets('state', { segment: undefined })).resolves.toEqual({
      dimension: 'state', total_matching: 0, buckets: [],
    });
    expect(seen).toEqual(['/api/v1/leads/facets?dimension=state']);
  });
});
