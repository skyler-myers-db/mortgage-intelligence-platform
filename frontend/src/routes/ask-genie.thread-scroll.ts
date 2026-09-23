import { useEffect, useRef, type RefObject } from 'react';
import { useLocation, useNavigationType } from 'react-router';
import { usePrefersReducedMotion } from '../lib/usePrefersReducedMotion';

/**
 * Whether the question that starts the latest exchange sits between the
 * sticky route nav at the top of `.main` and the docked composer at its
 * bottom. Under either bar is not in view. Null when the anchor is not laid
 * out (the Ask tab is hidden while another tab shows), so nothing may scroll.
 */
function latestAnchorInView(
  anchor: HTMLElement,
  dock: HTMLElement | null,
): boolean | null {
  if (anchor.getClientRects().length === 0) return null;
  const scroller = anchor.closest<HTMLElement>('.main');
  const nav = scroller?.querySelector<HTMLElement>('.route-nav');
  const viewTop = nav?.getBoundingClientRect().bottom ?? scroller?.getBoundingClientRect().top ?? 0;
  const viewBottom = dock?.getBoundingClientRect().top ?? window.innerHeight;
  const top = anchor.getBoundingClientRect().top;
  return top >= viewTop && top + anchor.offsetHeight <= viewBottom;
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
 * nothing moves on mount (useRevealLatestOnArrival owns that), and motion is
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
  const reducedMotion = usePrefersReducedMotion();
  useEffect(() => {
    if (previousKeyRef.current === anchorKey) return;
    previousKeyRef.current = anchorKey;
    const anchor = anchorRef.current;
    if (!anchor || typeof anchor.scrollIntoView !== 'function') return;
    // A turn the floating panel added must not scroll the tab the user is
    // reading: a hidden Ask tab reports null.
    if (latestAnchorInView(anchor, dockRef.current) !== false) return;
    anchor.scrollIntoView({ block: 'start', behavior: reducedMotion ? 'auto' : 'smooth' });
  }, [anchorKey, anchorRef, dockRef, reducedMotion]);
}

/**
 * Arriving on `/ask-genie` by a link with a thread already in the shared
 * store (one started in the floating panel, or here earlier) opens on its
 * latest exchange, as the floating panel does, instead of on the oldest turn
 * at the top of a thread that reads oldest-first.
 *
 * Only on arrival by PUSH / REPLACE without a `#hash`: Back, Forward and
 * reload (POP) keep the offset the shell's useMainScroll restores, and a hash
 * is its to scroll to. Only when the latest question is not already in view
 * (a one-turn thread opens with its hero and tabs showing), and only when the
 * Ask tab is the one laid out. Instant: this is where the page opens, not a
 * movement the reader follows.
 *
 * A passive effect on purpose: useMainScroll resets `.main` to the top in a
 * layout effect of the shell, which runs after this route's layout effects,
 * so a layout effect here would be undone.
 */
export function useRevealLatestOnArrival(
  anchorRef: RefObject<HTMLElement | null>,
  dockRef: RefObject<HTMLElement | null>,
): void {
  const navigationType = useNavigationType();
  const { hash } = useLocation();
  // How the route was ARRIVED at: later tab switches change neither.
  const arrivalRef = useRef({ navigationType, hash });
  useEffect(() => {
    const arrival = arrivalRef.current;
    if (arrival.navigationType === 'POP' || arrival.hash) return;
    const anchor = anchorRef.current;
    if (!anchor || typeof anchor.scrollIntoView !== 'function') return;
    if (latestAnchorInView(anchor, dockRef.current) !== false) return;
    anchor.scrollIntoView({ block: 'start', behavior: 'auto' });
  }, [anchorRef, dockRef]);
}
