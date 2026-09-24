/**
 * @vitest-environment happy-dom
 *
 * The Console "Single-key shortcuts" switch (audit wow-power-4, WCAG
 * 2.1.4): it reflects and flips the per-actor preference the keymap reads,
 * persists the choice, and a key-less chord (a plain letter) stops firing
 * while Cmd-K keeps working.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { installLocalStorage } from '../../test/installLocalStorage';
import { registerKeyBinding } from '../../lib/keymap';
import {
  SINGLE_KEY_SHORTCUTS_STORAGE_KEY,
  clearSingleKeyShortcutsPreference,
  singleKeyShortcutsEnabled,
} from '../../lib/keymapPreference';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../AppContext', () => ({
  useApp: () => ({
    consoleOpen: true,
    setConsoleOpen: vi.fn(),
    theme: 'dark',
    setTheme: vi.fn(),
    themePreference: 'dark',
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
  }),
}));

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>();
  return {
    ...actual,
    api: { ...actual.api, myAuditEvents: vi.fn().mockReturnValue(new Promise(() => {})) },
  };
});

import { Console } from './Console';

describe('Console single-key shortcuts switch', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(async () => {
    installLocalStorage();
    clearSingleKeyShortcutsPreference();
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
  });

  const toggle = () => container.querySelector<HTMLButtonElement>('[data-testid="console-single-key-shortcuts"]')!;

  it('flips the persisted preference; a letter stops firing and Cmd-K does not', () => {
    const letter = vi.fn();
    const chord = vi.fn();
    const offLetter = registerKeyBinding({ id: 't-j', scope: 'global', keys: ['j'], description: 'j', run: letter });
    const offChord = registerKeyBinding({ id: 't-k', scope: 'global', keys: ['Mod+K'], description: 'k', run: chord });
    try {
      expect(toggle().getAttribute('aria-pressed')).toBe('true');
      expect(toggle().getAttribute('aria-labelledby')).toBe('console-single-key-label');
      expect(container.querySelector('#console-single-key-label')?.textContent).toBe('Single-key shortcuts');
      // The switch also turns off the table's arrows and Enter (all are
      // non-modifier bindings), and the note says so.
      expect(container.querySelector('#console-single-key-note')?.textContent)
        .toBe("J, K, A, R, X, / and ? on their own, and the table's arrow keys and Enter. ⌘K / Ctrl+K always works.");

      act(() => toggle().click());

      expect(toggle().getAttribute('aria-pressed')).toBe('false');
      expect(singleKeyShortcutsEnabled()).toBe(false);
      expect(window.localStorage.getItem(SINGLE_KEY_SHORTCUTS_STORAGE_KEY)).toBe('off');
      act(() => {
        document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'j', bubbles: true, cancelable: true }));
        document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, bubbles: true, cancelable: true }));
      });
      expect(letter).not.toHaveBeenCalled();
      expect(chord).toHaveBeenCalledOnce();

      act(() => toggle().click());
      expect(toggle().getAttribute('aria-pressed')).toBe('true');
      expect(window.localStorage.getItem(SINGLE_KEY_SHORTCUTS_STORAGE_KEY)).toBe('on');
    } finally {
      offLetter();
      offChord();
    }
  });
});
