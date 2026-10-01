/**
 * The bulk gate's sampler and coverage (audit flow-03 / states-06,
 * D-approval-flow-a1): which rows are previewed, and whether the previewed
 * samples cover every offer in the run (Approve arms only then).
 */
import { describe, expect, it } from 'vitest';
import type { OutreachDraftResult } from '../../lib/apiTypes';
import { bulkSampleCoverage, coveredOfferCodes, type CoverageRow } from './LeadBulkApproveReview.coverage';
import { stratifiedSampleIds } from './LeadBulkApproveReview.sampler';

function rows(offers: readonly string[]): CoverageRow[] {
  return offers.map((offer, index) => ({ borrower_id: `B-${index}`, recommended_offer_code: offer }));
}

function draft(offer: string | null = null): OutreachDraftResult {
  return { generation_id: 'gen', offer_code: offer } as unknown as OutreachDraftResult;
}

describe('stratifiedSampleIds', () => {
  it('10 rows across 3 offers: one sample per offer, 3 in all', () => {
    const run = rows(['refi', 'refi', 'heloc', 'refi', 'purchase', 'refi', 'heloc', 'refi', 'refi', 'purchase']);
    expect(stratifiedSampleIds(run)).toEqual(['B-0', 'B-2', 'B-4']);
  });

  it('5 rows of 1 offer: still 3 samples, in table order', () => {
    expect(stratifiedSampleIds(rows(['refi', 'refi', 'refi', 'refi', 'refi']))).toEqual(['B-0', 'B-1', 'B-2']);
  });

  it('9 offers: 9 samples, never fewer than the offers', () => {
    const offers = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i'];
    expect(stratifiedSampleIds(rows(offers))).toHaveLength(9);
  });

  it('6 rows across 2 offers: one each, then the larger offer tops up (3 samples, not 2)', () => {
    const run = rows(['refi', 'heloc', 'refi', 'refi', 'heloc', 'refi']);
    expect(stratifiedSampleIds(run)).toEqual(['B-0', 'B-1', 'B-2']);
  });

  it('a row with no recommended offer is its own offer', () => {
    const run: CoverageRow[] = [
      { borrower_id: 'B-0', recommended_offer_code: 'refi' },
      { borrower_id: 'B-1', recommended_offer_code: null },
      { borrower_id: 'B-2' },
      { borrower_id: 'B-3', recommended_offer_code: 'refi' },
    ];
    expect(stratifiedSampleIds(run)).toEqual(['B-0', 'B-1', 'B-3']);
  });

  it('two rows give two samples; no rows give none', () => {
    expect(stratifiedSampleIds(rows(['refi', 'refi']))).toEqual(['B-0', 'B-1']);
    expect(stratifiedSampleIds([])).toEqual([]);
  });
});

describe('bulkSampleCoverage', () => {
  it('is complete once every offer has a ready sample', () => {
    const run = rows(['refi', 'heloc', 'refi']);
    const drafts = new Map([['B-0', draft('refi')], ['B-1', draft('heloc')]]);
    expect(bulkSampleCoverage(run, drafts)).toEqual({ complete: true, missingOfferCodes: [], missingSampleIds: [] });
  });

  it('a selection change that adds an uncovered offer names it and its first row', () => {
    const drafts = new Map([['B-0', draft('refi')]]);
    const before = rows(['refi', 'refi']);
    expect(bulkSampleCoverage(before, drafts).complete).toBe(true);
    const after = [...before, { borrower_id: 'B-7', recommended_offer_code: 'cash_out' }, { borrower_id: 'B-8', recommended_offer_code: 'cash_out' }];
    expect(bulkSampleCoverage(after, drafts)).toEqual({
      complete: false,
      missingOfferCodes: ['cash_out'],
      missingSampleIds: ['B-7'],
    });
  });

  it('a failed sample (no ready draft) leaves its offer uncovered; deselecting its row drops the offer', () => {
    const run = rows(['refi', 'heloc']);
    // B-1's sample failed: it is not among the ready drafts.
    const drafts = new Map([['B-0', draft('refi')]]);
    expect(bulkSampleCoverage(run, drafts)).toEqual({
      complete: false,
      missingOfferCodes: ['heloc'],
      missingSampleIds: ['B-1'],
    });
    expect(bulkSampleCoverage(run.slice(0, 1), drafts).complete).toBe(true);
  });

  it('a sample of a row no longer in the run covers nothing', () => {
    const drafts = new Map([['B-9', draft('refi')]]);
    expect(bulkSampleCoverage(rows(['refi']), drafts).complete).toBe(false);
  });
});

describe('coveredOfferCodes', () => {
  it('names the offers the previewed drafts showed (a draft\'s own offer, else its row\'s)', () => {
    const run = rows(['refi', 'heloc', 'purchase']);
    const drafts = new Map([['B-0', draft('refi_plus_heloc')], ['B-1', draft(null)]]);
    expect([...coveredOfferCodes(run, drafts)].sort()).toEqual(['heloc', 'refi_plus_heloc']);
  });
});
