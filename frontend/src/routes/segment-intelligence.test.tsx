import { describe, expect, it } from 'vitest';
import {
  INITIAL_ACTIVE_SEGMENTS,
  activeSegmentsFromSearch,
  formatSelectedSegmentLabel,
  lenderFiltersFromSearch,
  segmentCardQuerySelection,
  segmentModeFromSearch,
  segmentSearchParamsForState,
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
    expect(segmentModeFromSearch(new URLSearchParams('segment_mode=ALL'))).toBe('all');
    expect(segmentModeFromSearch(new URLSearchParams('segment_mode=any'))).toBe('any');
    expect(segmentModeFromSearch(new URLSearchParams('segment_mode=drop_table'))).toBe('any');
  });

  it('hydrates selected segments from public URL state and rejects unknown codes', () => {
    expect(activeSegmentsFromSearch(new URLSearchParams('segment=ITM'))).toEqual(['itm']);
    expect(activeSegmentsFromSearch(new URLSearchParams('segment_codes=itm,EQUITY,listed'))).toEqual([
      'itm',
      'equity',
      'listed',
    ]);
    expect(activeSegmentsFromSearch(new URLSearchParams('segments=itm,drop_table,equity,itm'))).toEqual([
      'itm',
      'equity',
    ]);
  });

  it('serializes selected segment cards into shareable route state', () => {
    const base = new URLSearchParams({
      owner_link: 'Portfolio investor (5+)',
      segment: 'retention',
      segment_mode: 'all',
    });

    const any = segmentSearchParamsForState(base, ['itm', 'equity'], 'any');
    expect(any.get('owner_link')).toBe('Portfolio investor (5+)');
    expect(any.get('segment')).toBeNull();
    expect(any.get('segment_codes')).toBe('itm,equity');
    expect(any.get('segment_mode')).toBe('any');

    const single = segmentSearchParamsForState(any, ['listed'], 'any');
    expect(single.get('segment')).toBe('listed');
    expect(single.get('segment_codes')).toBeNull();
    expect(single.get('segment_mode')).toBeNull();

    const emptyIntersection = segmentSearchParamsForState(single, [], 'all');
    expect(emptyIntersection.get('segment')).toBeNull();
    expect(emptyIntersection.get('segment_codes')).toBeNull();
    expect(emptyIntersection.get('segment_mode')).toBeNull();
  });

  it('uses selected-cohort segment counts only after cards are selected', () => {
    expect(segmentCardQuerySelection([], 'all')).toEqual({
      segmentCodes: undefined,
      segmentMode: 'any',
    });
    expect(segmentCardQuerySelection(['itm', 'equity'], 'any')).toEqual({
      segmentCodes: ['itm', 'equity'],
      segmentMode: 'any',
    });
    expect(segmentCardQuerySelection(['itm', 'equity'], 'all')).toEqual({
      segmentCodes: ['itm', 'equity'],
      segmentMode: 'all',
    });
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
