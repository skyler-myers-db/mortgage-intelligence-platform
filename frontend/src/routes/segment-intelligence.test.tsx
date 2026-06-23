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
});
