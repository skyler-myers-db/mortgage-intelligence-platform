import { describe, expect, it } from 'vitest';
import {
  MORE_FILTER_PARAMS,
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
    expect(leadBoundChipValue('rate_spread_bps', { max_rate_spread_bps: '-10' })).toBe('≤ -10 bps');
    expect(leadBoundChipValue('rate_spread_bps', { min_rate_spread_bps: '-25', max_rate_spread_bps: '1500' }))
      .toBe('-25 bps – +1,500 bps');
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
