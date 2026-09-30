import { useLayoutEffect, type RefObject } from 'react';

/** The sticky thead's block size, on `.tbl-wrap` (LeadTable.css). */
export const TABLE_HEAD_BLOCK_VAR = '--tbl-head-block-size';

function writePx(element: HTMLElement, property: string, size: number): void {
  const next = `${Math.ceil(size)}px`;
  if (element.style.getPropertyValue(property) !== next) element.style.setProperty(property, next);
}

/**
 * Keep keyboard focus clear of the ranked-borrower table's sticky thead
 * (audit a11y-v2; WCAG 2.2 SC 2.4.11 Focus Not Obscured, technique C43).
 *
 * The table scroller (`.tbl-wrap`) has a sticky thead at its top and, on the
 * Lead Queue, the pinned Approval column at its end edge. A row control that
 * takes focus from outside the view (Shift+Tab up the rows, Tab across a row
 * to its Approval cell with the Console open) was scrolled only to the
 * scroller's edge, behind one of them. LeadTable.css clears them by their
 * sizes plus the focus ring: the table's focus targets take scroll-margin
 * at the block start (the larger of the thead's size and the sticky route
 * nav's, which 38-focus-clearance.css sets on `.main` at its one-line
 * size), and the scroller takes scroll-padding at the inline end for the
 * pin. The pin's size is its column's declared width (the table is
 * `table-layout: fixed`), so only the thead, whose labels can wrap, is
 * measured here and written as a custom property; the sheet declares a
 * fallback, so nothing depends on this having run.
 *
 * Cost (wave-4b integration): the write is registered `inherits: false`
 * in LeadTable.css, so it re-styles the scroller alone. As an ordinary,
 * inherited custom property, each write re-styled every row of the table,
 * and with the pin written too the cold Lead Queue lost ~340 ms of TBT at
 * 4x CPU (measured by bisect; focus-clearance-cost.fixture.spec.ts pins the
 * re-styled element count). The first measurement is also taken in the
 * next animation frame rather than during the commit, where reading layout
 * forced a synchronous layout before the first paint (+~480 ms LCP).
 *
 * Measured in the next frame after mount and whenever the thead resizes,
 * never inside the ResizeObserver callback (as useLeadTableFillHeight does),
 * so it cannot start a resize loop. The property is removed on unmount.
 *
 * @param layoutKey re-observes when the columns change (the view preset
 *   re-renders the header cells).
 */
export function useTableScrollClearance(wrapRef: RefObject<HTMLElement | null>, layoutKey: string): void {
  useLayoutEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return undefined;
    const head = wrap.querySelector<HTMLElement>('thead');
    if (!head) return undefined;

    const measure = () => writePx(wrap, TABLE_HEAD_BLOCK_VAR, head.getBoundingClientRect().height);
    let frame = 0;
    const schedule = () => {
      if (frame !== 0) return;
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        measure();
      });
    };
    schedule();

    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(schedule);
    observer?.observe(head);
    return () => {
      observer?.disconnect();
      if (frame !== 0) window.cancelAnimationFrame(frame);
      wrap.style.removeProperty(TABLE_HEAD_BLOCK_VAR);
    };
  }, [wrapRef, layoutKey]);
}
