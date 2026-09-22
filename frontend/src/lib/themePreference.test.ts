/**
 * @vitest-environment happy-dom
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { installLocalStorage } from '../test/installLocalStorage';
import {
  DEFAULT_THEME_PREFERENCE,
  THEME_PREFERENCES,
  readStoredChoice,
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
  it('defaults to following the OS and resolves system by the OS scheme', () => {
    expect(DEFAULT_THEME_PREFERENCE).toBe('system');
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
