import { describe, expect, it } from 'vitest';
import { INITIAL_ACTIVE_SEGMENTS, lenderFiltersFromSearch } from './segment-intelligence';

describe('segment intelligence lender overlay URL state', () => {
  it('starts without a selected segment so cards render standalone counts', () => {
    expect(INITIAL_ACTIVE_SEGMENTS).toEqual([]);
  });

  it('hydrates public-safe lender overlay filters from the URL', () => {
    const filters = lenderFiltersFromSearch(
      new URLSearchParams({
        lender_relationship: 'Competitor customer',
        target_lender_ref: 'Competitor B',
      }),
      ['All', 'Competitor B'],
    );

    expect(filters).toEqual({
      lenderRelationship: 'Competitor customer',
      targetLenderRef: 'Competitor B',
    });
  });

  it('rejects raw lender strings from URL state', () => {
    const filters = lenderFiltersFromSearch(
      new URLSearchParams({
        lender_relationship: 'Wholesale partner',
        target_lender_ref: 'Wells Fargo Bank',
      }),
      ['All', 'Competitor B'],
    );

    expect(filters).toEqual({
      lenderRelationship: 'All',
      targetLenderRef: 'All',
    });
  });
});
