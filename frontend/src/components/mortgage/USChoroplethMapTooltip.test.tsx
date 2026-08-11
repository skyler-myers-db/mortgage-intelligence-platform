/**
 * @vitest-environment happy-dom
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { USChoroplethMapTooltip } from './USChoroplethMapTooltip';
import type { HoverState } from './USChoroplethMap.utils';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * Addressable-vs-contactable disclosure on the map tile.
 *
 * The tooltip's "Marketable borrowers" KPI is the addressable population.
 * The Lead Queue the tile links to applies the contact-eligibility
 * predicate, so it shows a strict subset — live 2026-08-11, IL was 76,711
 * of 1,851,040 (24x). Both numbers were correct and nothing stated the
 * relationship, so the click read as a broken link. Same idiom as
 * `.zip-tiles__reconcile`, which discloses the ZIP drill's gap.
 */
describe('USChoroplethMapTooltip contactable disclosure', () => {
  let root: Root;
  let host: HTMLElement;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    host = document.getElementById('root') as HTMLElement;
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
  });

  function render(overrides: Partial<HoverState>) {
    const hover: HoverState = {
      x: 100,
      y: 100,
      name: 'Illinois',
      count: 1851040,
      avgScore: 38,
      ...overrides,
    };
    act(() => root.render(<USChoroplethMapTooltip hover={hover} activeSegNames={null} />));
  }

  function tipText(): string {
    return (document.querySelector('.map-tip')?.textContent ?? '').replace(/\s+/g, ' ');
  }

  it('states the contactable subset against the tile headline', () => {
    render({ contactable: 76711 });
    const text = tipText();
    expect(text).toContain('Contactable');
    // The relationship, not two loose numbers: the reader has to be able to
    // see that the queue behind this tile is the smaller of the two.
    expect(text).toContain('76,711 of 1,851,040');
  });

  it('says nothing when the rollup does not report contactable', () => {
    // An older payload must read "not reported", never a fabricated zero —
    // "0 of 1,851,040" would claim nobody in the state can be contacted.
    render({ contactable: null });
    expect(tipText()).not.toContain('Contactable');
  });

  it('renders zero contactable honestly when that is the real number', () => {
    render({ count: 25, contactable: 0 });
    expect(tipText()).toContain('0 of 25');
  });

  it('omits the disclosure when the tile has no count to relate it to', () => {
    // Outside the footprint the KPI already renders "—"; a lone contactable
    // number with nothing to compare against is worse than silence.
    render({ count: null, contactable: 0 });
    expect(tipText()).not.toContain('Contactable');
  });

  it('says nothing when contactable equals addressable', () => {
    // Reachable with NO user action: Segment Intelligence defaults
    // Contactability to "Eligible only", so the filtered state rollup counts
    // contactable over an already-eligible universe and every tile returns
    // contactable === addressable (live: IL 76,711 of 76,711). The four cases
    // above cover non-equal / null / zero / no-count and so could not see it.
    render({ count: 76711, contactable: 76711 });
    expect(tipText()).not.toContain('Contactable');
  });

});
