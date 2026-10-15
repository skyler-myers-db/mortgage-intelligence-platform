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

/**
 * How many more deadlines an exit still in flight gets. The deadline is wall
 * clock, but a transition runs on the document timeline, which starts it and
 * reports its end (`transitionend`) only at rendering updates. When those lag
 * (a loaded machine, a throttled or software-composited renderer: Linux
 * WebKit on CI painted no frame for ~430 ms, run 38565698599), the first
 * deadline lands while the exit is pending, mid-flight, or already finished
 * with its `transitionend` not yet dispatched (WebKit then reports the
 * transition `finished`, no longer `running`); releasing there hard-cut the
 * fade, and the removed node's `transitionend` never came. Bounded, so a
 * timeline that never ticks cannot hold a closed panel indefinitely: at most
 * 1 + 4 deadlines.
 */
const MAX_DEADLINE_EXTENSIONS = 4;

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

/** The CSS transitions on the element itself (not a child) that still run. */
function runningOwnTransitions(element: HTMLElement): CSSTransition[] {
  if (typeof element.getAnimations !== 'function' || typeof CSSTransition === 'undefined') return [];
  return element
    .getAnimations()
    .filter((animation): animation is CSSTransition => animation instanceof CSSTransition && animation.playState === 'running');
}

/** True while a CSS transition on the element itself (not a child) still runs. */
export function hasRunningOwnTransition(element: HTMLElement): boolean {
  return runningOwnTransitions(element).length > 0;
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
    // The exit's own transitions: the style flush in exitTransitionMs above
    // created them. Each counts as in flight until the panel reports its
    // end (transitionend / transitioncancel for its property) or it is
    // cancelled without one (`idle`).
    const exitTransitions = runningOwnTransitions(element);
    const reported = new Set<string>();
    const exitInFlight = () =>
      hasRunningOwnTransition(element)
      || exitTransitions.some((transition) => transition.playState !== 'idle' && !reported.has(transition.transitionProperty));
    // Child transitions (button hovers, chips) bubble through the panel; only
    // the panel's own transition ends the exit. And only once none of its own
    // transitions still runs: an ENTRY that finished (or was reversed) in the
    // frame the panel closed dispatches its transitionend / transitioncancel
    // after the close, while the exit is just starting (motion-01 Console
    // follow-up; shell-wayfinding.fixture.spec.ts closes right after the entry).
    const onTransitionEnd = (event: Event) => {
      if (event.target !== element) return;
      reported.add((event as TransitionEvent).propertyName);
      if (!hasRunningOwnTransition(element)) release();
    };
    element.addEventListener('transitionend', onTransitionEnd);
    element.addEventListener('transitioncancel', onTransitionEnd);
    // `transitionend` never fires if the panel is display:none-d or detached
    // mid-flight; never leave a stale source behind a closed panel. Both
    // cancel its transitions, so the deadline releases then; an exit still in
    // flight at the deadline is only late (MAX_DEADLINE_EXTENSIONS).
    let extensions = 0;
    let fallback = 0;
    const onDeadline = () => {
      if (extensions < MAX_DEADLINE_EXTENSIONS && element.isConnected && exitInFlight()) {
        extensions += 1;
        fallback = window.setTimeout(onDeadline, waitMs + TRANSITION_END_GRACE_MS);
        return;
      }
      release();
    };
    fallback = window.setTimeout(onDeadline, waitMs + TRANSITION_END_GRACE_MS);
    return () => {
      element.removeEventListener('transitionend', onTransitionEnd);
      element.removeEventListener('transitioncancel', onTransitionEnd);
      window.clearTimeout(fallback);
    };
  }, [elementRef, retained, value]);

  return value ?? retained;
}

/**
 * useExitPresence — the boolean form of useExitRetained for an overlay that
 * unmounts when it closes (motion-01 item 2e: the filter menus, the account
 * menu, the drawer's recovery frame, the ? sheet). deviation:menu-exit-motion
 *
 * `present` keeps the element rendered until its own exit transition ends
 * (instant under reduced motion and where nothing transitions, such as the
 * test DOM); `closing` is the exit itself. The caller contract:
 *
 *   - render while `present`;
 *   - while `closing`, add `is-closing`, `aria-hidden="true"` and `inert`
 *     (lib/keymap hasOpenOverlay skips aria-hidden subtrees, so a closing
 *     menu never holds the Lead Queue's A / R keys off);
 *   - derive everything else from `open`: listeners, the Escape layer,
 *     aria-expanded / aria-controls / aria-activedescendant and focus
 *     return, so focus returns when the exit STARTS.
 *
 * A reopen during the exit keeps the same element (no duplicate ids).
 */
export function useExitPresence<T extends HTMLElement>(
  open: boolean,
  elementRef: RefObject<T | null>,
): { present: boolean; closing: boolean } {
  const present = useExitRetained(open ? true : null, elementRef) === true;
  return { present, closing: present && !open };
}
