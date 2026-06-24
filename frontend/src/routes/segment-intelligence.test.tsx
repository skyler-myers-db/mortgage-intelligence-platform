import { describe, expect, it } from 'vitest';
import {
  INITIAL_ACTIVE_SEGMENTS,
  formatSelectedSegmentLabel,
  lenderFiltersFromSearch,
  segmentModeFromSearch,
} from './segment-intelligence';

describe('segment intelligence lender overlay URL state', () => {
  it('starts without a selected segment so cards render standalone counts', () => {
    expect(INITIAL_ACTIVE_SEGMENTS).toEqual([]);
  });

  it('hydrates public-safe lender overlay filters from the URL', () => {
    const filters = lenderFiltersFromSearch(
      new URLSearchParams({
        lender_relationship: 'Competitor customer',
        target_lender_ref: 'Competitor B',
        owner_link: 'Portfolio investor (5+)',
        purchase_intent: 'HELOC intent',
      }),
      ['All', 'Competitor B'],
    );

    expect(filters).toEqual({
      lenderRelationship: 'Competitor customer',
      targetLenderRef: 'Competitor B',
      ownerLink: 'Portfolio investor (5+)',
      purchase: 'HELOC intent',
    });
  });

  it('rejects raw lender strings from URL state', () => {
    const filters = lenderFiltersFromSearch(
      new URLSearchParams({
        lender_relationship: 'Wholesale partner',
        target_lender_ref: 'Wells Fargo Bank',
        owner_link: 'Five-property owner',
        purchase_intent: 'Filed permit activity',
      }),
      ['All', 'Competitor B'],
    );

    expect(filters).toEqual({
      lenderRelationship: 'All',
      targetLenderRef: 'All',
      ownerLink: 'All',
      purchase: 'All',
    });
  });

  it('hydrates segment match mode from public URL state', () => {
    expect(segmentModeFromSearch(new URLSearchParams('segment_mode=all'))).toBe('all');
    expect(segmentModeFromSearch(new URLSearchParams('segment_mode=any'))).toBe('any');
    expect(segmentModeFromSearch(new URLSearchParams('segment_mode=drop_table'))).toBe('any');
  });

  it('formats selected segment labels with mode-specific conjunctions', () => {
    expect(formatSelectedSegmentLabel(['Prime Refi Candidates'], 'any')).toBe('Prime Refi Candidates');
    expect(formatSelectedSegmentLabel(['Prime Refi Candidates', 'Listed for Sale'], 'any')).toBe(
      'Prime Refi Candidates or Listed for Sale',
    );
    expect(formatSelectedSegmentLabel(['Prime Refi Candidates', 'Listed for Sale'], 'all')).toBe(
      'Prime Refi Candidates and Listed for Sale',
    );
    expect(formatSelectedSegmentLabel(['A', 'B', 'C'], 'any')).toBe('A, B, or C');
    expect(formatSelectedSegmentLabel(['A', 'B', 'C'], 'all')).toBe('A, B, and C');
  });
});
