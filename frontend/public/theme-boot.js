/*
 * Pre-paint theme bootstrap for the Mortgage Intelligence Platform shell.
 *
 * Loaded as a classic, render-blocking script from <head> (CSP is
 * script-src 'self', so it cannot be inline). It applies data-theme /
 * data-accent / data-density to <html> BEFORE the first paint so a
 * light-theme user never sees a dark flash while React mounts, sets
 * <meta name="theme-color"> to the page background of that theme, and
 * pre-sets data-console so the .main Console gutter does not shift in
 * after mount for a presenter who left the Console open.
 *
 * It MIRRORS frontend/src/lib/themePreference.ts: same storage keys, same
 * accepted values, same fallbacks. Nothing stored boots dark (the prototype
 * default; 2026-09-30 ruling). A stored 'light' always applies; a stored
 * 'dark' or 'system' only with the mip.themeChosen marker, and a stored
 * 'bright' accent only with mip.accentChosen, because earlier builds wrote
 * those values on mount without the user choosing them. A chosen System
 * follows prefers-color-scheme, dark when the platform cannot say. Garbage
 * is ignored. frontend/src/lib/themeBoot.test.ts executes this file against
 * that module's constants, and index.html references it with a content-hash
 * query (?v=...) that the same test pins, so an edit here cannot ship stale
 * or drift from the React side.
 *
 * ES5 on purpose: it must run in whatever parses index.html first.
 */
(function () {
  'use strict';
  var THEME_KEY = 'mip.theme';
  var ACCENT_KEY = 'mip.accent';
  var DENSITY_KEY = 'mip.density';
  var CONSOLE_KEY = 'mip.consoleOpen';
  var THEME_CHOICE_KEY = 'mip.themeChosen';
  var ACCENT_CHOICE_KEY = 'mip.accentChosen';
  var DEFAULT_THEME = 'dark';
  var DEFAULT_ACCENT = 'bright';
  var THEME_PREFERENCES = ['dark', 'light', 'system'];
  var ACCENTS = ['bright', 'teal', 'navy', 'red'];
  var DENSITIES = ['comfortable', 'compact'];
  /* --bg-0 per theme in frontend/src/design-system/tokens.css. */
  var THEME_COLORS = { dark: '#04101F', light: '#F4F7FA' };

  function stored(key, allowed) {
    try {
      var raw = window.localStorage.getItem(key);
      return raw !== null && allowed.indexOf(raw) !== -1 ? raw : null;
    } catch (error) {
      return null;
    }
  }

  function prefersDark() {
    try {
      return typeof window.matchMedia !== 'function'
        || window.matchMedia('(prefers-color-scheme: dark)').matches;
    } catch (error) {
      return true;
    }
  }

  try {
    var root = document.documentElement;
    var rawTheme = stored(THEME_KEY, THEME_PREFERENCES);
    var preference = (rawTheme === 'light'
      || (rawTheme !== null && stored(THEME_CHOICE_KEY, ['true']) === 'true')) ? rawTheme : DEFAULT_THEME;
    var theme = preference === 'system' ? (prefersDark() ? 'dark' : 'light') : preference;
    var rawAccent = stored(ACCENT_KEY, ACCENTS);
    var accent = (rawAccent !== null
      && (rawAccent !== DEFAULT_ACCENT || stored(ACCENT_CHOICE_KEY, ['true']) === 'true')) ? rawAccent : DEFAULT_ACCENT;
    root.setAttribute('data-theme', theme);
    root.setAttribute('data-accent', accent);
    root.setAttribute('data-density', stored(DENSITY_KEY, DENSITIES) || 'comfortable');
    /* AppContext persists 'true' | 'false' and reflects it as open | closed. */
    root.setAttribute('data-console', stored(CONSOLE_KEY, ['true']) === 'true' ? 'open' : 'closed');
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', THEME_COLORS[theme]);
  } catch (error) {
    /* React applies the same attributes after mount; never block the shell. */
  }
})();
