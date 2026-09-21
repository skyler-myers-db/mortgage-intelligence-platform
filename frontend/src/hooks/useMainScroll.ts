import { useEffect, useLayoutEffect, useRef, type RefObject } from 'react';
import { useLocation, useNavigationType, type Location } from 'react-router';
import { retryUntil } from './retryUntil';

/**
 * useMainScroll — scroll continuity for the persistent `.main` scroller.
 *
 * Audit 2026-09-21 (`shell-03`, `stack-03`, `runtime-08`, `motion-v1`):
 * `<main className="main">` is the app's scroll container and it never
 * remounts, so its offset leaked across routes. Reproduced in a browser: Home
 * scrolled to 689 then Analytics opened at 570; Back to the Lead Queue returned
 * 0 instead of 770; `/glossary#clip` never scrolled on client navigation.
 * `<ScrollRestoration>` is Data-router only and the app is deliberately
 * Declarative, so this hook does the same job for one element.
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
 * non-reversible fingerprint of its URL so no borrower id is written to storage.
 */

export const MAIN_SCROLL_STORAGE_KEY = 'mip.mainScroll.v1';
const MAX_ENTRIES = 50;
/** How long a restore / hash scroll waits for lazy content before giving up. */
export const MAIN_SCROLL_DEADLINE_MS = 3000;
const STICKY_NAV_SELECTOR = '.route-nav';
const USER_SCROLL_INTENT_EVENTS = ['wheel', 'touchstart', 'pointerdown', 'keydown'] as const;

type ScrollLocation = Pick<Location, 'key' | 'pathname' | 'search' | 'hash'>;

/** FNV-1a, base36. Only used to avoid writing a URL into sessionStorage. */
function fingerprint(text: string): string {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(36);
}

/**
 * Storage key for a history entry. The router gives every pushed entry a
 * random key; entries it did not create (the first load, a native `#fragment`
 * jump such as the skip link) all report "default", so those are told apart by
 * their URL.
 */
export function scrollStorageKey(location: ScrollLocation): string {
  if (location.key !== 'default') return location.key;
  return `default:${fingerprint(`${location.pathname}${location.search}${location.hash}`)}`;
}

export function readStoredOffsets(): Map<string, number> {
  const offsets = new Map<string, number>();
  try {
    const raw = window.sessionStorage.getItem(MAIN_SCROLL_STORAGE_KEY);
    if (!raw) return offsets;
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return offsets;
    for (const entry of parsed.slice(-MAX_ENTRIES)) {
      if (!Array.isArray(entry)) continue;
      const [key, value] = entry as unknown[];
      if (typeof key === 'string' && typeof value === 'number' && Number.isFinite(value)) {
        offsets.set(key, value);
      }
    }
  } catch {
    // Storage can be unavailable or hold a corrupt value; start empty.
  }
  return offsets;
}

function writeStoredOffsets(offsets: ReadonlyMap<string, number>): void {
  try {
    window.sessionStorage.setItem(MAIN_SCROLL_STORAGE_KEY, JSON.stringify([...offsets]));
  } catch {
    // Storage can be unavailable in privacy-restricted contexts.
  }
}

/** Most-recently-used insert with a hard size cap. */
export function rememberOffset(offsets: Map<string, number>, key: string, value: number): void {
  offsets.delete(key);
  offsets.set(key, value);
  while (offsets.size > MAX_ENTRIES) {
    const oldest = offsets.keys().next();
    if (oldest.done) break;
    offsets.delete(oldest.value);
  }
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
  useEffect(() => {
    const main = mainRef.current;
    if (!main) return undefined;
    const offsets = (offsetsRef.current ??= readStoredOffsets());
    const onScroll = () => {
      const key = activeKeyRef.current;
      if (key === null || restoringRef.current) return;
      rememberOffset(offsets, key, main.scrollTop);
    };
    const onUserIntent = () => {
      cancelPendingRef.current?.();
    };
    const onPageHide = () => writeStoredOffsets(offsets);
    main.addEventListener('scroll', onScroll, { passive: true });
    for (const type of USER_SCROLL_INTENT_EVENTS) {
      main.addEventListener(type, onUserIntent, { passive: true });
    }
    window.addEventListener('pagehide', onPageHide);
    return () => {
      main.removeEventListener('scroll', onScroll);
      for (const type of USER_SCROLL_INTENT_EVENTS) {
        main.removeEventListener(type, onUserIntent);
      }
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
