/**
 * The two pure decisions behind the shared listbox (2026-09-21 audit
 * a11y-02 / stack-05): which option a typeahead buffer lands on, and whether
 * an anchored menu opens above its trigger. The rendered behaviour is pinned
 * in FilterSelect.keyboard.test.tsx, MultiFilterSelect.keyboard.test.tsx and
 * filter-listbox.fixture.spec.ts.
 */
import { describe, expect, it } from 'vitest';
import { typeaheadMatch } from './useListboxNavigation';
import { menuPlacement } from './useMenuPlacement';

const LABELS = ['All states', 'Arizona', 'California', 'Colorado', 'Connecticut', 'Texas'];

describe('typeaheadMatch', () => {
  it('finds the first label starting with a letter, case-insensitively', () => {
    expect(typeaheadMatch(LABELS, 'C', -1)).toBe(2);
    expect(typeaheadMatch(LABELS, 't', -1)).toBe(5);
  });

  it('cycles on from the active option for one letter, repeated or not', () => {
    expect(typeaheadMatch(LABELS, 'c', 2)).toBe(3);
    expect(typeaheadMatch(LABELS, 'cc', 3)).toBe(4);
    expect(typeaheadMatch(LABELS, 'ccc', 4)).toBe(2);
  });

  it('refines from the active option (inclusive) for a longer buffer', () => {
    expect(typeaheadMatch(LABELS, 'co', 2)).toBe(3);
    expect(typeaheadMatch(LABELS, 'con', 3)).toBe(4);
    expect(typeaheadMatch(LABELS, 'col', 3)).toBe(3);
    expect(typeaheadMatch(LABELS, 'all s', 0)).toBe(0);
  });

  it('returns -1 when nothing matches or there is nothing to match', () => {
    expect(typeaheadMatch(LABELS, 'zz', 0)).toBe(-1);
    expect(typeaheadMatch([], 'a', -1)).toBe(-1);
    expect(typeaheadMatch(LABELS, '', 0)).toBe(-1);
  });
});

describe('menuPlacement', () => {
  const VIEWPORT = { top: 0, bottom: 900 };

  it('opens below while the menu fits under the trigger, capped at the room below', () => {
    expect(menuPlacement({ top: 100, bottom: 128 }, 280, VIEWPORT)).toEqual({ placement: 'below', space: 768 });
  });

  it('opens above when the menu would cross the bottom edge and there is more room above', () => {
    expect(menuPlacement({ top: 760, bottom: 788 }, 280, VIEWPORT)).toEqual({ placement: 'above', space: 756 });
  });

  it('stays below when neither side fits but below has more room', () => {
    expect(menuPlacement({ top: 200, bottom: 228 }, 600, { top: 0, bottom: 700 })).toEqual({ placement: 'below', space: 468 });
  });

  it('measures room inside the clipping scroller, not the whole viewport', () => {
    // .main starts under the route nav at 112: 248 px above, less than the menu.
    const main = { top: 112, bottom: 456 };
    expect(menuPlacement({ top: 364, bottom: 392 }, 280, main)).toEqual({ placement: 'above', space: 248 });
    // Against the bare viewport the same trigger would claim 360 px above.
    expect(menuPlacement({ top: 364, bottom: 392 }, 280, { top: 0, bottom: 456 }).space).toBe(360);
  });
});
