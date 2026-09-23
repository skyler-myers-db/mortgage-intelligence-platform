import { useLayoutEffect, type RefObject } from 'react';

/** CSS custom property `.tbl-wrap--fill` reads (LeadTable.css). */
export const LEAD_TABLE_FILL_VAR = '--lead-table-fill-block';

/**
 * Size the Lead Queue's table scroller from its OWN top edge (audit flow-01 /
 * tables-04): the scroller, and the surface footer under it, end on the fold
 * of the app's `<main>` scroller instead of a viewport-derived guess that left
 * ~150px of rows, the footer and the horizontal scrollbar below the fold at
 * 1440x900. `.tbl-wrap--fill` floors the value at the prototype's 480px.
 *
 * The value is the space from the scroller's top (in `<main>`'s content box,
 * so it does not change while the page scrolls) to `<main>`'s bottom, minus
 * whatever the table surface renders below the scroller (footer, bulk bar,
 * notices). It is re-measured when `<main>` resizes (viewport, Console) or
 * `.main__inner` does (a filter panel opening, hero chips wrapping). The
 * write happens in the next animation frame, never inside the observer
 * callback, so it cannot start a ResizeObserver loop; setting the same value
 * again is a no-op, so the measurement converges.
 */
export function useLeadTableFillHeight(wrapRef: RefObject<HTMLElement | null>, enabled: boolean): void {
  useLayoutEffect(() => {
    const wrap = wrapRef.current;
    if (!enabled || !wrap) return undefined;
    const main = wrap.closest<HTMLElement>('main');
    const surface = wrap.closest<HTMLElement>('.surface');
    if (!main || !surface) return undefined;

    const measure = () => {
      const wrapBox = wrap.getBoundingClientRect();
      const topInMain = wrapBox.top - main.getBoundingClientRect().top + main.scrollTop;
      const below = surface.getBoundingClientRect().bottom - wrapBox.bottom;
      const available = Math.max(0, Math.floor(main.clientHeight - topInMain - below));
      const next = `${available}px`;
      if (wrap.style.getPropertyValue(LEAD_TABLE_FILL_VAR) !== next) {
        wrap.style.setProperty(LEAD_TABLE_FILL_VAR, next);
      }
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;

    let frame = 0;
    const observer = new ResizeObserver(() => {
      if (frame !== 0) return;
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        measure();
      });
    });
    observer.observe(main);
    const inner = wrap.closest<HTMLElement>('.main__inner');
    if (inner) observer.observe(inner);
    return () => {
      observer.disconnect();
      if (frame !== 0) window.cancelAnimationFrame(frame);
    };
  }, [enabled, wrapRef]);
}
