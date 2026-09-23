import { useEffect, type RefObject } from 'react';

/** Read by routes/ask-genie.css for `.main`'s scroll-padding-block-end. */
export const COMPOSER_BLOCK_SIZE_PROPERTY = '--genie-composer-block-size';
/** Read by routes/ask-genie.css for `.main`'s scroll-padding-block-start. */
export const ROUTE_NAV_BLOCK_SIZE_PROPERTY = '--genie-route-nav-block-size';

/**
 * Keep keyboard focus clear of the sticky bars on `/ask-genie` (audit
 * 2026-09-21 `visual-07`; WCAG 2.2 SC 2.4.11 Focus Not Obscured, technique
 * C43).
 *
 * `.main`, the app's scroller, has two sticky bars while the Ask tab shows:
 * the route nav at its top and the docked composer at its bottom. A control
 * that takes focus from outside the view (the answer's feedback buttons and
 * the suggestion chips below it, a table row above it on Shift+Tab) is
 * scrolled only as far as the scroller's edge, which leaves it entirely
 * behind one of them. routes/ask-genie.css gives `.main` scroll-padding of
 * these two sizes while the Ask tab shows, so focus scrolling stops between
 * the bars instead.
 *
 * Both bars change size (the composer grows from two lines to eight, the nav
 * wraps on narrow screens), so they are measured rather than assumed, and
 * re-measured when they resize. The properties are written on the closest
 * `.main` and removed on unmount. A hidden composer (another tab shows)
 * measures 0 and is not written; the rule is off then anyway.
 */
export function useComposerScrollClearance(dockRef: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const dock = dockRef.current;
    const scroller = dock?.closest<HTMLElement>('.main');
    if (!dock || !scroller) return undefined;
    const nav = scroller.querySelector<HTMLElement>('.route-nav');
    const bars: ReadonlyArray<[HTMLElement, string]> = nav
      ? [
          [dock, COMPOSER_BLOCK_SIZE_PROPERTY],
          [nav, ROUTE_NAV_BLOCK_SIZE_PROPERTY],
        ]
      : [[dock, COMPOSER_BLOCK_SIZE_PROPERTY]];
    const write = () => {
      for (const [bar, property] of bars) {
        const size = bar.offsetHeight;
        if (size > 0) scroller.style.setProperty(property, `${size}px`);
      }
    };
    write();
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(write) : null;
    for (const [bar] of bars) observer?.observe(bar);
    return () => {
      observer?.disconnect();
      for (const [, property] of bars) scroller.style.removeProperty(property);
    };
  }, [dockRef]);
}
