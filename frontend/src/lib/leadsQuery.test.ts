/**
 * leadsQuery key-completeness contract (audit runtime-02 / tables-v1).
 *
 * The Lead Queue key once omitted `cities`, so two city cohorts shared one
 * cache entry. The rule pinned here: for EVERY argument `api.leadsPage`
 * accepts, changing only that argument changes the query key.
 *
 * The variant tables are typed as exhaustive mapped types over the real
 * `api.leadsPage` parameter types. Adding a new argument to the client
 * without adding a variant here is a `tsc -b` error, so the list cannot go
 * stale silently.
 */
import { hashKey } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const leadsPage = vi.hoisted(() => vi.fn());

vi.mock('./api', () => ({
  api: { leadsPage: (...args: unknown[]) => leadsPage(...args) },
}));

import { leadsQuery, type LeadsRequest } from './leadsQuery';

type Geo = NonNullable<LeadsRequest['geo']>;
type Opts = NonNullable<LeadsRequest['opts']>;

const BASE: Required<LeadsRequest> = {
  segment: 'itm',
  geo: {
    state: 'IL',
    zip: '60617',
    county: '17031',
    counties: ['17031'],
    states: ['IL'],
    zips: ['60617'],
    cities: ['CHICAGO~IL'],
    borrowerIds: ['B-AAAAAAAAAAAA1'],
  },
  opts: {
    segmentCodes: ['itm'],
    segmentMode: 'any',
    funnelStage: 'approved',
    targetLenderRef: 'Competitor A',
    cohortId: '11111111-1111-4111-8111-111111111111',
    portfolioCriteria: { product: 'HELOC' },
    approvalStatus: 'pending',
    outreachStatus: 'none',
    assignedTo: 'lo.one@summit.example',
    agedDays: 7,
    limit: 100,
    growthAgentProof: null,
  },
};

const GEO_VARIANTS: { [K in keyof Required<Geo>]: NonNullable<Geo[K]> } = {
  state: 'TX',
  zip: '75217',
  county: '48113',
  counties: ['48113'],
  states: ['TX'],
  zips: ['75217'],
  cities: ['SPRINGFIELD~IL'],
  borrowerIds: ['B-BBBBBBBBBBBB2'],
};

const OPTS_VARIANTS: { [K in keyof Required<Opts>]: NonNullable<Opts[K]> } = {
  segmentCodes: ['equity'],
  segmentMode: 'all',
  funnelStage: 'actioned',
  targetLenderRef: 'Competitor B',
  cohortId: '22222222-2222-4222-8222-222222222222',
  portfolioCriteria: { product: 'Cash-out' },
  approvalStatus: 'approved',
  outreachStatus: 'queued',
  assignedTo: 'lo.two@summit.example',
  agedDays: 30,
  limit: 250,
  growthAgentProof: {
    runId: '33333333-3333-4333-8333-333333333333',
    actionableTotal: 5394,
    cohortFingerprint: 'b'.repeat(64),
    snapshotId: '2026-07-14 12:00:00',
    toolResultHash: 'c'.repeat(64),
    growthHandoff: 'signed-handoff',
  },
};

const baseHash = hashKey(leadsQuery('lead-queue', BASE).queryKey);

describe('leadsQuery key completeness', () => {
  beforeEach(() => {
    leadsPage.mockReset();
    leadsPage.mockResolvedValue({ leads: [] });
  });

  it('changes the key when only `segment` changes', () => {
    const next = leadsQuery('lead-queue', { ...BASE, segment: 'equity' });
    expect(hashKey(next.queryKey)).not.toBe(baseHash);
  });

  it.each(Object.keys(GEO_VARIANTS) as Array<keyof Geo>)(
    'changes the key when only geo.%s changes',
    (field) => {
      const next = leadsQuery('lead-queue', {
        ...BASE,
        geo: { ...BASE.geo, [field]: GEO_VARIANTS[field] },
      });
      expect(hashKey(next.queryKey)).not.toBe(baseHash);
    },
  );

  it.each(Object.keys(OPTS_VARIANTS) as Array<keyof Opts>)(
    'changes the key when only opts.%s changes',
    (field) => {
      const next = leadsQuery('lead-queue', {
        ...BASE,
        opts: { ...BASE.opts, [field]: OPTS_VARIANTS[field] },
      });
      expect(hashKey(next.queryKey)).not.toBe(baseHash);
    },
  );

  it('separates the two city cohorts that used to collide', () => {
    const chicago = leadsQuery('lead-queue', { geo: { cities: ['CHICAGO~IL'] } });
    const springfield = leadsQuery('lead-queue', { geo: { cities: ['SPRINGFIELD~IL'] } });
    const national = leadsQuery('lead-queue', { geo: { cities: [] } });
    const hashes = [chicago, springfield, national].map((q) => hashKey(q.queryKey));
    expect(new Set(hashes).size).toBe(3);
  });

  it('is stable for an equal request built on a later render', () => {
    const again = leadsQuery('lead-queue', {
      segment: BASE.segment,
      opts: { ...BASE.opts },
      geo: { ...BASE.geo },
    });
    expect(hashKey(again.queryKey)).toBe(baseHash);
  });

  it('keys implicit inputs and the per-surface scope', () => {
    expect(hashKey(leadsQuery('lead-queue', BASE, ['proof-a']).queryKey)).not.toBe(
      hashKey(leadsQuery('lead-queue', BASE, ['proof-b']).queryKey),
    );
    expect(hashKey(leadsQuery('segment-intelligence', BASE).queryKey)).not.toBe(baseHash);
  });

  it('stays under the operational invalidation prefix', () => {
    expect(leadsQuery('lead-queue', BASE).queryKey.slice(0, 2)).toEqual(['mip', 'leads']);
  });

  it('hands the fetcher the very same members the key hashed', async () => {
    const signal = new AbortController().signal;
    await leadsQuery('lead-queue', BASE).fetcher(signal);
    expect(leadsPage).toHaveBeenCalledTimes(1);
    const [segment, passedSignal, geo, opts] = leadsPage.mock.calls[0];
    expect(segment).toBe(BASE.segment);
    expect(passedSignal).toBe(signal);
    expect(geo).toBe(BASE.geo);
    expect(opts).toBe(BASE.opts);
  });
});
