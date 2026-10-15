import { useLayoutEffect, useState, type RefObject } from 'react';

/**
 * Whether a scroll container overflows its own box, for a `role="region"`
 * scroller that should take a tab stop only while it actually scrolls (axe
 * scrollable-region-focusable, WCAG 2.1.1; W5e a11y-05). A shell panel with
 * nothing to scroll gets no needless tab stop.
 *
 * Measures `scrollHeight > clientHeight + 1` (the 1px absorbs sub-pixel
 * rounding) before paint, again whenever `contentKey` changes (pass the row
 * count: rows added inside a fixed max-height do not resize the panel), and
 * on the panel's own resize through one ResizeObserver. State changes only
 * on a flip, so a resize storm renders nothing.
 */
export function useOverflowFocusable(ref: RefObject<HTMLElement | null>, contentKey: unknown): boolean {
  const [overflowing, setOverflowing] = useState(false);
  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return undefined;
    const measure = () => {
      const next = node.scrollHeight > node.clientHeight + 1;
      setOverflowing((current) => (current === next ? current : next));
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [ref, contentKey]);
  return overflowing;
}
