/**
 * @vitest-environment happy-dom
 */
import { act, createRef } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { USChoroplethMapTooltip } from './USChoroplethMapTooltip';
import type { MapCard } from './USChoroplethMap.hover';

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

  function render(overrides: Partial<MapCard>) {
    const card: MapCard = {
      name: 'Illinois',
      count: 1851040,
      avgScore: 38,
      ...overrides,
    };
    act(() => root.render(
      <USChoroplethMapTooltip card={card} unitKey="state:il" tipRef={createRef()} placeTip={() => {}} activeSegNames={null} />,
    ));
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

  // wow-stage-1 / D-dataviz-geo-b: change versus today as labelled numbers.
  const rows = () => [...document.querySelectorAll('.map-tip__row')].map((row) => (row.textContent ?? '').replace(/\s+/g, ' ').trim());

  it('lists the scenario rows away from step 0, then the cohort note in a muted row', () => {
    render({ scenario: { step: -50, ratePct: 5.8, today: 1_000, atStep: 1_300, change: 300, contactableAtStep: 90 } });
    const text = rows();
    const at = text.indexOf('In the money today1,000');
    expect(at).toBeGreaterThanOrEqual(0);
    expect(text.slice(at, at + 5)).toEqual([
      'In the money today1,000',
      'In the money at 5.80%1,300',
      'Change+300',
      'Contactable in the money at 5.80%90',
      "The Lead Queue and campaigns use today's par rate.",
    ]);
    const note = [...document.querySelectorAll('.map-tip__row')][at + 4];
    expect(note.classList.contains('map-tip__row--muted')).toBe(true);
  });

  it('shows only today and the note at step 0, and no contactable row when it is not reported', () => {
    render({ scenario: { step: 0, ratePct: 6.3, today: 1_000, atStep: 1_000, change: 0, contactableAtStep: null } });
    const text = rows();
    const at = text.indexOf('In the money today1,000');
    expect(text.slice(at)).toContain("The Lead Queue and campaigns use today's par rate.");
    expect(text.some((row) => row.startsWith('Change'))).toBe(false);
    expect(text.some((row) => row.startsWith('In the money at'))).toBe(false);
    expect(text.some((row) => row.startsWith('Contactable in the money'))).toBe(false);
  });

  it('a card without a scenario has no scenario rows', () => {
    render({});
    expect(rows().some((row) => row.startsWith('In the money'))).toBe(false);
  });
});

/**
 * deviation:map-tip-top-layer (css-03 / runtime-07). The tip is a manual
 * popover promoted to the top layer, so the floating Genie panel or the
 * drawer never covers it, and it is placed BEFORE it is promoted so it never
 * paints at the previous unit's point. happy-dom has no Popover API, so the
 * test defines the method the component feature-detects.
 */
describe('USChoroplethMapTooltip top layer (D-dataviz-geo-d1)', () => {
  let root: Root;
  let calls: string[];
  const proto = HTMLElement.prototype as HTMLElement & { showPopover?: () => void };
  const hadShowPopover = Object.prototype.hasOwnProperty.call(proto, 'showPopover');
  const originalShowPopover = proto.showPopover;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    calls = [];
    proto.showPopover = vi.fn(function showPopover(this: HTMLElement) {
      calls.push(`show@${this.style.left}`);
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    if (hadShowPopover) proto.showPopover = originalShowPopover;
    else Reflect.deleteProperty(proto, 'showPopover');
  });

  const card: MapCard = { name: 'Texas', count: 2148, avgScore: 82 };

  it('renders a manual popover in document.body, placed before it is promoted', () => {
    const tipRef = createRef<HTMLDivElement>();
    let left = 300;
    const placeTip = (element: HTMLElement) => {
      calls.push(`place@${left}px`);
      element.style.left = `${left}px`;
    };
    act(() => root.render(
      <USChoroplethMapTooltip card={card} unitKey="state:tx" tipRef={tipRef} placeTip={placeTip} activeSegNames={null} />,
    ));
    const tip = document.querySelector<HTMLElement>('.map-tip');
    expect(tip?.getAttribute('popover')).toBe('manual');
    expect(tip?.parentElement).toBe(document.body);
    expect(tipRef.current).toBe(tip);
    // Placement first, then the top layer, at the placed point.
    expect(calls).toEqual(['place@300px', 'show@300px']);

    // Another unit re-places the open tip; the same unit does not.
    left = 420;
    act(() => root.render(
      <USChoroplethMapTooltip card={{ ...card, name: 'Illinois' }} unitKey="state:il" tipRef={tipRef} placeTip={placeTip} activeSegNames={null} />,
    ));
    expect(calls.slice(2)).toEqual(['place@420px', 'show@420px']);
    act(() => root.render(
      <USChoroplethMapTooltip card={{ ...card, name: 'Illinois' }} unitKey="state:il" tipRef={tipRef} placeTip={placeTip} activeSegNames={null} />,
    ));
    expect(calls).toHaveLength(4);
    expect(document.querySelector('.map-tip__name')?.textContent).toBe('Illinois');
  });

});
