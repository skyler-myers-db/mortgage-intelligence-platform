/**
 * @vitest-environment happy-dom
 *
 * AppProvider theme model (2026-09-21 audit css-02 / responsive-03): the
 * provider resolves a `system` preference through prefers-color-scheme,
 * follows OS flips live, persists the PREFERENCE (not the resolved theme),
 * and keeps <html data-theme> plus <meta name="theme-color"> in step.
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

vi.mock('../lib/api', () => ({ api: apiMocks }));

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

function Probe() {
  const { theme, themePreference, setTheme, setThemePreference } = useApp();
  return (
    <>
      <output data-testid="probe">{`${themePreference}/${theme}`}</output>
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

  it('defaults to system and resolves it through prefers-color-scheme', async () => {
    const media = installMatchMedia(false);
    await mount();
    expect(probe()).toBe('system/light');
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
    expect(window.localStorage.getItem('mip.theme')).toBe('system');

    await act(async () => media.flip(true));
    expect(probe()).toBe('system/dark');
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
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
    expect(window.localStorage.getItem('mip.theme')).toBe('dark');

    await act(async () => press('follow-system'));
    expect(probe()).toBe('system/light');
    expect(window.localStorage.getItem('mip.theme')).toBe('system');
  });

  it('ignores garbage in storage', async () => {
    window.localStorage.setItem('mip.theme', 'neon');
    installMatchMedia(true);
    await mount();
    expect(probe()).toBe('system/dark');
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
