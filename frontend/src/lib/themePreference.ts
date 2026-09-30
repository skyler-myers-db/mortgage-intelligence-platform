/**
 * Theme / accent / density preference model.
 *
 * Single source of truth for the storage keys, the accepted values, the
 * choice markers, the only writers (persist*; nothing is written on mount)
 * and the "system" resolution that AppContext applies after mount. The pre-paint
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
/**
 * Choice markers: 'true' once the user has picked a theme / accent through
 * the UI. Only a marked 'dark' / 'system' theme and a marked 'bright' accent
 * are honoured, because builds up to fa4c944f^ auto-wrote dark / bright /
 * comfortable on mount and fa4c944f..0a30fca2 auto-wrote 'system'; no build
 * ever auto-wrote 'light' or a non-bright accent, so those need no marker.
 */
export const THEME_CHOICE_KEY = 'mip.themeChosen';
export const ACCENT_CHOICE_KEY = 'mip.accentChosen';

export const THEMES: readonly Theme[] = ['dark', 'light'];
export const THEME_PREFERENCES: readonly ThemePreference[] = ['dark', 'light', 'system'];
export const ACCENTS: readonly Accent[] = ['bright', 'teal', 'navy', 'red'];
export const DENSITIES: readonly Density[] = ['comfortable', 'compact'];

/**
 * Nothing chosen boots dark, the prototype default (design_files/index.html:2;
 * the 2026-09-30 ruling, report 12.4 #4, reverted the OS-following default).
 * The System option is an additive departure from the two-state prototype
 * control (css-02 / responsive-03; deviation:theme-system-option): an explicit
 * opt-in that follows prefers-color-scheme live, and dark when the platform
 * cannot report a preference.
 */
export const DEFAULT_THEME_PREFERENCE: ThemePreference = 'dark';
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

function storedValue(key: string): string | null {
  if (typeof window === 'undefined') return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

/** The stored theme choice: 'light' always; 'dark' / 'system' only when marked (see THEME_CHOICE_KEY). */
export function readThemePreference(fallback: ThemePreference): ThemePreference {
  const raw = storedValue(THEME_STORAGE_KEY);
  if (raw === 'light') return raw;
  if ((raw === 'dark' || raw === 'system') && storedValue(THEME_CHOICE_KEY) === 'true') return raw;
  return fallback;
}

/** The stored accent: any non-default accent; the default only when marked (see ACCENT_CHOICE_KEY). */
export function readAccentPreference(fallback: Accent): Accent {
  const raw = storedValue(ACCENT_STORAGE_KEY);
  const accent = (ACCENTS as readonly string[]).includes(raw ?? '') ? (raw as Accent) : null;
  if (accent === null) return fallback;
  return accent !== DEFAULT_ACCENT || storedValue(ACCENT_CHOICE_KEY) === 'true' ? accent : fallback;
}

/** The one try/catch writer every persist* call shares; storage can be unavailable. */
export function writeStoredChoice(key: string, value: string): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Private mode or blocked site data: the choice lasts this session only.
  }
}

/** An explicit theme pick (dark, light or system), marked as chosen. */
export function persistThemePreference(preference: ThemePreference): void {
  writeStoredChoice(THEME_STORAGE_KEY, preference);
  writeStoredChoice(THEME_CHOICE_KEY, 'true');
}

/** An explicit accent pick, marked as chosen. */
export function persistAccent(accent: Accent): void {
  writeStoredChoice(ACCENT_STORAGE_KEY, accent);
  writeStoredChoice(ACCENT_CHOICE_KEY, 'true');
}

/** Density needs no marker: its default was never ambiguous. */
export function persistDensity(density: Density): void {
  writeStoredChoice(DENSITY_STORAGE_KEY, density);
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
