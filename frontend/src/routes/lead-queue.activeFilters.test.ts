import { describe, expect, it } from 'vitest';
import {
  MORE_FILTER_PARAMS,
  hasLeadQueueFilters,
  leadQueueFilterParams,
  searchParamsCleared,
  leadBoundChipValue,
  leadQueueActiveFilterChips,
  moreFiltersActiveCount,
  searchParamsWithoutFilter,
  type LeadQueueActiveFilterInput,
} from './lead-queue.activeFilters';
import { LEAD_BOUND_FILTER_KEYS } from './lead-queue.filters';

const BASE: LeadQueueActiveFilterInput = {
  outreachStatus: 'any',
  agedDays: null,
  zipFilters: [],
  cityFilters: [],
  countyFilters: [],
  borrowerIdFilters: [],
};

describe('score and rate-spread chips (audit tables-06)', () => {
  it('reads each dimension as one chip in plain, formatted words', () => {
    expect(leadBoundChipValue('opportunity_score', { min_opportunity_score: '70' })).toBe('≥ 70');
    expect(leadBoundChipValue('opportunity_score', { max_opportunity_score: '90' })).toBe('≤ 90');
    expect(leadBoundChipValue('opportunity_score', { min_opportunity_score: '70', max_opportunity_score: '90' })).toBe('70–90');
    expect(leadBoundChipValue('rate_spread_bps', { min_rate_spread_bps: '50' })).toBe('≥ +50 bps');
    expect(leadBoundChipValue('rate_spread_bps', { max_rate_spread_bps: '-10' })).toBe('≤ −10 bps');
    expect(leadBoundChipValue('rate_spread_bps', { min_rate_spread_bps: '-25', max_rate_spread_bps: '1500' }))
      .toBe('−25 bps – +1,500 bps');
    expect(leadBoundChipValue('opportunity_score', {})).toBeNull();
    expect(leadBoundChipValue('opportunity_score', undefined)).toBeNull();
  });

  it('adds SCORE and RATE SPREAD chips whose removal drops both keys, and no generic chip', () => {
    const chips = leadQueueActiveFilterChips({
      ...BASE,
      portfolioCriteria: {
        owner_link: 'Portfolio investor (5+)',
        min_opportunity_score: '70',
        max_opportunity_score: '90',
        max_rate_spread_bps: '150',
      },
    });
    expect(chips.map((chip) => [chip.label, chip.value, chip.params])).toEqual([
      ['OWNER LINK', 'Portfolio investor (5+)', ['owner_link']],
      ['SCORE', '70–90', ['min_opportunity_score', 'max_opportunity_score']],
      ['RATE SPREAD', '≤ +150 bps', ['min_rate_spread_bps', 'max_rate_spread_bps']],
    ]);
    const next = searchParamsWithoutFilter(
      new URLSearchParams('state=IL&min_opportunity_score=70&max_opportunity_score=90&max_rate_spread_bps=150'),
      chips[1].params,
    );
    expect(next.toString()).toBe('state=IL&max_rate_spread_bps=150');
  });

  it('counts each bounded dimension once toward More filters', () => {
    expect(MORE_FILTER_PARAMS).toEqual(expect.arrayContaining([...LEAD_BOUND_FILTER_KEYS]));
    const chips = leadQueueActiveFilterChips({
      ...BASE,
      portfolioCriteria: { min_opportunity_score: '70', max_opportunity_score: '90', min_rate_spread_bps: '0' },
    });
    expect(moreFiltersActiveCount(chips)).toBe(2);
  });
});

describe('the Triage deck mode is display state (D-approval-flow-a2)', () => {
  it('is never a filter, never enables Clear all and survives it', () => {
    expect(hasLeadQueueFilters(new URLSearchParams('mode=triage&row=B-0000000000001&view=sales-ops'))).toBe(false);
    expect(leadQueueFilterParams(new URLSearchParams('mode=triage&state=IL')).toString()).toBe('state=IL');
    expect(searchParamsCleared(new URLSearchParams('mode=triage&state=IL')).toString()).toBe('mode=triage');
  });
});

describe('hidden columns are display state (tables-05 step 2)', () => {
  it('never enable Clear all and survive it, the none sentinel included', () => {
    expect(hasLeadQueueFilters(new URLSearchParams('hide=segments,rate&view=sales-ops'))).toBe(false);
    expect(hasLeadQueueFilters(new URLSearchParams('hide=none'))).toBe(false);
    expect(leadQueueFilterParams(new URLSearchParams('hide=rate&state=IL')).toString()).toBe('state=IL');
    expect(searchParamsCleared(new URLSearchParams('state=IL&hide=none&view=sales-ops')).toString()).toBe('hide=none&view=sales-ops');
  });
});

describe('an approval-request scope is a filter (flow-02 / shell-06)', () => {
  it('Clear all drops approval_request_batch and keeps the display state', () => {
    const scoped = new URLSearchParams('approval_request_batch=0f8fad5b-d9cb-469f-a165-70867728950e&view=sales-ops');
    expect(hasLeadQueueFilters(scoped)).toBe(true);
    expect(searchParamsCleared(scoped).toString()).toBe('view=sales-ops');
  });
});
