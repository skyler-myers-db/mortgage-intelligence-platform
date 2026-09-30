/**
 * @vitest-environment happy-dom
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { installLocalStorage } from '../test/installLocalStorage';
import {
  ACCENT_CHOICE_KEY,
  DEFAULT_ACCENT,
  DEFAULT_THEME_PREFERENCE,
  THEME_CHOICE_KEY,
  THEME_PREFERENCES,
  persistAccent,
  persistDensity,
  persistThemePreference,
  readAccentPreference,
  readStoredChoice,
  readThemePreference,
  resolveTheme,
  subscribeSystemTheme,
  syncThemeColorMeta,
  systemPrefersDark,
} from './themePreference';

type Listener = (event: { matches: boolean }) => void;

function installMatchMedia(matches: boolean): { flip: (next: boolean) => void; listeners: Listener[] } {
  const listeners: Listener[] = [];
  let current = matches;
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      media: query,
      get matches() {
        return current;
      },
      addEventListener: (_type: string, listener: Listener) => listeners.push(listener),
      removeEventListener: (_type: string, listener: Listener) => {
        const at = listeners.indexOf(listener);
        if (at !== -1) listeners.splice(at, 1);
      },
    }),
  });
  return {
    listeners,
    flip: (next) => {
      current = next;
      for (const listener of [...listeners]) listener({ matches: next });
    },
  };
}

const originalMatchMedia = window.matchMedia;

beforeEach(() => {
  installLocalStorage();
});

afterEach(() => {
  Object.defineProperty(window, 'matchMedia', { value: originalMatchMedia, configurable: true, writable: true });
});

describe('theme preference model', () => {
  it('defaults to dark (the prototype default) and resolves a chosen System by the OS scheme', () => {
    expect(DEFAULT_THEME_PREFERENCE).toBe('dark');
    expect(DEFAULT_ACCENT).toBe('bright');
    expect([THEME_CHOICE_KEY, ACCENT_CHOICE_KEY]).toEqual(['mip.themeChosen', 'mip.accentChosen']);
    expect(THEME_PREFERENCES).toEqual(['dark', 'light', 'system']);
    expect(resolveTheme('system', true)).toBe('dark');
    expect(resolveTheme('system', false)).toBe('light');
    expect(resolveTheme('light', true)).toBe('light');
    expect(resolveTheme('dark', false)).toBe('dark');
  });

  it('reads only accepted stored values', () => {
    window.localStorage.setItem('mip.theme', 'light');
    expect(readStoredChoice('mip.theme', 'system', THEME_PREFERENCES)).toBe('light');
    window.localStorage.setItem('mip.theme', 'neon');
    expect(readStoredChoice('mip.theme', 'system', THEME_PREFERENCES)).toBe('system');
    window.localStorage.removeItem('mip.theme');
    expect(readStoredChoice('mip.theme', 'system', THEME_PREFERENCES)).toBe('system');
  });

  it('reads a stored theme: light always, dark and system only when marked chosen', () => {
    expect(readThemePreference('dark')).toBe('dark');
    window.localStorage.setItem('mip.theme', 'light');
    expect(readThemePreference('dark')).toBe('light');
    window.localStorage.setItem('mip.theme', 'system');
    expect(readThemePreference('dark'), 'an auto-written system (fa4c944f..0a30fca2)').toBe('dark');
    window.localStorage.setItem('mip.themeChosen', 'true');
    expect(readThemePreference('dark')).toBe('system');
    window.localStorage.setItem('mip.theme', 'dark');
    expect(readThemePreference('light')).toBe('dark');
    window.localStorage.setItem('mip.themeChosen', 'yes');
    expect(readThemePreference('light'), 'only the literal marker counts').toBe('light');
    window.localStorage.setItem('mip.theme', 'neon');
    window.localStorage.setItem('mip.themeChosen', 'true');
    expect(readThemePreference('dark')).toBe('dark');
  });

  it('reads a stored accent: any non-default accent, the default only when marked', () => {
    // A tenant fallback other than the default shows why the marker matters.
    window.localStorage.setItem('mip.accent', 'bright');
    expect(readAccentPreference('navy'), 'an auto-written bright yields to the fallback').toBe('navy');
    window.localStorage.setItem('mip.accentChosen', 'true');
    expect(readAccentPreference('navy')).toBe('bright');
    window.localStorage.removeItem('mip.accentChosen');
    for (const accent of ['teal', 'navy', 'red'] as const) {
      window.localStorage.setItem('mip.accent', accent);
      expect(readAccentPreference('bright')).toBe(accent);
    }
    window.localStorage.setItem('mip.accent', 'purple');
    expect(readAccentPreference('bright')).toBe('bright');
  });

  it('persists only explicit picks: theme and accent with their marker, density alone', () => {
    persistThemePreference('system');
    persistAccent('teal');
    persistDensity('compact');
    const keys = ['mip.theme', 'mip.themeChosen', 'mip.accent', 'mip.accentChosen', 'mip.density'];
    expect(Object.fromEntries(keys.map((key) => [key, window.localStorage.getItem(key)]))).toEqual({
      'mip.theme': 'system',
      'mip.themeChosen': 'true',
      'mip.accent': 'teal',
      'mip.accentChosen': 'true',
      'mip.density': 'compact',
    });
    expect(window.localStorage.length).toBe(keys.length);
  });

  it('reports the OS scheme and falls back to dark without matchMedia', () => {
    installMatchMedia(false);
    expect(systemPrefersDark()).toBe(false);
    installMatchMedia(true);
    expect(systemPrefersDark()).toBe(true);
    Object.defineProperty(window, 'matchMedia', { value: undefined, configurable: true, writable: true });
    expect(systemPrefersDark()).toBe(true);
  });

  it('subscribes to scheme changes and unsubscribes cleanly', () => {
    const media = installMatchMedia(true);
    const onChange = vi.fn();
    const unsubscribe = subscribeSystemTheme(onChange);
    media.flip(false);
    expect(onChange).toHaveBeenLastCalledWith(false);
    unsubscribe();
    media.flip(true);
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(media.listeners).toEqual([]);
  });

  it('mirrors the computed page background into the theme-color meta', () => {
    document.head.innerHTML = '<meta name="theme-color" content="#000000">';
    const root = document.documentElement;
    root.style.setProperty('--bg-0', '#F4F7FA');
    syncThemeColorMeta(root);
    expect(document.querySelector('meta[name="theme-color"]')?.getAttribute('content')).toBe('#F4F7FA');
    root.style.removeProperty('--bg-0');
  });
});
