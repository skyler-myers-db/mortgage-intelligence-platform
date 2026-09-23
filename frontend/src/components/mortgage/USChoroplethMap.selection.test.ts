import { describe, expect, it } from 'vitest';
import {
  EMPTY_MAP_SELECTION,
  MAP_STATE_PARAM,
  MAP_ZIP_PARAM,
  mapSelectionFromSearch,
  parseMapSelection,
  sameMapSelection,
  withMapSelection,
} from './USChoroplethMap.selection';

describe('map selection URL form (dataviz-04)', () => {
  it('parses a drilled state and ZIP from the search params', () => {
    expect(mapSelectionFromSearch(new URLSearchParams('geo_state=tx&zip=77002'))).toEqual({
      state: 'TX',
      county: null,
      zip: '77002',
    });
    expect(mapSelectionFromSearch(new URLSearchParams(''))).toEqual(EMPTY_MAP_SELECTION);
  });

  it('only drills an intrinsic USPS code, and a ZIP only inside a state', () => {
    expect(parseMapSelection('ZZ', null).state).toBeNull();
    expect(parseMapSelection('Texas', null).state).toBeNull();
    expect(parseMapSelection('<x>', '77002')).toEqual(EMPTY_MAP_SELECTION);
    expect(parseMapSelection(null, '77002')).toEqual(EMPTY_MAP_SELECTION);
    expect(parseMapSelection('IL', '6061')).toEqual({ state: 'IL', county: null, zip: null });
    expect(parseMapSelection(' dc ', null).state).toBe('DC');
  });

  it('serialises a selection and keeps every other param', () => {
    const base = new URLSearchParams('segment=itm&lender_relationship=Competitor+customer&zip=99999');
    const drilled = withMapSelection(base, { state: 'TX', county: null, zip: null });
    expect(drilled.get(MAP_STATE_PARAM)).toBe('TX');
    expect(drilled.has(MAP_ZIP_PARAM)).toBe(false);
    expect(drilled.get('segment')).toBe('itm');
    expect(drilled.get('lender_relationship')).toBe('Competitor customer');
    // The input is not mutated.
    expect(base.get(MAP_ZIP_PARAM)).toBe('99999');

    const zip = withMapSelection(drilled, { state: 'TX', county: null, zip: '77002' });
    expect(zip.toString()).toBe('segment=itm&lender_relationship=Competitor+customer&geo_state=TX&zip=77002');
    expect(mapSelectionFromSearch(zip)).toEqual({ state: 'TX', county: null, zip: '77002' });

    const cleared = withMapSelection(zip, EMPTY_MAP_SELECTION);
    expect(cleared.toString()).toBe('segment=itm&lender_relationship=Competitor+customer');
  });

  it('round-trips every selection it can produce', () => {
    for (const selection of [
      EMPTY_MAP_SELECTION,
      { state: 'IL', county: null, zip: null },
      { state: 'WA', county: null, zip: '98101' },
    ]) {
      expect(sameMapSelection(mapSelectionFromSearch(withMapSelection(new URLSearchParams(), selection)), selection)).toBe(true);
    }
  });
});
