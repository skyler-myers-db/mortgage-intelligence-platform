import { useEffect, useLayoutEffect, useRef, type RefObject } from 'react';
import { useLocation, useNavigationType } from 'react-router';
import { retryUntil } from './retryUntil';
import { readOffsets, rememberOffsetCapped, scrollStorageKey, writeOffsets } from './scrollOffsetStore';

export { scrollStorageKey };

/**
 * useMainScroll — scroll continuity for the persistent `.main` scroller.
 *
 * Audit 2026-09-21 (`shell-03`, `stack-03`, `runtime-08`, `motion-v1`):
 * `<main className="main">` is the app's scroll container and it never
 * remounts, so its offset leaked across routes. Reproduced in a browser: Home
 * scrolled to 689 then Analytics opened at 570; Back to the Lead Queue returned
 * 0 instead of 770; `/glossary#clip` never scrolled on client navigation.
 * The app is a RouterProvider data router now, but `<ScrollRestoration>`
 * restores only the WINDOW's scroll, and the window never scrolls here: `.main`
 * does. So this hook does that job for the one element (and the Lead Queue's
 * table scroller has its own, useLeadTableScroll).
 *
 * Rules, keyed on the history entry (`location.key`):
 *   - POP (Back / Forward / reload) restores the offset saved for that entry,
 *     waiting (bounded) until the content is tall enough to reach it.
 *   - A `#hash` scrolls its target under the sticky route nav, honouring the
 *     target's CSS `scroll-margin-top`.
 *   - PUSH / REPLACE to a DIFFERENT pathname resets to the top.
 *   - PUSH / REPLACE on the SAME pathname keeps the offset: Analytics, Segment
 *     Intelligence, Portfolio Builder and the Lead Queue write their filters to
 *     the URL with `setSearchParams`, and toggling a filter half-way down a
 *     page must not throw the reader back to the top.
 *
 * Offsets live in a Map mirrored to sessionStorage (so a reload restores too),
 * capped at `MAX_ENTRIES`. Values are plain numbers keyed by the router's
 * random entry key; the first-load entry (key "default") is keyed by a
 * non-reversible fingerprint of its URL so no borrower id is written to storage
 * (hooks/scrollOffsetStore.ts, shared with the table scroller).
 */

export const MAIN_SCROLL_STORAGE_KEY = 'mip.mainScroll.v1';
const MAX_ENTRIES = 50;
/** How long a restore / hash scroll waits for lazy content before giving up. */
export const MAIN_SCROLL_DEADLINE_MS = 3000;
const STICKY_NAV_SELECTOR = '.route-nav';
const USER_SCROLL_INTENT_EVENTS = ['wheel', 'touchstart', 'pointerdown', 'keydown'] as const;

/** The `.main` offsets, under this hook's own storage key and cap. */
export function readStoredOffsets(): Map<string, number> {
  return readOffsets(MAIN_SCROLL_STORAGE_KEY, MAX_ENTRIES);
}

function writeStoredOffsets(offsets: ReadonlyMap<string, number>): void {
  writeOffsets(MAIN_SCROLL_STORAGE_KEY, offsets);
}

/** Most-recently-used insert with the `.main` size cap. */
export function rememberOffset(offsets: Map<string, number>, key: string, value: number): void {
  rememberOffsetCapped(offsets, key, value, MAX_ENTRIES);
}

function hashTargetId(hash: string): string {
  const raw = hash.startsWith('#') ? hash.slice(1) : hash;
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

function cssScrollMarginTop(target: Element): number {
  const value = Number.parseFloat(window.getComputedStyle(target).scrollMarginTop);
  return Number.isFinite(value) ? value : 0;
}

/**
 * Distance from the top of `main`'s scrollable content to `target`, read from
 * the `offsetTop` chain. Unlike `getBoundingClientRect()` this ignores
 * transforms, which matters here: a new route enters with `route-in`
 * (`translateY(4px)` → 0), so a rect read at navigation time is 4px off its
 * resting place. Returns null when `main` is not in the target's offsetParent
 * chain (it is `position: relative` in the shell, so it normally is).
 */
function offsetTopWithin(main: HTMLElement, target: HTMLElement): number | null {
  let top = 0;
  let node: HTMLElement | null = target;
  while (node && node !== main) {
    top += node.offsetTop;
    node = node.offsetParent instanceof HTMLElement ? node.offsetParent : null;
  }
  return node === main ? top : null;
}

/**
 * Scroll `main` so the `#hash` target sits just under the sticky route nav.
 * Returns false while the target is not in the DOM yet (lazy route). Targets
 * outside `main` (the skip links' `#main-content` / `#workspace-console`) are
 * left to the browser's native fragment behaviour.
 */
function scrollHashTargetIntoView(main: HTMLElement, hash: string): boolean {
  const id = hashTargetId(hash);
  if (!id) return true;
  const target = document.getElementById(id);
  if (!target) return false;
  if (target === main || !main.contains(target)) return true;
  const nav = main.querySelector(STICKY_NAV_SELECTOR);
  const navHeight = nav ? nav.getBoundingClientRect().height : 0;
  const margin = Math.max(cssScrollMarginTop(target), navHeight);
  const offset =
    offsetTopWithin(main, target) ??
    main.scrollTop + target.getBoundingClientRect().top - main.getBoundingClientRect().top;
  main.scrollTop = Math.max(0, offset - margin);
  return true;
}

export function useMainScroll(mainRef: RefObject<HTMLElement | null>): void {
  const location = useLocation();
  const navigationType = useNavigationType();
  const { pathname, hash } = location;
  const storageKey = scrollStorageKey(location);

  const offsetsRef = useRef<Map<string, number> | null>(null);
  const activeKeyRef = useRef<string | null>(null);
  const previousPathnameRef = useRef<string | null>(null);
  /** True while a restore is waiting for content: ignore our own clamped scrolls. */
  const restoringRef = useRef(false);
  const cancelPendingRef = useRef<(() => void) | null>(null);

  // Record the live offset against the current history entry, and let real
  // user scroll intent cancel a restore that is still waiting for content.
  //
  // `scroll` alone is not a reliable record of where the user left: it is
  // delivered on the next rendering opportunity, so the last movement before a
  // click can be missing, and a throttled or background tab delivers none at
  // all (seen in a real browser: scrollTop 2200, zero scroll events, 451
  // restored). So the offset is also captured at the moments a navigation can
  // begin, while the outgoing route is still in the DOM: any click (capture
  // phase, before a link's handler navigates), `popstate` (Back / Forward:
  // React has not committed the new route yet) and `pagehide` (reload).
  useEffect(() => {
    const main = mainRef.current;
    if (!main) return undefined;
    const offsets = (offsetsRef.current ??= readStoredOffsets());
    const capture = () => {
      const key = activeKeyRef.current;
      if (key === null || restoringRef.current) return;
      rememberOffset(offsets, key, main.scrollTop);
    };
    const onUserIntent = () => {
      cancelPendingRef.current?.();
    };
    const onPageHide = () => {
      capture();
      writeStoredOffsets(offsets);
    };
    main.addEventListener('scroll', capture, { passive: true });
    for (const type of USER_SCROLL_INTENT_EVENTS) {
      main.addEventListener(type, onUserIntent, { passive: true });
    }
    window.addEventListener('click', capture, { capture: true, passive: true });
    window.addEventListener('popstate', capture);
    window.addEventListener('pagehide', onPageHide);
    return () => {
      main.removeEventListener('scroll', capture);
      for (const type of USER_SCROLL_INTENT_EVENTS) {
        main.removeEventListener(type, onUserIntent);
      }
      window.removeEventListener('click', capture, { capture: true });
      window.removeEventListener('popstate', capture);
      window.removeEventListener('pagehide', onPageHide);
    };
  }, [mainRef]);

  // Layout effect so the offset is final before paint (and before any future
  // view-transition snapshot).
  useLayoutEffect(() => {
    const main = mainRef.current;
    if (!main) return undefined;
    const offsets = (offsetsRef.current ??= readStoredOffsets());
    const previousPathname = previousPathnameRef.current;
    previousPathnameRef.current = pathname;
    activeKeyRef.current = storageKey;
    const pathnameChanged = previousPathname !== null && previousPathname !== pathname;
    const saved = navigationType === 'POP' ? offsets.get(storageKey) : undefined;

    const settle = () => {
      restoringRef.current = false;
      cancelPendingRef.current = null;
      rememberOffset(offsets, storageKey, main.scrollTop);
      writeStoredOffsets(offsets);
    };

    let cancelRetry: () => void = () => undefined;
    const begin = (attempt: () => boolean, onExpire?: () => void) => {
      restoringRef.current = true;
      let finished = false;
      const finishOnce = () => {
        if (finished) return;
        finished = true;
        settle();
      };
      cancelRetry = retryUntil(
        () => {
          if (!attempt()) return false;
          finishOnce();
          return true;
        },
        main,
        MAIN_SCROLL_DEADLINE_MS,
        () => {
          onExpire?.();
          finishOnce();
        },
      );
      if (!finished) {
        cancelPendingRef.current = () => {
          cancelRetry();
          finishOnce();
        };
      }
    };

    if (saved !== undefined) {
      begin(
        () => {
          if (main.scrollHeight - main.clientHeight < saved) return false;
          main.scrollTop = saved;
          return true;
        },
        // Content never grew tall enough: go as far as it allows.
        () => {
          main.scrollTop = saved;
        },
      );
    } else {
      if (pathnameChanged) main.scrollTop = 0;
      if (hash) begin(() => scrollHashTargetIntoView(main, hash));
      else settle();
    }

    return () => {
      cancelRetry();
      restoringRef.current = false;
      cancelPendingRef.current = null;
    };
  }, [mainRef, storageKey, pathname, hash, navigationType]);
}
