/**
 * @vitest-environment happy-dom
 *
 * public/theme-boot.js is the pre-paint mirror of lib/themePreference.ts
 * (2026-09-21 audit css-02 / responsive-03). This test:
 *
 *  1. executes the real script in a DOM for every stored / OS-preference
 *     case and asserts it lands the same attributes AppContext would;
 *  2. pins the script's key names, accepted values and theme colours to the
 *     TS constants and to tokens.css, so the two sides cannot drift;
 *  3. pins the `?v=` content hash in index.html, so an edit to the script
 *     cannot be served stale from a browser cache (the SPA fallback serves
 *     public/ files with no Cache-Control; the query string is the lever).
 */
import { describe, expect, it } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads repo files under Vitest only.
import { createHash } from 'node:crypto';
// @ts-expect-error see node:crypto note above.
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:crypto note above.
import { join } from 'node:path';
import { installLocalStorage } from '../test/installLocalStorage';
import { TokenCascade, readTokensCss } from '../test/tokenCascade';
import {
  ACCENTS,
  ACCENT_CHOICE_KEY,
  ACCENT_STORAGE_KEY,
  CONSOLE_OPEN_STORAGE_KEY,
  DEFAULT_ACCENT,
  DEFAULT_THEME_PREFERENCE,
  DENSITIES,
  DENSITY_STORAGE_KEY,
  THEME_CHOICE_KEY,
  THEME_COLOR_TOKEN,
  THEME_PREFERENCES,
  THEME_STORAGE_KEY,
  TENANT_ACCENT_META,
  TENANT_THEME_META,
  readAccentPreference,
  readThemePreference,
  resolveTheme,
  tenantDefaultAccent,
  tenantDefaultTheme,
} from './themePreference';

declare const process: { cwd(): string };

const bootSource = readFileSync(join(process.cwd(), 'public', 'theme-boot.js'), 'utf8');
const indexHtml = readFileSync(join(process.cwd(), 'index.html'), 'utf8');

interface BootScenario {
  stored: Record<string, string>;
  prefersDark: boolean | 'unavailable';
  /** Build-written tenant defaults (responsive-10), as `<meta name content>` in <head>. */
  metas?: Record<string, string>;
}

interface BootResult {
  theme: string | null;
  accent: string | null;
  density: string | null;
  themeColor: string | null;
}

function runBoot(scenario: BootScenario): BootResult {
  installLocalStorage();
  for (const [key, value] of Object.entries(scenario.stored)) window.localStorage.setItem(key, value);
  const root = document.documentElement;
  for (const attr of ['data-theme', 'data-accent', 'data-density', 'data-console']) root.removeAttribute(attr);
  document.head.innerHTML = '<meta name="theme-color" content="#000000">'
    + Object.entries(scenario.metas ?? {}).map(([name, content]) => `<meta name="${name}" content="${content}">`).join('');
  const original = window.matchMedia;
  if (scenario.prefersDark === 'unavailable') {
    Object.defineProperty(window, 'matchMedia', { value: undefined, configurable: true, writable: true });
  } else {
    const matches = scenario.prefersDark;
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      writable: true,
      value: (query: string) => ({ matches: query === '(prefers-color-scheme: dark)' && matches, media: query }),
    });
  }
  try {
    // The script is an IIFE that touches only window/document; evaluating it
    // with Function keeps the happy-dom globals in scope.
    new Function('window', 'document', bootSource)(window, document);
  } finally {
    Object.defineProperty(window, 'matchMedia', { value: original, configurable: true, writable: true });
  }
  return {
    theme: root.getAttribute('data-theme'),
    accent: root.getAttribute('data-accent'),
    density: root.getAttribute('data-density'),
    themeColor: document.querySelector('meta[name="theme-color"]')?.getAttribute('content') ?? null,
  };
}

function bootConstant(name: string): string[] {
  const match = new RegExp(`var ${name} = \\[([^\\]]*)\\];`).exec(bootSource);
  if (!match) throw new Error(`theme-boot.js no longer declares ${name}`);
  return match[1].split(',').map((part) => part.trim().replace(/^'|'$/g, ''));
}

describe('theme-boot.js mirrors lib/themePreference.ts', () => {
  it('uses the same storage keys and accepted values', () => {
    expect(bootSource).toContain(`var THEME_KEY = '${THEME_STORAGE_KEY}';`);
    expect(bootSource).toContain(`var ACCENT_KEY = '${ACCENT_STORAGE_KEY}';`);
    expect(bootSource).toContain(`var DENSITY_KEY = '${DENSITY_STORAGE_KEY}';`);
    expect(bootSource).toContain(`var CONSOLE_KEY = '${CONSOLE_OPEN_STORAGE_KEY}';`);
    expect(bootSource).toContain(`var THEME_CHOICE_KEY = '${THEME_CHOICE_KEY}';`);
    expect(bootSource).toContain(`var ACCENT_CHOICE_KEY = '${ACCENT_CHOICE_KEY}';`);
    expect(bootSource).toContain(`var DEFAULT_THEME = '${DEFAULT_THEME_PREFERENCE}';`);
    expect(bootSource).toContain(`var DEFAULT_ACCENT = '${DEFAULT_ACCENT}';`);
    expect(bootConstant('THEME_PREFERENCES')).toEqual([...THEME_PREFERENCES]);
    expect(bootConstant('ACCENTS')).toEqual([...ACCENTS]);
    expect(bootConstant('DENSITIES')).toEqual([...DENSITIES]);
  });

  it('paints the theme-color of each theme from tokens.css', () => {
    const css = readTokensCss();
    for (const theme of ['dark', 'light']) {
      const expected = new TokenCascade(css, { theme, accent: 'bright' }).resolve(THEME_COLOR_TOKEN);
      expect(bootSource).toContain(`${theme}: '${expected}'`);
    }
  });

  it('applies a stored explicit theme before React mounts', () => {
    expect(runBoot({ stored: { 'mip.theme': 'light' }, prefersDark: true })).toEqual({
      theme: 'light', accent: 'bright', density: 'comfortable', themeColor: '#F4F7FA',
    });
    expect(runBoot({ stored: { 'mip.theme': 'dark' }, prefersDark: false })).toEqual({
      theme: 'dark', accent: 'bright', density: 'comfortable', themeColor: '#04101F',
    });
  });

  it('boots dark with nothing stored, even when the OS prefers light (2026-09-30, 12.4 #4)', () => {
    expect(runBoot({ stored: {}, prefersDark: false })).toEqual({
      theme: 'dark', accent: 'bright', density: 'comfortable', themeColor: '#04101F',
    });
  });

  it('follows the OS only for a System the user chose (the mip.themeChosen marker)', () => {
    // An unmarked 'system' was auto-written on mount by fa4c944f..0a30fca2.
    expect(runBoot({ stored: { 'mip.theme': 'system' }, prefersDark: false }).theme).toBe('dark');
    const chosen = { 'mip.theme': 'system', 'mip.themeChosen': 'true' };
    expect(runBoot({ stored: chosen, prefersDark: false }).theme).toBe('light');
    expect(runBoot({ stored: chosen, prefersDark: true }).theme).toBe('dark');
    // No build ever auto-wrote 'light': it applies unmarked.
    expect(runBoot({ stored: { 'mip.theme': 'light' }, prefersDark: true }).theme).toBe('light');
  });

  it('honours an unmarked accent only when it is not the default', () => {
    expect(runBoot({ stored: { 'mip.accent': 'bright' }, prefersDark: true }).accent).toBe('bright');
    expect(runBoot({ stored: { 'mip.accent': 'teal' }, prefersDark: true }).accent).toBe('teal');
  });

  it('lands exactly what AppContext reads, for every stored / marker / OS combination', () => {
    const themes = [undefined, 'dark', 'light', 'system', 'neon'];
    const accents = [undefined, 'bright', 'teal', 'purple'];
    const marks = [undefined, 'true', 'yes'];
    for (const theme of themes) for (const themeMark of marks) for (const accent of accents) {
      for (const accentMark of marks) for (const prefersDark of [true, false]) {
        const stored: Record<string, string> = {};
        if (theme) stored[THEME_STORAGE_KEY] = theme;
        if (themeMark) stored[THEME_CHOICE_KEY] = themeMark;
        if (accent) stored[ACCENT_STORAGE_KEY] = accent;
        if (accentMark) stored[ACCENT_CHOICE_KEY] = accentMark;
        const booted = runBoot({ stored, prefersDark });
        // runBoot left the same storage installed for the TS readers.
        const expected = {
          theme: resolveTheme(readThemePreference(DEFAULT_THEME_PREFERENCE), prefersDark),
          accent: readAccentPreference(DEFAULT_ACCENT),
        };
        expect({ theme: booted.theme, accent: booted.accent }, JSON.stringify({ stored, prefersDark })).toEqual(expected);
      }
    }
  });

  it('boots dark when the platform cannot report a preference', () => {
    expect(runBoot({ stored: {}, prefersDark: 'unavailable' }).theme).toBe('dark');
  });

  it('ignores garbage the same way AppContext does', () => {
    const result = runBoot({
      stored: { 'mip.theme': 'neon', 'mip.accent': 'purple', 'mip.density': 'cozy' },
      prefersDark: false,
    });
    expect(result).toEqual({ theme: 'dark', accent: 'bright', density: 'comfortable', themeColor: '#04101F' });
  });

  it('pre-sets the Console gutter the way AppContext reflects the stored flag', () => {
    const consoleState = (stored: Record<string, string>): string | null => {
      runBoot({ stored, prefersDark: true });
      return document.documentElement.getAttribute('data-console');
    };
    expect(consoleState({ 'mip.consoleOpen': 'true' })).toBe('open');
    expect(consoleState({ 'mip.consoleOpen': 'false' })).toBe('closed');
    expect(consoleState({})).toBe('closed');
    expect(consoleState({ 'mip.consoleOpen': 'yes' })).toBe('closed');
  });

  it('applies stored accent and density', () => {
    const result = runBoot({
      stored: { 'mip.theme': 'dark', 'mip.accent': 'teal', 'mip.density': 'compact' },
      prefersDark: false,
    });
    expect(result).toEqual({ theme: 'dark', accent: 'teal', density: 'compact', themeColor: '#04101F' });
  });
});

/**
 * Deploy-time tenant defaults (audit responsive-10, 12.4 #9): a build-written
 * meta applies only to a user with no explicit choice, after W5a's marker
 * rules and before the product default; anything outside the accepted lists
 * is ignored. AppContext reads the same order through tenantDefault*().
 */
describe('theme-boot.js applies a tenant default only without an explicit choice', () => {
  const navy = { [TENANT_ACCENT_META]: 'navy' };
  const light = { [TENANT_THEME_META]: 'light' };

  it('names the same metas as lib/themePreference.ts', () => {
    expect(bootSource).toContain(`var TENANT_THEME_META = '${TENANT_THEME_META}';`);
    expect(bootSource).toContain(`var TENANT_ACCENT_META = '${TENANT_ACCENT_META}';`);
  });

  it('a navy tenant accent: nothing stored or an unmarked bright -> navy; a marked bright or a stored teal wins', () => {
    expect(runBoot({ stored: {}, prefersDark: true, metas: navy }).accent).toBe('navy');
    expect(runBoot({ stored: { 'mip.accent': 'bright' }, prefersDark: true, metas: navy }).accent).toBe('navy');
    expect(runBoot({ stored: { 'mip.accent': 'bright', 'mip.accentChosen': 'true' }, prefersDark: true, metas: navy }).accent).toBe('bright');
    expect(runBoot({ stored: { 'mip.accent': 'teal' }, prefersDark: true, metas: navy }).accent).toBe('teal');
  });

  it('a light tenant theme: an unmarked dark -> light, a marked dark wins; system follows the OS', () => {
    expect(runBoot({ stored: {}, prefersDark: true, metas: light })).toEqual({
      theme: 'light', accent: 'bright', density: 'comfortable', themeColor: '#F4F7FA',
    });
    expect(runBoot({ stored: { 'mip.theme': 'dark' }, prefersDark: true, metas: light }).theme).toBe('light');
    expect(runBoot({ stored: { 'mip.theme': 'dark', 'mip.themeChosen': 'true' }, prefersDark: false, metas: light }).theme).toBe('dark');
    const system = { [TENANT_THEME_META]: 'system' };
    expect(runBoot({ stored: {}, prefersDark: false, metas: system }).theme).toBe('light');
    expect(runBoot({ stored: {}, prefersDark: true, metas: system }).theme).toBe('dark');
  });

  it('ignores a tenant meta outside the accepted values', () => {
    const garbage = { [TENANT_THEME_META]: 'sepia', [TENANT_ACCENT_META]: 'magenta' };
    expect(runBoot({ stored: {}, prefersDark: false, metas: garbage })).toEqual({
      theme: 'dark', accent: 'bright', density: 'comfortable', themeColor: '#04101F',
    });
  });

  it('lands exactly what AppContext reads with tenant metas, for every stored / marker combination', () => {
    for (const metas of [light, navy, { ...light, ...navy }, { [TENANT_THEME_META]: 'system', [TENANT_ACCENT_META]: 'red' }]) {
      for (const theme of [undefined, 'dark', 'light', 'system']) for (const themeMark of [undefined, 'true']) {
        for (const accent of [undefined, 'bright', 'teal']) for (const accentMark of [undefined, 'true']) {
          for (const prefersDark of [true, false]) {
            const stored: Record<string, string> = {};
            if (theme) stored[THEME_STORAGE_KEY] = theme;
            if (themeMark) stored[THEME_CHOICE_KEY] = themeMark;
            if (accent) stored[ACCENT_STORAGE_KEY] = accent;
            if (accentMark) stored[ACCENT_CHOICE_KEY] = accentMark;
            const booted = runBoot({ stored, prefersDark, metas });
            // runBoot left the same storage and metas in place for the TS readers.
            const expected = {
              theme: resolveTheme(readThemePreference(tenantDefaultTheme()), prefersDark),
              accent: readAccentPreference(tenantDefaultAccent()),
            };
            expect({ theme: booted.theme, accent: booted.accent }, JSON.stringify({ stored, prefersDark, metas })).toEqual(expected);
          }
        }
      }
    }
  });
});

describe('index.html wires the bootstrap', () => {
  it('declares the color-scheme meta and a render-blocking head script', () => {
    expect(indexHtml).toMatch(/<meta name="color-scheme" content="dark light" \/>/);
    const head = indexHtml.slice(0, indexHtml.indexOf('</head>'));
    expect(head).toMatch(/<script src="\/theme-boot\.js\?v=[0-9a-f]{8}"><\/script>/);
    expect(head).not.toMatch(/<script[^>]*theme-boot[^>]*(async|defer|type="module")/);
  });

  it('references the script by its current content hash so an edit is never served stale', () => {
    const hash = createHash('sha256').update(bootSource).digest('hex').slice(0, 8);
    expect(
      indexHtml,
      `public/theme-boot.js changed: update the ?v= query in index.html to ${hash}`,
    ).toContain(`/theme-boot.js?v=${hash}`);
  });
});
