/**
 * Mortgage synonyms in the command palette registry (audit 2026-09-21
 * `shell-07`, vocabulary part): "refi" used to match nothing. The words a
 * lender types are keywords, matched before any fuzzy scoring, and they land
 * on the page that owns the concept.
 */
import { describe, expect, it } from 'vitest';
import { filterCommandActions } from './commandActions';

const ids = (query: string) => filterCommandActions(query).map((action) => action.id);

describe('command palette mortgage synonyms', () => {
  it.each([
    ['refi', ['nav-segments', 'nav-offer']],
    ['refinance', ['nav-segments', 'nav-offer']],
    ['cash-out', ['nav-segments', 'nav-offer']],
    ['cash out', ['nav-segments', 'nav-offer']],
    ['HELOC', ['nav-segments', 'nav-offer', 'nav-leads']],
    ['recapture', ['nav-segments', 'nav-offer']],
    ['in the money', ['nav-segments', 'nav-leads']],
    ['listed', ['nav-segments']],
  ])('"%s" resolves to the pages that own the concept', (query, expected) => {
    const matched = ids(query);
    expect(matched.length, query).toBeGreaterThan(0);
    for (const id of expected) expect(matched, `${query} -> ${id}`).toContain(id);
  });

  it('"refi" surfaces Segment Intelligence first, the page that owns the refi segments', () => {
    expect(ids('refi')[0]).toBe('nav-segments');
  });

  it('a made-up word still matches nothing', () => {
    expect(ids('zyrplax')).toEqual([]);
  });
});
