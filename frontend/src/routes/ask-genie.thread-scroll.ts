import { useEffect, useRef, type RefObject } from 'react';

function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/**
 * Keep the newest exchange of the `/ask-genie` thread in view (audit
 * 2026-09-21 `visual-07`).
 *
 * The thread now reads oldest-first with the composer docked under it, the
 * floating panel's order, so a new question and its answer land at the END
 * of the thread. When that point is off screen (a long thread, the user
 * scrolled up), the question that starts the new exchange is scrolled to the
 * top of the view, once per change of `anchorKey`; the answer then reads
 * down from its question. Nothing moves when the question is already in view,
 * nothing moves on mount (the shell restores scroll on Back), and motion is
 * instant under `prefers-reduced-motion`.
 *
 * `dockRef` is the sticky composer: the part of the view it covers does not
 * count as "in view".
 */
export function useRevealLatestExchange(
  anchorRef: RefObject<HTMLElement | null>,
  dockRef: RefObject<HTMLElement | null>,
  anchorKey: string,
): void {
  const previousKeyRef = useRef(anchorKey);
  useEffect(() => {
    if (previousKeyRef.current === anchorKey) return;
    previousKeyRef.current = anchorKey;
    const anchor = anchorRef.current;
    if (!anchor || typeof anchor.scrollIntoView !== 'function') return;
    // Not laid out (the Ask tab is hidden while another tab shows): a turn
    // the floating panel added must not scroll the tab the user is reading.
    if (anchor.getClientRects().length === 0) return;
    const scroller = anchor.closest<HTMLElement>('.main');
    // The route nav is sticky at the top of `.main`: under it is not in view.
    const nav = scroller?.querySelector<HTMLElement>('.route-nav');
    const viewTop = nav?.getBoundingClientRect().bottom ?? scroller?.getBoundingClientRect().top ?? 0;
    const viewBottom = dockRef.current?.getBoundingClientRect().top ?? window.innerHeight;
    const top = anchor.getBoundingClientRect().top;
    if (top >= viewTop && top + anchor.offsetHeight <= viewBottom) return;
    anchor.scrollIntoView({ block: 'start', behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
  }, [anchorKey, anchorRef, dockRef]);
}
