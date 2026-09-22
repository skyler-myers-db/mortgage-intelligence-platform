/**
 * Theme / accent / density preference model.
 *
 * Single source of truth for the storage keys, the accepted values and the
 * "system" resolution that AppContext applies after mount. The pre-paint
 * bootstrap in `frontend/public/theme-boot.js` mirrors the same keys and
 * validation so the first painted document already carries the right
 * `data-theme` / `data-accent` / `data-density`; `themeBoot.test.ts` executes
 * that script against the constants exported here, so the two cannot drift
 * silently. Keep this module dependency-free: it runs before React.
 */

export type Theme = 'dark' | 'light';
/** What the user chose. `system` follows `prefers-color-scheme` live. */
export type ThemePreference = Theme | 'system';
export type Accent = 'bright' | 'teal' | 'navy' | 'red';
export type Density = 'comfortable' | 'compact';

export const THEME_STORAGE_KEY = 'mip.theme';
export const ACCENT_STORAGE_KEY = 'mip.accent';
export const DENSITY_STORAGE_KEY = 'mip.density';
/**
 * Console-open flag AppContext persists as 'true' | 'false' (anything else
 * reads as closed). theme-boot.js pre-sets `data-console` from it so the
 * `.main` Console gutter is on the first paint instead of shifting in after
 * React mounts.
 */
export const CONSOLE_OPEN_STORAGE_KEY = 'mip.consoleOpen';

export const THEMES: readonly Theme[] = ['dark', 'light'];
export const THEME_PREFERENCES: readonly ThemePreference[] = ['dark', 'light', 'system'];
export const ACCENTS: readonly Accent[] = ['bright', 'teal', 'navy', 'red'];
export const DENSITIES: readonly Density[] = ['comfortable', 'compact'];

/**
 * Nothing stored means "follow the OS". The prototype Console is a two-state
 * Dark/Light control that defaults to dark (design_files/index.html:2); the
 * System option and OS-following default are an additive departure from
 * the 2026-09-21 audit (css-02 / responsive-03). When the platform cannot
 * report a preference the app still boots dark, as the prototype does.
 */
export const DEFAULT_THEME_PREFERENCE: ThemePreference = 'system';
export const DEFAULT_ACCENT: Accent = 'bright';
export const DEFAULT_DENSITY: Density = 'comfortable';

export const DARK_SCHEME_QUERY = '(prefers-color-scheme: dark)';
/** The token `<meta name="theme-color">` mirrors: the page background. */
export const THEME_COLOR_TOKEN = '--bg-0';

export function readStoredChoice<T extends string>(key: string, fallback: T, allowed: readonly T[]): T {
  if (typeof window === 'undefined') return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    if (raw && (allowed as readonly string[]).includes(raw)) return raw as T;
  } catch {
    // Storage can be unavailable (private mode, blocked site data).
  }
  return fallback;
}

export function systemPrefersDark(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return true;
  try {
    return window.matchMedia(DARK_SCHEME_QUERY).matches;
  } catch {
    return true;
  }
}

export function resolveTheme(preference: ThemePreference, prefersDark: boolean): Theme {
  if (preference === 'system') return prefersDark ? 'dark' : 'light';
  return preference;
}

/** Subscribe to OS scheme changes; returns the unsubscribe function. */
export function subscribeSystemTheme(onChange: (prefersDark: boolean) => void): () => void {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return () => undefined;
  let query: MediaQueryList;
  try {
    query = window.matchMedia(DARK_SCHEME_QUERY);
  } catch {
    return () => undefined;
  }
  const handler = (event: MediaQueryListEvent) => onChange(event.matches);
  query.addEventListener('change', handler);
  return () => query.removeEventListener('change', handler);
}

/**
 * Keep `<meta name="theme-color">` on the page background of the theme that
 * is now applied to `<html>`. Reads the computed token so the value can
 * never drift from tokens.css; a no-op when styles are not attached yet.
 */
export function syncThemeColorMeta(root: HTMLElement = document.documentElement): void {
  const meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (!meta) return;
  const value = getComputedStyle(root).getPropertyValue(THEME_COLOR_TOKEN).trim();
  if (value) meta.setAttribute('content', value);
}
