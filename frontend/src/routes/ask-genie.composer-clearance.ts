import { useEffect, type RefObject } from 'react';

/** Read by routes/ask-genie.css for `.main`'s scroll-padding-block-end. */
export const COMPOSER_BLOCK_SIZE_PROPERTY = '--genie-composer-block-size';

/**
 * Keep keyboard focus clear of the docked composer on `/ask-genie` (audit
 * 2026-09-21 `visual-07`; WCAG 2.2 SC 2.4.11 Focus Not Obscured, technique
 * C43).
 *
 * `.main`, the app's scroller, has two sticky bars while the Ask tab shows:
 * the route nav at its top (while it is docked) and the docked composer at
 * its bottom. A control that takes focus from outside the view (the answer's
 * feedback buttons and the suggestion chips below it, a table row above it
 * on Shift+Tab) is scrolled only as far as the scroller's edge, which leaves
 * it entirely behind one of them. routes/ask-genie.css gives `.main`
 * scroll-padding of both sizes while the Ask tab shows, so focus scrolling
 * stops between the bars instead.
 *
 * The composer grows from two lines to eight, so it is measured rather than
 * assumed, and re-measured when it resizes. The nav is measured by the shell
 * (hooks/useRouteNavDock writes --route-nav-block on `.main` and marks the
 * nav docked), so this hook measures the composer only. The property is
 * written on the closest `.main` and removed on unmount. A hidden composer
 * (another tab shows) measures 0 and is not written; the rule is off then
 * anyway.
 */
export function useComposerScrollClearance(dockRef: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const dock = dockRef.current;
    const scroller = dock?.closest<HTMLElement>('.main');
    if (!dock || !scroller) return undefined;
    const write = () => {
      const size = dock.offsetHeight;
      if (size > 0) scroller.style.setProperty(COMPOSER_BLOCK_SIZE_PROPERTY, `${size}px`);
    };
    write();
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(write) : null;
    observer?.observe(dock);
    return () => {
      observer?.disconnect();
      scroller.style.removeProperty(COMPOSER_BLOCK_SIZE_PROPERTY);
    };
  }, [dockRef]);
}
