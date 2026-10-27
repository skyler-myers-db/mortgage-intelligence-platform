// @vitest-environment happy-dom
/**
 * Virtual screen-reader pass: the ⌘K command palette (a combobox over a
 * grouped listbox in a native dialog; WCAG 4.1.2, 4.1.3, 2.1.1, 2.4.3;
 * docs/accessibility/VPAT.md). Harness as CommandPalette.test.tsx: borrower
 * search is mocked and answers no rows.
 *
 * Not modelled (virtualReader.ts): the reader does not follow
 * aria-activedescendant, so an arrow key is silent here; a real reader speaks
 * the newly active option. `reportFocus()` re-reads the focused combobox,
 * whose phrase names the active descendant. The listbox is then read with
 * the virtual cursor, as a user exploring it would.
 */
import { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import { mount } from '../render';
import { expectTranscript, nextUntil, reportFocus, settle, startReader } from './virtualReader';

const navigate = vi.fn();
vi.mock('react-router', () => ({ useNavigate: () => navigate }));

vi.mock('../../components/AppContext', () => ({
  useApp: () => ({
    theme: 'dark',
    setTheme: vi.fn(),
    consoleOpen: false,
    setConsoleOpen: vi.fn(),
    setGenieOpen: vi.fn(),
    canAccessAdmin: false,
    savedLeads: {},
  }),
}));
vi.mock('../../lib/apiClients/savedViews', () => ({ fetchSavedViews: () => Promise.resolve({ saved_views: [] }) }));
vi.mock('../../lib/sessionQuery', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/sessionQuery')>()),
  useAuditLedgerAccess: () => false,
}));
const borrowerSearch = vi.fn(() => Promise.resolve([]));
vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: { borrowerSearch: (...args: unknown[]) => borrowerSearch(...(args as [])) },
}));
vi.mock('../../lib/routePreloaders', () => ({ preloadRouteForPath: () => undefined }));

import { CommandPalette, loadCommandPaletteDialog } from '../../components/command/CommandPalette';

beforeAll(async () => {
  await loadCommandPaletteDialog();
}, 60_000);

const input = () => document.querySelector<HTMLInputElement>('input[role="combobox"]')!;
const dialog = () => document.querySelector<HTMLDialogElement>('dialog.cmdk[open]');

function keyOnInput(key: string) {
  act(() => {
    input().dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
  });
}

async function setQuery(value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
  await act(async () => {
    setter.call(input(), value);
    input().dispatchEvent(new Event('input', { bubbles: true }));
  });
}

describe('virtual screen reader: command palette', () => {
  it('announces the dialog and combobox, reads grouped options and speaks a no-match status', async () => {
    await mount(
      <QueryClientProvider client={new QueryClient()}>
        <button type="button">Opener</button>
        <CommandPalette />
      </QueryClientProvider>,
    );
    const opener = document.querySelector('button')!;
    opener.focus();
    const reader = await startReader();

    const open = await reader.step('⌘K opens the palette with focus in its combobox', async () => {
      await act(async () => {
        window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true, bubbles: true, cancelable: true }));
      });
      await settle();
    });
    expect(dialog()).not.toBeNull();
    expect(document.activeElement).toBe(input());
    expect(open[0]).toBe('dialog, Command palette');
    expect(open[1]).toMatch(/^combobox, .*active descendant/);

    const arrow = await reader.step('ArrowDown twice, then report focus (the active option is in the combobox phrase)', async () => {
      keyOnInput('ArrowDown');
      keyOnInput('ArrowDown');
      await settle();
      await reportFocus();
    });
    expect(input().getAttribute('aria-activedescendant')).toBe('cmdk-option-2');
    const active = document.getElementById('cmdk-option-2')!;
    expect(active.getAttribute('aria-selected')).toBe('true');
    expect(arrow).toHaveLength(1);
    expect(arrow[0]).toContain('active descendant');

    const list = await reader.step('Read the listbox with the virtual cursor', async () => {
      await nextUntil(/^listbox/);
      await nextUntil(/^end of listbox/);
    });
    expect(list.some((phrase) => /^group, /.test(phrase))).toBe(true);
    expect(list.filter((phrase) => /^option, .*, selected/.test(phrase))).toHaveLength(1);

    const noMatch = await reader.step('Type a query nothing matches', async () => {
      await setQuery('zzqx');
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 400));
      });
      await settle();
    });
    expect(borrowerSearch).toHaveBeenCalled();
    expect(noMatch.at(-1)).toBe('polite: No pages, actions, or borrowers match “zzqx”.');

    const close = await reader.step('Escape closes the palette onto the opener', async () => {
      await act(async () => {
        window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
      });
      await settle();
    });
    expect(dialog()).toBeNull();
    expect(document.activeElement).toBe(opener);
    expect(close).toContain('button, Opener');

    await expectTranscript('command-palette', reader);
  }, 30_000);
});
