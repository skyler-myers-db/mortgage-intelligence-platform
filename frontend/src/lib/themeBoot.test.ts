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
  ACCENT_STORAGE_KEY,
  CONSOLE_OPEN_STORAGE_KEY,
  DENSITIES,
  DENSITY_STORAGE_KEY,
  THEME_COLOR_TOKEN,
  THEME_PREFERENCES,
  THEME_STORAGE_KEY,
} from './themePreference';

declare const process: { cwd(): string };

const bootSource = readFileSync(join(process.cwd(), 'public', 'theme-boot.js'), 'utf8');
const indexHtml = readFileSync(join(process.cwd(), 'index.html'), 'utf8');

interface BootScenario {
  stored: Record<string, string>;
  prefersDark: boolean | 'unavailable';
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
  document.head.innerHTML = '<meta name="theme-color" content="#000000">';
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

  it('follows prefers-color-scheme when nothing or "system" is stored', () => {
    expect(runBoot({ stored: {}, prefersDark: false }).theme).toBe('light');
    expect(runBoot({ stored: {}, prefersDark: true }).theme).toBe('dark');
    expect(runBoot({ stored: { 'mip.theme': 'system' }, prefersDark: false }).theme).toBe('light');
    expect(runBoot({ stored: { 'mip.theme': 'system' }, prefersDark: true }).theme).toBe('dark');
  });

  it('boots dark when the platform cannot report a preference', () => {
    expect(runBoot({ stored: {}, prefersDark: 'unavailable' }).theme).toBe('dark');
  });

  it('ignores garbage the same way AppContext does', () => {
    const result = runBoot({
      stored: { 'mip.theme': 'neon', 'mip.accent': 'purple', 'mip.density': 'cozy' },
      prefersDark: false,
    });
    expect(result).toEqual({ theme: 'light', accent: 'bright', density: 'comfortable', themeColor: '#F4F7FA' });
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
