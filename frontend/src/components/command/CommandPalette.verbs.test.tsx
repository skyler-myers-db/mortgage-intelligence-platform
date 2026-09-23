/**
 * @vitest-environment happy-dom
 *
 * Cmd-K verbs on the page's selection (audit wow-power-4): the palette lists
 * "Approve N selected…" / "Assign N selected…" only while a page publishes a
 * selection, hides the approve verb whenever the page says the actor cannot
 * approve, and runs a verb through the page's OWN handler (the selection
 * context's `run`), never an approval of its own. The Cmd-K chord is a
 * keymap modifier binding: it still opens the palette with single-key
 * shortcuts switched off.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { installLocalStorage } from '../../test/installLocalStorage';
import { clearSingleKeyShortcutsPreference, setSingleKeyShortcutsEnabled } from '../../lib/keymapPreference';
import { commandVerbActions } from './commandActions';
import { publishCommandSelection, type CommandSelectionContext } from './commandSelection';

vi.mock('react-router', () => ({ useNavigate: () => vi.fn() }));
vi.mock('../AppContext', () => ({
  useApp: () => ({
    theme: 'dark',
    setTheme: vi.fn(),
    consoleOpen: false,
    setConsoleOpen: vi.fn(),
    setGenieOpen: vi.fn(),
    canAccessAdmin: false,
  }),
}));
const approve = vi.fn();
vi.mock('../../lib/api', () => ({
  api: { borrowerSearch: () => Promise.resolve([]), approve: (...args: unknown[]) => approve(...args) },
}));

import { CommandPalette } from './CommandPalette';

function selection(overrides: Partial<CommandSelectionContext> = {}): CommandSelectionContext {
  return {
    selectedCount: 12,
    approveCount: 11,
    canApprove: true,
    canAssign: true,
    run: vi.fn(),
    ...overrides,
  };
}

describe('commandVerbActions', () => {
  it('offers nothing without a selection', () => {
    expect(commandVerbActions(null)).toEqual([]);
    expect(commandVerbActions(selection({ selectedCount: 0 }))).toEqual([]);
  });

  it('names the eligible count and hides approve when the actor cannot approve', () => {
    expect(commandVerbActions(selection()).map((action) => action.label))
      .toEqual(['Approve 11 selected…', 'Assign 12 selected…']);
    expect(commandVerbActions(selection({ canApprove: false })).map((action) => action.label))
      .toEqual(['Assign 12 selected…']);
    expect(commandVerbActions(selection({ approveCount: 0, canAssign: false }))).toEqual([]);
  });
});

describe('CommandPalette selection verbs', () => {
  let container: HTMLDivElement;
  let root: Root;
  let unpublish: (() => void) | null = null;

  beforeEach(() => {
    vi.clearAllMocks();
    installLocalStorage();
    clearSingleKeyShortcutsPreference();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => root.render(<CommandPalette />));
  });
  afterEach(() => {
    unpublish?.();
    unpublish = null;
    act(() => root.unmount());
    container.remove();
  });

  function openPalette() {
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true, bubbles: true, cancelable: true }));
    });
  }
  const verbGroup = () => container.querySelector('[role="group"][aria-label="Selected borrowers"]');

  it('lists the verbs first and runs them through the page handler, not an approval', () => {
    const context = selection();
    act(() => {
      unpublish = publishCommandSelection(context);
    });
    openPalette();

    const rows = [...(verbGroup()?.querySelectorAll('[role="option"]') ?? [])];
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringContaining('Approve 11 selected…'),
      expect.stringContaining('Assign 12 selected…'),
    ]);
    // The first option of the whole list is the approve verb.
    expect(container.querySelector('[data-cmd-index="0"]')?.textContent).toContain('Approve 11 selected…');

    act(() => (rows[0] as HTMLButtonElement).click());

    expect(context.run).toHaveBeenCalledWith('approve-selected');
    expect(approve).not.toHaveBeenCalled();
    expect(container.querySelector('[role="dialog"]')).toBeNull();
  });

  it('shows no approve verb for a selection the actor cannot approve', () => {
    act(() => {
      unpublish = publishCommandSelection(selection({ canApprove: false }));
    });
    openPalette();
    expect(verbGroup()?.textContent).not.toContain('Approve');
    expect(verbGroup()?.textContent).toContain('Assign 12 selected…');
  });

  it('drops the verbs when the page clears its selection', () => {
    act(() => {
      unpublish = publishCommandSelection(selection());
    });
    act(() => {
      unpublish?.();
      unpublish = null;
    });
    openPalette();
    expect(verbGroup()).toBeNull();
  });

  it('Cmd-K is a modifier chord: it opens with single-key shortcuts switched off', () => {
    setSingleKeyShortcutsEnabled(false);
    openPalette();
    expect(container.querySelector('[role="dialog"][aria-label="Command palette"]')).not.toBeNull();
  });
});
