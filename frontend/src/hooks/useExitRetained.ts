import { useEffect, useState, type RefObject } from 'react';

/**
 * useExitRetained — keeps the last non-null value alive while a panel plays
 * its CSS exit transition (2026-09-21 audit css-03, TSX half).
 *
 * A panel that renders `{value && ...}` and is closed with `setValue(null)`
 * empties itself in the very commit that starts its slide-out, so it animates
 * out blank. This hook answers "what should the panel still show?": the live
 * value while open, then the last value until the panel element reports the
 * end of its own transition.
 *
 * It deliberately does NOT answer "is the panel open?". Callers keep deriving
 * `open` from the live value, so the focus trap releases and focus returns to
 * the trigger the moment the exit STARTS, not when it ends.
 *
 * The retained value is dropped immediately when there is nothing to wait
 * for: the user prefers reduced motion, or the panel has no running
 * transition (no transition CSS on it, or a DOM without stylesheets such as
 * the Vitest environment). That makes the hook correct whether or not the
 * stylesheet animates the exit.
 */

/** Slack past the computed transition time before giving up on `transitionend`. */
const TRANSITION_END_GRACE_MS = 120;

function parseCssTimes(value: string): number[] {
  return value
    .split(',')
    .map((part) => part.trim())
    .filter((part) => part.length > 0)
    .map((part) => {
      const amount = Number.parseFloat(part);
      if (!Number.isFinite(amount)) return 0;
      return part.endsWith('ms') ? amount : amount * 1000;
    });
}

/** Longest duration + delay across the element's transition list, in ms. */
export function exitTransitionMs(element: HTMLElement): number {
  if (
    typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  ) {
    return 0;
  }
  const style = window.getComputedStyle(element);
  const durations = parseCssTimes(style.transitionDuration ?? '');
  const delays = parseCssTimes(style.transitionDelay ?? '');
  return durations.reduce((longest, duration, index) => {
    if (duration <= 0) return longest;
    const delay = delays.length > 0 ? delays[index % delays.length] : 0;
    return Math.max(longest, duration + Math.max(0, delay));
  }, 0);
}

/** True while a CSS transition on the element itself (not a child) still runs. */
export function hasRunningOwnTransition(element: HTMLElement): boolean {
  if (typeof element.getAnimations !== 'function' || typeof CSSTransition === 'undefined') return false;
  return element
    .getAnimations()
    .some((animation) => animation instanceof CSSTransition && animation.playState === 'running');
}

export function useExitRetained<TValue, TElement extends HTMLElement>(
  value: TValue | null,
  elementRef: RefObject<TElement | null>,
): TValue | null {
  const [retained, setRetained] = useState<TValue | null>(value);
  // Track the live value during render (no effect, no extra paint) so the
  // value being closed is already retained in the commit that closes it.
  if (value !== null && value !== retained) setRetained(value);

  useEffect(() => {
    if (value !== null || retained === null) return undefined;
    const element = elementRef.current;
    const waitMs = element ? exitTransitionMs(element) : 0;
    if (!element || waitMs <= 0) {
      setRetained(null);
      return undefined;
    }

    const release = () => setRetained(null);
    // Child transitions (button hovers, chips) bubble through the panel; only
    // the panel's own transition ends the exit. And only once none of its own
    // transitions still runs: an ENTRY that finished (or was reversed) in the
    // frame the panel closed dispatches its transitionend / transitioncancel
    // after the close, while the exit is just starting (motion-01 Console
    // follow-up; shell-wayfinding.fixture.spec.ts closes right after the entry).
    const onTransitionEnd = (event: Event) => {
      if (event.target === element && !hasRunningOwnTransition(element)) release();
    };
    element.addEventListener('transitionend', onTransitionEnd);
    element.addEventListener('transitioncancel', onTransitionEnd);
    // `transitionend` never fires if the panel is display:none-d or detached
    // mid-flight; never leave a stale source behind a closed panel.
    const fallback = window.setTimeout(release, waitMs + TRANSITION_END_GRACE_MS);
    return () => {
      element.removeEventListener('transitionend', onTransitionEnd);
      element.removeEventListener('transitioncancel', onTransitionEnd);
      window.clearTimeout(fallback);
    };
  }, [elementRef, retained, value]);

  return value ?? retained;
}
