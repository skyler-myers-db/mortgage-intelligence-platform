/**
 * @vitest-environment happy-dom
 *
 * Console theme control (2026-09-21 audit css-02): Dark / Light from the
 * prototype plus the additive System option. The control edits the
 * PREFERENCE, so System stays selected while the painted theme follows the
 * OS, and pressing a button hands the provider that preference.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { appContext } = vi.hoisted(() => ({
  appContext: {
    consoleOpen: true,
    setConsoleOpen: vi.fn(),
    theme: 'dark',
    setTheme: vi.fn(),
    themePreference: 'system',
    setThemePreference: vi.fn(),
    accent: 'bright',
    setAccent: vi.fn(),
    density: 'comfortable',
    setDensity: vi.fn(),
    lender: 'Summit Mortgage',
    showEvidence: true,
    setShowEvidence: vi.fn(),
    showConfidence: true,
    setShowConfidence: vi.fn(),
    setGenieOpen: vi.fn(),
    savedLeads: {},
    savedDrafts: {},
    workspaceStatus: 'ready',
    workspaceError: null,
    refreshWorkspace: vi.fn(),
    recentActivityFocusRequest: 0,
    acknowledgeRecentActivityFocus: vi.fn(),
  },
}));

vi.mock('../AppContext', () => ({
  useApp: () => appContext,
}));

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>();
  return {
    ...actual,
    api: { ...actual.api, myAuditEvents: vi.fn().mockReturnValue(new Promise(() => {})) },
  };
});

import { Console } from './Console';

describe('Console theme control', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(async () => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter><Console /></MemoryRouter>
        </QueryClientProvider>,
      );
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.clearAllMocks();
  });

  const buttons = () =>
    Array.from(container.querySelectorAll<HTMLButtonElement>('[role="group"][aria-label="Theme"] button'));

  it('offers Dark, Light and System and marks the stored PREFERENCE, not the painted theme', () => {
    expect(buttons().map((b) => b.textContent)).toEqual(['Dark', 'Light', 'System']);
    // theme is 'dark' (painted) but the preference is 'system'.
    expect(buttons().map((b) => b.getAttribute('aria-pressed'))).toEqual(['false', 'false', 'true']);
    expect(buttons()[2].className).toContain('is-active');
  });

  it('hands the provider the chosen preference', () => {
    act(() => buttons()[1].click());
    expect(appContext.setThemePreference).toHaveBeenCalledWith('light');
    act(() => buttons()[2].click());
    expect(appContext.setThemePreference).toHaveBeenCalledWith('system');
    expect(appContext.setTheme).not.toHaveBeenCalled();
  });
});
