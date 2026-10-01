/**
 * @vitest-environment happy-dom
 *
 * AppProvider theme model (2026-09-21 audit css-02 / responsive-03; the
 * 2026-09-30 ruling, report 12.4 #4): a first visit boots dark and writes
 * nothing; only an explicit pick persists, with its choice marker; a chosen
 * `system` resolves through prefers-color-scheme and follows OS flips live;
 * the PREFERENCE (not the resolved theme) is stored; <html data-theme> and
 * <meta name="theme-color"> stay in step.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { installLocalStorage } from '../test/installLocalStorage';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const apiMocks = vi.hoisted(() => ({
  session: vi.fn(),
  workspace: vi.fn(),
}));

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: apiMocks,
}));

vi.mock('../lib/configOptionsQuery', () => {
  const STABLE = { data: { lender_name: 'Summit Mortgage', rum_enabled: false } };
  return { useConfigOptionsQuery: () => STABLE };
});

import { AppProvider, useApp } from './AppContext';

type Listener = (event: { matches: boolean }) => void;

function installMatchMedia(initial: boolean): { flip: (next: boolean) => void } {
  const listeners: Listener[] = [];
  let current = initial;
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
    flip: (next) => {
      current = next;
      for (const listener of [...listeners]) listener({ matches: next });
    },
  };
}

/** The five keys a theme / accent / density pick may write. */
const PREFERENCE_KEYS = ['mip.theme', 'mip.themeChosen', 'mip.accent', 'mip.accentChosen', 'mip.density'] as const;
const storedPreferences = () =>
  Object.fromEntries(PREFERENCE_KEYS.map((key) => [key, window.localStorage.getItem(key)]).filter(([, value]) => value !== null));

function Probe() {
  const { theme, themePreference, setTheme, setThemePreference, accent, setAccent, density, setDensity } = useApp();
  return (
    <>
      <output data-testid="probe">{`${themePreference}/${theme}`}</output>
      <output data-testid="look">{`${accent}/${density}`}</output>
      <button type="button" data-testid="accent-teal" onClick={() => setAccent('teal')}>teal</button>
      <button type="button" data-testid="density-compact" onClick={() => setDensity('compact')}>compact</button>
      <button type="button" data-testid="pin-dark" onClick={() => setTheme('dark')}>dark</button>
      <button type="button" data-testid="pin-light" onClick={() => setTheme('light')}>light</button>
      <button type="button" data-testid="follow-system" onClick={() => setThemePreference('system')}>system</button>
    </>
  );
}

function press(testId: string): void {
  const button = document.querySelector<HTMLButtonElement>(`[data-testid="${testId}"]`);
  if (!button) throw new Error(`${testId} not rendered`);
  button.click();
}

describe('AppProvider theme preference', () => {
  let root: Root;
  let queryClient: QueryClient;
  const originalMatchMedia = window.matchMedia;

  beforeEach(() => {
    installLocalStorage();
    document.body.innerHTML = '<div id="root"></div>';
    document.head.innerHTML = '<meta name="theme-color" content="#000000">';
    document.documentElement.removeAttribute('data-theme');
    root = createRoot(document.getElementById('root') as HTMLElement);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    apiMocks.workspace.mockResolvedValue({ saved_leads: [], saved_drafts: [] });
    apiMocks.session.mockReturnValue(new Promise(() => {}));
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    document.body.innerHTML = '';
    Object.defineProperty(window, 'matchMedia', { value: originalMatchMedia, configurable: true, writable: true });
    vi.clearAllMocks();
  });

  async function mount() {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <AppProvider><Probe /></AppProvider>
        </QueryClientProvider>,
      );
    });
  }

  const probe = () => document.querySelector('[data-testid="probe"]')?.textContent;

  it('mounts dark with nothing stored, even under an OS-light scheme, and writes none of the five keys', async () => {
    const media = installMatchMedia(false);
    await mount();
    expect(probe()).toBe('dark/dark');
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
    expect(document.documentElement.getAttribute('data-accent')).toBe('bright');
    expect(storedPreferences()).toEqual({});

    await act(async () => media.flip(true));
    await act(async () => media.flip(false));
    expect(probe()).toBe('dark/dark');
    expect(storedPreferences()).toEqual({});
  });

  it('a deploy tenant default (light / navy) applies with nothing stored and writes nothing; a marked choice wins', async () => {
    // responsive-10: the build writes these metas only off the product defaults.
    document.head.insertAdjacentHTML('beforeend', '<meta name="mip-default-theme" content="light"><meta name="mip-default-accent" content="navy">');
    installMatchMedia(true);
    await mount();
    expect(probe()).toBe('light/light');
    expect(document.querySelector('[data-testid="look"]')?.textContent).toBe('navy/comfortable');
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
    expect(storedPreferences()).toEqual({});

    act(() => root.unmount());
    root = createRoot(document.getElementById('root') as HTMLElement);
    window.localStorage.setItem('mip.theme', 'dark');
    window.localStorage.setItem('mip.themeChosen', 'true');
    window.localStorage.setItem('mip.accent', 'bright');
    window.localStorage.setItem('mip.accentChosen', 'true');
    await mount();
    expect(probe()).toBe('dark/dark');
    expect(document.querySelector('[data-testid="look"]')?.textContent).toBe('bright/comfortable');
  });

  it('a chosen System writes the preference and its marker, then follows OS flips', async () => {
    const media = installMatchMedia(false);
    await mount();
    await act(async () => press('follow-system'));
    expect(probe()).toBe('system/light');
    expect(storedPreferences()).toEqual({ 'mip.theme': 'system', 'mip.themeChosen': 'true' });

    await act(async () => media.flip(true));
    expect(probe()).toBe('system/dark');
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
  });

  it('boots an unmarked stored System (auto-written by fa4c944f..0a30fca2) dark', async () => {
    window.localStorage.setItem('mip.theme', 'system');
    installMatchMedia(false);
    await mount();
    expect(probe()).toBe('dark/dark');
  });

  it('setAccent writes the accent and its marker; setDensity writes density only', async () => {
    installMatchMedia(true);
    await mount();
    await act(async () => press('accent-teal'));
    expect(document.querySelector('[data-testid="look"]')?.textContent).toBe('teal/comfortable');
    expect(storedPreferences()).toEqual({ 'mip.accent': 'teal', 'mip.accentChosen': 'true' });
    await act(async () => press('density-compact'));
    expect(storedPreferences()).toEqual({ 'mip.accent': 'teal', 'mip.accentChosen': 'true', 'mip.density': 'compact' });
  });

  it('honours a stored explicit theme and ignores OS flips for it', async () => {
    window.localStorage.setItem('mip.theme', 'light');
    const media = installMatchMedia(true);
    await mount();
    expect(probe()).toBe('light/light');

    await act(async () => media.flip(false));
    await act(async () => media.flip(true));
    expect(probe()).toBe('light/light');
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
  });

  it('setTheme pins an explicit theme; setThemePreference("system") lets the OS decide again', async () => {
    installMatchMedia(false);
    await mount();
    await act(async () => press('pin-dark'));
    expect(probe()).toBe('dark/dark');
    expect(storedPreferences()).toEqual({ 'mip.theme': 'dark', 'mip.themeChosen': 'true' });

    await act(async () => press('follow-system'));
    expect(probe()).toBe('system/light');
    expect(window.localStorage.getItem('mip.theme')).toBe('system');
  });

  it('ignores garbage in storage and boots dark', async () => {
    window.localStorage.setItem('mip.theme', 'neon');
    installMatchMedia(false);
    await mount();
    expect(probe()).toBe('dark/dark');
  });

  it('mirrors the painted theme into meta theme-color when the token is computable', async () => {
    installMatchMedia(true);
    document.documentElement.style.setProperty('--bg-0', '#04101F');
    await mount();
    expect(document.querySelector('meta[name="theme-color"]')?.getAttribute('content')).toBe('#04101F');
    document.documentElement.style.setProperty('--bg-0', '#F4F7FA');
    await act(async () => press('pin-light'));
    expect(document.querySelector('meta[name="theme-color"]')?.getAttribute('content')).toBe('#F4F7FA');
    document.documentElement.style.removeProperty('--bg-0');
  });
});

/**
 * The theme cross-fade (2026-09-21 audit motion-03 setTheme; stack-04): a
 * change of the PAINTED theme runs inside document.startViewTransition with a
 * flushSync'd update, so <html data-theme> already names the new theme when
 * the callback returns (the browser snapshots right after it). A choice that
 * leaves the painted theme alone starts nothing.
 */
describe('AppProvider theme cross-fade', () => {
  let root: Root;
  let queryClient: QueryClient;
  const originalMatchMedia = window.matchMedia;
  let themeInsideCallback: Array<string | null> = [];

  function installSchemeOnly(systemDark: boolean): void {
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      writable: true,
      value: (query: string) => ({
        media: query,
        // Motion is allowed; only the colour scheme query reports the OS.
        matches: query.includes('prefers-color-scheme: dark') ? systemDark : false,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
      }),
    });
  }

  beforeEach(() => {
    installLocalStorage();
    themeInsideCallback = [];
    document.body.innerHTML = '<div id="root"></div>';
    document.documentElement.removeAttribute('data-theme');
    root = createRoot(document.getElementById('root') as HTMLElement);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    apiMocks.workspace.mockResolvedValue({ saved_leads: [], saved_drafts: [] });
    apiMocks.session.mockReturnValue(new Promise(() => {}));
    // The browser calls back on a later task, after the old snapshot.
    Object.defineProperty(document, 'startViewTransition', {
      configurable: true,
      writable: true,
      value: vi.fn((callback: () => void) => {
        const done = new Promise<void>((resolve) => {
          window.setTimeout(() => {
            callback();
            themeInsideCallback.push(document.documentElement.getAttribute('data-theme'));
            resolve();
          }, 0);
        });
        return { ready: done, updateCallbackDone: done, finished: done, skipTransition: () => undefined };
      }),
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    document.body.innerHTML = '';
    Reflect.deleteProperty(document, 'startViewTransition');
    Object.defineProperty(window, 'matchMedia', { value: originalMatchMedia, configurable: true, writable: true });
    vi.clearAllMocks();
  });

  async function mount() {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <AppProvider><Probe /></AppProvider>
        </QueryClientProvider>,
      );
    });
  }

  async function pressAndSettle(testId: string) {
    await act(async () => {
      press(testId);
      await new Promise((resolve) => window.setTimeout(resolve, 10));
    });
  }

  const startViewTransition = () =>
    (document as Document & { startViewTransition: ReturnType<typeof vi.fn> }).startViewTransition;
  const probe = () => document.querySelector('[data-testid="probe"]')?.textContent;

  it('a dark to light toggle has written data-theme="light" by the time the transition callback returns', async () => {
    window.localStorage.setItem('mip.theme', 'dark');
    installSchemeOnly(true);
    await mount();
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');

    // The pick is stored synchronously, before the transition's callback runs.
    act(() => press('pin-light'));
    expect(themeInsideCallback).toEqual([]);
    expect(storedPreferences()).toEqual({ 'mip.theme': 'light', 'mip.themeChosen': 'true' });
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 10));
    });
    expect(startViewTransition()).toHaveBeenCalledTimes(1);
    expect(themeInsideCallback).toEqual(['light']);
    expect(probe()).toBe('light/light');
    expect(document.documentElement.hasAttribute('data-theme-switching')).toBe(false);

    await pressAndSettle('pin-dark');
    expect(startViewTransition()).toHaveBeenCalledTimes(2);
    expect(themeInsideCallback).toEqual(['light', 'dark']);
  });

  it('a System pick that matches the painted theme starts no transition', async () => {
    window.localStorage.setItem('mip.theme', 'dark');
    installSchemeOnly(true);
    await mount();
    await pressAndSettle('follow-system');
    expect(probe()).toBe('system/dark');
    expect(startViewTransition()).not.toHaveBeenCalled();

    // Pinning the theme that is already painted starts none either.
    await pressAndSettle('pin-dark');
    expect(probe()).toBe('dark/dark');
    expect(startViewTransition()).not.toHaveBeenCalled();
  });

  it('a System pick that changes the painted theme cross-fades', async () => {
    window.localStorage.setItem('mip.theme', 'dark');
    installSchemeOnly(false);
    await mount();
    await pressAndSettle('follow-system');
    expect(probe()).toBe('system/light');
    expect(startViewTransition()).toHaveBeenCalledTimes(1);
    expect(themeInsideCallback).toEqual(['light']);
  });
});
