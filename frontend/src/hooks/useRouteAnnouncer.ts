import { useEffect, useRef, type RefObject } from 'react';
import { useLocation } from 'react-router';
import { documentTitleFor, routePageLabel } from '../lib/routeMeta';
import { retryUntil } from './retryUntil';

/**
 * useRouteAnnouncer — says where the user is after every navigation.
 *
 * Audit 2026-09-21 (`critic-v1`, `a11y-03`, `shell-08`, `stack-03`): every
 * route shared one document title (WCAG 2.4.2, Level A), and a route change
 * moved no focus and announced nothing, so keyboard and screen-reader users
 * stayed parked on the link they had just activated.
 *
 *   - `document.title` becomes "<Page> · Mortgage Intelligence Platform" from
 *     the shared `routeMeta` table. It is set in an effect on purpose: React
 *     19 hoists a rendered `<title>` AFTER the static one in `index.html`, and
 *     `document.title` reads the first, so a rendered `<title>` would lose.
 *   - On a pathname change (never on first load, where the browser announces
 *     the title itself) the page label is written into the shell's single
 *     polite live region and focus moves to the page `<h1>`, or to a focusable
 *     `#hash` target when the link addressed one. Lazy routes may not have
 *     rendered yet, so the focus waits (bounded) and falls back to `<main>`.
 *   - A hash-only change moves focus to its target and announces nothing.
 *   - Focus is never pulled out of an open dialog (the floating Genie panel
 *     navigates the page behind it while the user keeps typing).
 *
 * Focus uses `preventScroll` so it cannot fight `useMainScroll`.
 */

/** How long focus waits for a lazy route's heading before settling on `<main>`. */
export const ROUTE_FOCUS_DEADLINE_MS = 3000;

function focusHeldByDialog(): boolean {
  const active = document.activeElement;
  return active instanceof Element && active.closest('[role="dialog"]') !== null;
}

function hashTarget(main: HTMLElement, hash: string): HTMLElement | null {
  if (hash.length < 2) return null;
  let id = hash.slice(1);
  try {
    id = decodeURIComponent(id);
  } catch {
    // Malformed escape: fall back to the raw fragment.
  }
  const target = document.getElementById(id);
  if (!target || target === main || !main.contains(target)) return null;
  return target.hasAttribute('tabindex') ? target : null;
}

export function useRouteAnnouncer(
  mainRef: RefObject<HTMLElement | null>,
  liveRegionRef: RefObject<HTMLElement | null>,
): void {
  const { pathname, hash } = useLocation();
  const title = documentTitleFor(pathname);
  const label = routePageLabel(pathname);
  const previousRef = useRef<{ pathname: string; hash: string } | null>(null);

  useEffect(() => {
    document.title = title;
  }, [title]);

  useEffect(() => {
    const previous = previousRef.current;
    previousRef.current = { pathname, hash };
    // First load: the browser announces the page title on its own.
    if (previous === null) return undefined;
    const pathnameChanged = previous.pathname !== pathname;
    if (!pathnameChanged && previous.hash === hash) return undefined;

    if (pathnameChanged && liveRegionRef.current) {
      liveRegionRef.current.textContent = label;
    }
    const main = mainRef.current;
    if (!main || focusHeldByDialog()) return undefined;

    if (!pathnameChanged) {
      hashTarget(main, hash)?.focus({ preventScroll: true });
      return undefined;
    }
    return retryUntil(
      () => {
        const heading = main.querySelector<HTMLElement>('h1');
        if (!heading) return false;
        (hashTarget(main, hash) ?? heading).focus({ preventScroll: true });
        return true;
      },
      main,
      ROUTE_FOCUS_DEADLINE_MS,
      () => {
        if (!focusHeldByDialog()) main.focus({ preventScroll: true });
      },
    );
  }, [pathname, hash, label, mainRef, liveRegionRef]);
}
