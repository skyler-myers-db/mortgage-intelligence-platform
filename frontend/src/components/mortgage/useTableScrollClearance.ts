import { useLayoutEffect, type RefObject } from 'react';

/** The sticky thead's block size, on `.tbl-wrap` (LeadTable.css). */
export const TABLE_HEAD_BLOCK_VAR = '--tbl-head-block-size';
/** The pinned Approval column's inline size, on `.tbl-wrap` (LeadTable.css). */
export const TABLE_PIN_INLINE_VAR = '--tbl-pin-inline-size';

function writePx(element: HTMLElement, property: string, size: number): void {
  const next = `${Math.ceil(size)}px`;
  if (element.style.getPropertyValue(property) !== next) element.style.setProperty(property, next);
}

/**
 * Keep keyboard focus clear of the ranked-borrower table's sticky chrome
 * (audit a11y-v2; WCAG 2.2 SC 2.4.11 Focus Not Obscured, technique C43).
 *
 * The table scroller (`.tbl-wrap`) has a sticky thead at its top and, on the
 * Lead Queue, the pinned Approval column at its end edge. A row control that
 * takes focus from outside the view (Shift+Tab up the rows, Tab across a row
 * to its Approval cell with the Console open) was scrolled only to the
 * scroller's edge, behind one of them. LeadTable.css gives the scroller
 * scroll-padding of these sizes plus the focus ring, so focus scrolling
 * stops clear of them; this hook measures the real sizes (the header wraps,
 * the pin follows its column width) and writes them as custom properties.
 * Each is declared with a fallback in that sheet, so nothing depends on this
 * having run. (The sticky route nav above `.main` is cleared by
 * 38-focus-clearance.css at its one-line size, on every route.)
 *
 * Measured on mount and whenever one of them resizes. The write happens in
 * the next animation frame, never inside the ResizeObserver callback (as
 * useLeadTableFillHeight does), so it cannot start a resize loop. Both
 * properties are removed on unmount.
 *
 * @param layoutKey re-observes when the columns change (the view preset
 *   re-renders the header cells).
 */
export function useTableScrollClearance(wrapRef: RefObject<HTMLElement | null>, layoutKey: string): void {
  useLayoutEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return undefined;
    const head = wrap.querySelector<HTMLElement>('thead');
    const pin = wrap.querySelector<HTMLElement>('.lead-table__approval-header');

    const measure = () => {
      if (head) writePx(wrap, TABLE_HEAD_BLOCK_VAR, head.getBoundingClientRect().height);
      if (pin) writePx(wrap, TABLE_PIN_INLINE_VAR, pin.getBoundingClientRect().width);
    };
    measure();

    let frame = 0;
    const observer = typeof ResizeObserver === 'undefined'
      ? null
      : new ResizeObserver(() => {
          if (frame !== 0) return;
          frame = window.requestAnimationFrame(() => {
            frame = 0;
            measure();
          });
        });
    for (const target of [head, pin]) {
      if (target) observer?.observe(target);
    }
    return () => {
      observer?.disconnect();
      if (frame !== 0) window.cancelAnimationFrame(frame);
      wrap.style.removeProperty(TABLE_HEAD_BLOCK_VAR);
      wrap.style.removeProperty(TABLE_PIN_INLINE_VAR);
    };
  }, [wrapRef, layoutKey]);
}
