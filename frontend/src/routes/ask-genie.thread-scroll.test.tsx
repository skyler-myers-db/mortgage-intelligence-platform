// @vitest-environment happy-dom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { latestAnchorInView } from './ask-genie.thread-scroll';

/**
 * Where the view starts for "is the latest question in view" (visual-07):
 * under the route nav only while it is docked (sticky, report 12.4 #5). A nav
 * in flow scrolls away with the page, so its bottom can be far above the
 * scroller (negative): the view then starts at the scroller's top, or an
 * anchor scrolled up under the topbar would read as in view and never be
 * brought back.
 */

const rects = new Map<string, { top: number; bottom: number }>();

function build(docked: boolean): { anchor: HTMLElement; scroller: HTMLElement } {
  document.body.innerHTML = `<main class="main"><nav class="route-nav"${docked ? ' data-docked=""' : ''}></nav><p class="anchor"></p></main>`;
  return {
    anchor: document.querySelector<HTMLElement>('.anchor') as HTMLElement,
    scroller: document.querySelector<HTMLElement>('.main') as HTMLElement,
  };
}

describe('latestAnchorInView', () => {
  beforeEach(() => {
    rects.clear();
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function rect(this: HTMLElement) {
      const box = rects.get(this.className) ?? { top: 0, bottom: 0 };
      return { ...box, left: 0, right: 0, width: 0, height: box.bottom - box.top, x: 0, y: box.top } as DOMRect;
    });
    vi.spyOn(HTMLElement.prototype, 'getClientRects').mockImplementation(() => [{}] as unknown as DOMRectList);
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockImplementation(() => 40);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    document.body.innerHTML = '';
  });

  it('an undocked nav scrolled away does not stretch the view above the scroller top', () => {
    const { anchor } = build(false);
    rects.set('main', { top: 56, bottom: 900 });
    rects.set('route-nav', { top: -97, bottom: -40 });
    // The question sits under the topbar, above the scroller's top edge.
    rects.set('anchor', { top: 10, bottom: 50 });
    expect(latestAnchorInView(anchor, null)).toBe(false);
    rects.set('anchor', { top: 60, bottom: 100 });
    expect(latestAnchorInView(anchor, null)).toBe(true);
  });

  it('a docked nav covers the top of the view: under it is not in view, below it is', () => {
    const { anchor } = build(true);
    rects.set('main', { top: 56, bottom: 900 });
    rects.set('route-nav', { top: 56, bottom: 113 });
    rects.set('anchor', { top: 80, bottom: 120 });
    expect(latestAnchorInView(anchor, null)).toBe(false);
    rects.set('anchor', { top: 120, bottom: 160 });
    expect(latestAnchorInView(anchor, null)).toBe(true);
  });

  it('is null while the anchor is not laid out (a hidden tab)', () => {
    const { anchor } = build(true);
    vi.spyOn(HTMLElement.prototype, 'getClientRects').mockImplementation(() => [] as unknown as DOMRectList);
    expect(latestAnchorInView(anchor, null)).toBeNull();
  });
});
