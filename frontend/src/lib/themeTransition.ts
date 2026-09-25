import { flushSync } from 'react-dom';
import type { Theme } from './themePreference';

/**
 * The theme cross-fade (2026-09-21 audit motion-03 `setTheme`; stack-04).
 *
 * A theme switch used to rely on piecemeal colour transitions on a dozen
 * selectors with mixed timings. Where the browser has same-document View
 * Transitions and the user allows motion, the switch now runs inside
 * `document.startViewTransition`: the old page is snapshotted, `update`
 * commits synchronously (`flushSync`, so AppContext's data-theme effect has
 * written <html> before the callback returns and the new snapshot is taken
 * of the new theme), and the root cross-fades on the tokens in
 * app.transitions.css. `<html data-theme-switching>` is set for the length of
 * the transition, which switches the colour transitions off so nothing tweens
 * inside the new snapshot. DECLARED EXTENSION: the prototype has no theme
 * motion.
 *
 * Everywhere else (no API, reduced motion) `update` runs directly.
 *
 * Every promise the transition returns is caught: a skipped transition
 * rejects `ready` (and `updateCallbackDone`, if `update` throws) with an
 * AbortError that must never reach the unhandledrejection listener and
 * become a client_error report.
 */

export const THEME_SWITCHING_ATTRIBUTE = 'data-theme-switching';

type ViewTransitionStarter = (update: () => void) => ViewTransition;

function viewTransitionStarter(): ViewTransitionStarter | null {
  if (typeof document === 'undefined') return null;
  const start = (document as Document & { startViewTransition?: unknown }).startViewTransition;
  return typeof start === 'function' ? (update) => document.startViewTransition(update) : null;
}

function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

const ignore = () => undefined;

/**
 * Apply a theme choice: through the cross-fade only when the PAINTED theme
 * actually changes. Pinning the theme that is already painted, or picking
 * System while the OS scheme matches it, runs `update` with no transition.
 */
export function changeTheme(painted: Theme, next: Theme, update: () => void): void {
  if (painted === next) update();
  else runThemeTransition(update);
}

/** Theme transitions not yet finished: a quick second toggle skips the first. */
let running = 0;

export function runThemeTransition(update: () => void): void {
  const start = viewTransitionStarter();
  if (!start || prefersReducedMotion()) {
    update();
    return;
  }
  const root = document.documentElement;
  running += 1;
  root.setAttribute(THEME_SWITCHING_ATTRIBUTE, '');
  let transition: ViewTransition;
  try {
    transition = start(() => {
      flushSync(update);
    });
  } catch {
    running -= 1;
    if (running === 0) root.removeAttribute(THEME_SWITCHING_ATTRIBUTE);
    update();
    return;
  }
  transition.ready.catch(ignore);
  transition.updateCallbackDone.catch(ignore);
  transition.finished
    .catch(ignore)
    .finally(() => {
      running -= 1;
      // The skipped first of two quick toggles finishes while the second
      // still runs; only the last one out clears the attribute.
      if (running === 0) root.removeAttribute(THEME_SWITCHING_ATTRIBUTE);
    });
}
