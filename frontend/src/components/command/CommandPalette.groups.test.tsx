/**
 * @vitest-environment happy-dom
 *
 * The ⌘K palette's provider groups (2026-09-21 audit shell-07 items 2 and 3,
 * tables-09 phase 2): Saved views, Segments, Glossary, Saved leads, Recent
 * borrowers and an administrator's Data assets, rendered in the palette's
 * order with the prototype `.cmdk__group` / `.cmdk__row` BEM. Every read is
 * audit-free and runs only while the palette is open; a row with a live
 * keymap binding shows its keycaps and carries aria-keyshortcuts.
 */
import { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { installLocalStorage } from '../../test/installLocalStorage';
import { OPEN_SHORTCUTS_EVENT, listKeyBindings, registerKeyBinding } from '../../lib/keymap';
import { clearSingleKeyShortcutsPreference, setSingleKeyShortcutsEnabled } from '../../lib/keymapPreference';
import { segmentName } from '../../lib/segmentMetadata';
import type { SavedLead } from '../../types/workspace';

const navigate = vi.fn();
vi.mock('react-router', () => ({ useNavigate: () => navigate }));

const app = vi.hoisted(() => ({
  admin: false,
  sessionStatus: 'ready' as string,
  savedLeads: {} as Record<string, SavedLead>,
}));
vi.mock('../AppContext', () => ({
  useApp: () => ({
    theme: 'dark',
    setTheme: vi.fn(),
    consoleOpen: false,
    setConsoleOpen: vi.fn(),
    setGenieOpen: vi.fn(),
    canAccessAdmin: app.admin,
    sessionStatus: app.sessionStatus,
    savedLeads: app.savedLeads,
  }),
}));
vi.mock('../../lib/sessionQuery', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/sessionQuery')>()),
  useAuditLedgerAccess: () => app.admin,
}));

const calls = vi.hoisted(() => ({
  borrowerSearch: vi.fn(),
  myAuditEvents: vi.fn(),
  dataEstate: vi.fn(),
  fetchSavedViews: vi.fn(),
}));
vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: {
    borrowerSearch: (...a: unknown[]) => calls.borrowerSearch(...a),
    myAuditEvents: (...a: unknown[]) => calls.myAuditEvents(...a),
    dataEstate: (...a: unknown[]) => calls.dataEstate(...a),
  },
}));
vi.mock('../../lib/apiClients/savedViews', () => ({
  fetchSavedViews: (...a: unknown[]) => calls.fetchSavedViews(...a),
}));
vi.mock('../../lib/routePreloaders', () => ({ preloadRouteForPath: vi.fn() }));

import { CommandPalette, loadCommandPaletteDialog } from './CommandPalette';

beforeAll(async () => {
  await loadCommandPaletteDialog();
}, 60_000);

const RECENT = ['B-0000000000001', 'B-0000000000002'];

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.clearAllMocks();
  installLocalStorage();
  clearSingleKeyShortcutsPreference();
  app.admin = false;
  app.sessionStatus = 'ready';
  app.savedLeads = {};
  calls.borrowerSearch.mockResolvedValue([]);
  calls.fetchSavedViews.mockResolvedValue({
    saved_views: [{ view_id: 'v1', name: 'Texas refi', params: 'state=TX&segment=itm', created_at: '', updated_at: '' }],
  });
  calls.myAuditEvents.mockResolvedValue({
    items: [
      ...RECENT.map((subject_id) => ({ event_type: 'VIEW_BORROWER', entity_type: 'borrower', subject_id, created_at: '' })),
      { event_type: 'VIEW_LEADS', entity_type: 'lead_queue', subject_id: null, created_at: '' },
    ],
    next_cursor: null,
  });
  calls.dataEstate.mockResolvedValue({
    lanes: [{
      id: 'silver', title: 'Silver', description: '', status: 'live',
      assets: [{ name: 'property_master', label: 'Property master', status: 'live', uc_object: 'mip.silver.property_master', note: '' }],
    }],
  });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => root.render(<QueryClientProvider client={new QueryClient()}><CommandPalette /></QueryClientProvider>));
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function toggle() {
  act(() => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true, bubbles: true, cancelable: true }));
  });
}
const input = () => container.querySelector<HTMLInputElement>('input[role="combobox"]')!;
function setQuery(value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
  act(() => {
    setter.call(input(), value);
    input().dispatchEvent(new Event('input', { bubbles: true }));
  });
}
async function settle() {
  for (const ms of [200, 20, 20]) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, ms));
    });
  }
}
const groupNames = () => [...container.querySelectorAll('dialog.cmdk[open] [role="group"]')].map((g) => g.getAttribute('aria-label'));
const group = (name: string) => container.querySelector(`dialog.cmdk[open] [role="group"][aria-label="${name}"]`);
const rowsOf = (name: string) => [...(group(name)?.querySelectorAll<HTMLElement>('[role="option"]') ?? [])];

describe('palette provider groups', () => {
  it('an empty query lists Pages & actions, Saved views and Recent borrowers, in order', async () => {
    toggle();
    await settle();
    expect(groupNames()).toEqual(['Pages and actions', 'Saved views', 'Recent borrowers']);
    expect(rowsOf('Recent borrowers').map((row) => row.textContent)).toEqual([
      expect.stringContaining(RECENT[0]),
      expect.stringContaining(RECENT[1]),
    ]);
    // Pages & actions keeps registry order with an empty query.
    expect(rowsOf('Pages and actions')[0].textContent).toContain('Home');
  });

  it('"refi" leads with the Segment Intelligence page and renders Segments and Glossary', async () => {
    toggle();
    setQuery('refi');
    await settle();
    expect(groupNames()).toEqual(['Pages and actions', 'Saved views', 'Segments', 'Glossary', 'Ask Genie']);
    expect(rowsOf('Pages and actions')[0].textContent).toContain('Segment Intelligence');
    expect(rowsOf('Segments').map((row) => row.textContent)).toContainEqual(expect.stringContaining(segmentName('itm')));
    expect(rowsOf('Saved views')[0].textContent).toContain('Texas refi');
  });

  it('reads my-events once per open, only while open and the session is ready', async () => {
    toggle();
    await settle();
    expect(calls.myAuditEvents).toHaveBeenCalledTimes(1);
    expect(calls.myAuditEvents.mock.calls[0][0]).toBe(25);
    expect(calls.fetchSavedViews).toHaveBeenCalledTimes(1);
    toggle();
    await settle();
    expect(calls.myAuditEvents).toHaveBeenCalledTimes(1);
    toggle();
    await settle();
    expect(calls.myAuditEvents).toHaveBeenCalledTimes(2);
  });

  it('reads no my-events before the session is ready', async () => {
    app.sessionStatus = 'loading';
    toggle();
    await settle();
    expect(calls.myAuditEvents).not.toHaveBeenCalled();
    expect(groupNames()).not.toContain('Recent borrowers');
  });

  it('a recent borrower opens its dossier on Enter (the dossier is the audited read)', async () => {
    toggle();
    await settle();
    const target = rowsOf('Recent borrowers')[0];
    const index = Number(target.getAttribute('data-cmd-index'));
    for (let i = 0; i < index; i += 1) {
      act(() => {
        input().dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true }));
      });
    }
    act(() => {
      input().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
    });
    expect(navigate).toHaveBeenCalledWith(`/borrower-360/${RECENT[0]}`);
  });

  it('a saved view opens the Lead Queue with its exact params', async () => {
    toggle();
    await settle();
    act(() => rowsOf('Saved views')[0].click());
    expect(navigate).toHaveBeenCalledWith('/lead-queue?state=TX&segment=itm');
  });

  it('lists a borrower once: a saved lead hides its recent row and its live row', async () => {
    app.savedLeads = {
      [RECENT[0]]: { borrower_id: RECENT[0], city: 'Chicago', state: 'IL', zip: '60611', saved_at: '1', updated_at: '1' },
    };
    calls.borrowerSearch.mockResolvedValue([
      { borrower_id: RECENT[0], city: 'Chicago', state: 'IL', zip: '60611' },
      { borrower_id: 'B-0000000000009', city: 'Chicago', state: 'IL', zip: '60611' },
    ]);
    toggle();
    setQuery('B-000');
    await settle();
    const listed = [...container.querySelectorAll('dialog.cmdk[open] [role="option"]')]
      .map((row) => row.textContent ?? '')
      .filter((text) => text.includes(RECENT[0]));
    expect(listed).toHaveLength(1);
    expect(rowsOf('Saved leads')[0].textContent).toContain(RECENT[0]);
    expect(rowsOf('Recent borrowers').map((row) => row.textContent)).toEqual([expect.stringContaining(RECENT[1])]);
    expect(rowsOf('Borrowers').map((row) => row.textContent)).toEqual([expect.stringContaining('B-0000000000009')]);
  });

  it('never reads the data estate for a non-administrator', async () => {
    toggle();
    setQuery('property');
    await settle();
    expect(calls.dataEstate).not.toHaveBeenCalled();
    expect(group('Data assets')).toBeNull();
  });

  it('reads the data estate once for an administrator, after two typed characters', async () => {
    app.admin = true;
    toggle();
    setQuery('p');
    await settle();
    expect(calls.dataEstate).not.toHaveBeenCalled();
    setQuery('property');
    await settle();
    expect(calls.dataEstate).toHaveBeenCalledTimes(1);
    expect(groupNames().slice(-2)).toEqual(['Data assets', 'Ask Genie']);
    act(() => rowsOf('Data assets')[0].click());
    expect(navigate).toHaveBeenCalledWith('/data-estate/assets/property_master');
  });

  // The listbox's children in order: a group by its name, a line by its class.
  const listOrder = () => [...(container.querySelector('dialog.cmdk[open] [role="listbox"]')?.children ?? [])]
    .map((child) => child.getAttribute('aria-label') ?? child.className);

  it.each([
    ['running', () => new Promise<never>(() => undefined), 'cmdk__status'],
    ['failed', () => Promise.reject(new Error('search down')), 'cmdk__status cmdk__status--error'],
  ])('the %s borrower search\'s line sits at the Borrowers place, before Data assets and Ask Genie', async (_state, search, line) => {
    app.admin = true;
    calls.borrowerSearch.mockImplementation(search);
    toggle();
    setQuery('property');
    await settle();
    expect(listOrder().slice(-3)).toEqual([line, 'Data assets', 'Ask Genie']);
  });
});

describe('palette keycaps from the live keymap (shell-07 item 3)', () => {
  const sheetRow = () => rowsOf('Pages and actions').find((row) => row.textContent?.includes('Keyboard shortcuts'));

  it('shows the ? keycap and aria-keyshortcuts while the sheet binding is live', async () => {
    // The shell host (ShortcutOverlayHost, mounted with the palette) binds `?`.
    expect(listKeyBindings().some((binding) => binding.id === 'shortcut-sheet')).toBe(true);
    toggle();
    setQuery('keyboard');
    await settle();
    expect(sheetRow()?.getAttribute('aria-keyshortcuts')).toBe('?');
    expect([...(sheetRow()?.querySelectorAll('.cmdk__row-hint kbd') ?? [])].map((kbd) => kbd.textContent)).toEqual(['?']);

    act(() => setSingleKeyShortcutsEnabled(false));
    expect(sheetRow()?.hasAttribute('aria-keyshortcuts')).toBe(false);
    expect(sheetRow()?.querySelector('kbd')).toBeNull();

    act(() => setSingleKeyShortcutsEnabled(true));
    expect(sheetRow()?.getAttribute('aria-keyshortcuts')).toBe('?');
  });

  it('shows no keycap for a binding this page has not registered (the Lead Queue bulk keys)', async () => {
    toggle();
    setQuery('toggle');
    await settle();
    // No row in the registry outside the verbs and the sheet carries a shortcut.
    expect(container.querySelector('dialog.cmdk[open] [role="option"][aria-keyshortcuts]')).toBeNull();
    let unregister = () => undefined as void;
    act(() => {
      unregister = registerKeyBinding({
        id: 'some-other-binding', scope: 'global', keys: ['g'], description: 'x', run: () => undefined,
      });
    });
    expect(container.querySelector('dialog.cmdk[open] [role="option"][aria-keyshortcuts]')).toBeNull();
    act(() => unregister());
  });

  it('"Keyboard shortcuts" closes the palette, then opens the sheet on the next frame', async () => {
    const opened = vi.fn();
    window.addEventListener(OPEN_SHORTCUTS_EVENT, opened);
    toggle();
    setQuery('keyboard');
    await settle();
    act(() => sheetRow()?.click());
    expect(container.querySelector('dialog.cmdk[open]')).toBeNull();
    expect(opened).not.toHaveBeenCalled();
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(opened).toHaveBeenCalledTimes(1);
    window.removeEventListener(OPEN_SHORTCUTS_EVENT, opened);
  });
});
