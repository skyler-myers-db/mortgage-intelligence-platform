/**
 * @vitest-environment happy-dom
 *
 * The `?` shortcut sheet (audit wow-power-4): opens on `?` and on the
 * `mip:open-shortcuts` window event (the identity menu dispatches it),
 * lists exactly the bindings registered right now (a page's scope first,
 * then the global ones), marks single-key shortcuts "Off" while the Console
 * switch is off, and closes on Escape through the shared layer stack.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { installLocalStorage } from '../../test/installLocalStorage';
import { OPEN_SHORTCUTS_EVENT, openShortcutOverlay, registerKeyBinding } from '../../lib/keymap';
import { clearSingleKeyShortcutsPreference, setSingleKeyShortcutsEnabled } from '../../lib/keymapPreference';
import { ShortcutOverlayHost } from './ShortcutOverlayHost';
import { leadTableHotkeys } from '../mortgage/LeadTable.keymap';

// The sheet is its own lazy chunk: transform it once up front, so the host's
// dynamic import resolves in a few microtasks rather than racing a loaded
// machine's on-demand transform against vi.waitFor's default one second.
beforeAll(async () => {
  await import('./ShortcutOverlay');
}, 60_000);

describe('ShortcutOverlay', () => {
  let container: HTMLDivElement;
  let root: Root;
  const offs: Array<() => void> = [];

  beforeEach(() => {
    installLocalStorage();
    clearSingleKeyShortcutsPreference();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    offs.push(registerKeyBinding({
      id: 'test-next',
      scope: 'lead-queue',
      keys: ['j', 'ArrowDown'],
      description: 'Next borrower',
      run: vi.fn(),
    }));
    offs.push(registerKeyBinding({
      id: 'test-palette',
      scope: 'global',
      keys: ['Mod+K'],
      description: 'Open or close the command palette',
      run: vi.fn(),
    }));
    act(() => root.render(<ShortcutOverlayHost />));
  });

  afterEach(() => {
    while (offs.length > 0) offs.pop()?.();
    act(() => root.unmount());
    container.remove();
  });

  const sheet = () => document.querySelector<HTMLElement>('[data-testid="shortcut-sheet"]');

  async function waitForSheet(): Promise<HTMLElement> {
    await vi.waitFor(() => expect(sheet()).not.toBeNull(), { timeout: 10_000 });
    return sheet()!;
  }

  it('opens on the mip:open-shortcuts event and lists the page scope before the global one', async () => {
    expect(OPEN_SHORTCUTS_EVENT).toBe('mip:open-shortcuts');
    act(() => openShortcutOverlay());
    const panel = await waitForSheet();

    // A native modal <dialog class="cmdk"> named by its heading (stack-05);
    // the panel keeps the test id and carries no dialog role of its own.
    const dialog = panel.closest('dialog');
    expect(dialog?.classList.contains('cmdk')).toBe(true);
    expect(dialog?.open).toBe(true);
    expect(dialog?.hasAttribute('role')).toBe(false);
    expect(document.getElementById(dialog?.getAttribute('aria-labelledby') ?? '')?.textContent).toBe('Keyboard shortcuts');
    expect(panel.hasAttribute('role')).toBe(false);
    const groups = [...panel.querySelectorAll('.cmdk__group-label')].map((label) => label.textContent);
    expect(groups).toEqual(['Ranked borrowers table', 'Everywhere']);
    const next = [...panel.querySelectorAll('.cmdk__row')].find((row) => row.textContent?.includes('Next borrower'));
    expect([...(next?.querySelectorAll('kbd') ?? [])].map((kbd) => kbd.textContent)).toEqual(['J', '↓']);
    // The sheet's own `?` binding is listed with the rest.
    expect(panel.textContent).toContain('Show keyboard shortcuts');
    expect(panel.querySelector('[data-testid="shortcut-sheet-off"]')).toBeNull();
  });

  it('lists Shift+A and Shift+R on one row, so the table group does not scroll (tables-07)', async () => {
    const openBulkGate = vi.fn();
    const openBulkRejectGate = vi.fn();
    const hotkeys = leadTableHotkeys({
      approverActive: true,
      move: vi.fn(),
      toggleCursorRow: () => false,
      toggleSelectCursorRow: vi.fn(),
      extendSelectionToCursor: vi.fn(),
      reviewCursorRow: vi.fn(),
      rejectCursorRow: vi.fn(),
      openBulkGate,
      openBulkRejectGate,
    });
    for (const hotkey of hotkeys) {
      offs.push(registerKeyBinding({
        id: `sheet-${hotkey.id}`,
        scope: 'lead-queue',
        keys: hotkey.keys,
        description: hotkey.description,
        run: (event) => hotkey.run(event, null),
      }));
    }
    act(() => openShortcutOverlay());
    const panel = await waitForSheet();
    const bulk = [...panel.querySelectorAll('.cmdk__row')].filter((row) => row.textContent?.includes('selected borrowers'));
    expect(bulk).toHaveLength(1);
    expect(bulk[0].textContent).toContain('Approve or reject the selected borrowers');
    const keys = [...bulk[0].querySelectorAll('kbd')].map((kbd) => kbd.textContent).join(' ');
    expect(keys).toContain('A');
    expect(keys).toContain('R');

    const bulkHotkey = hotkeys.find((hotkey) => hotkey.id === 'bulk-approve')!;
    bulkHotkey.run(new KeyboardEvent('keydown', { key: 'R', shiftKey: true }), null);
    expect(openBulkRejectGate).toHaveBeenCalledTimes(1);
    bulkHotkey.run(new KeyboardEvent('keydown', { key: 'A', shiftKey: true }), null);
    expect(openBulkGate).toHaveBeenCalledTimes(1);
  });

  it('opens on ? and closes on Escape', async () => {
    act(() => {
      document.body.dispatchEvent(new KeyboardEvent('keydown', { key: '?', shiftKey: true, bubbles: true, cancelable: true }));
    });
    await waitForSheet();

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    });
    expect(sheet()).toBeNull();
  });

  it('marks single-key shortcuts Off (and says why) while the Console switch is off', async () => {
    setSingleKeyShortcutsEnabled(false);
    // `?` itself is a single-key shortcut: it is off too, the event still opens.
    act(() => {
      document.body.dispatchEvent(new KeyboardEvent('keydown', { key: '?', shiftKey: true, bubbles: true, cancelable: true }));
    });
    expect(sheet()).toBeNull();
    act(() => openShortcutOverlay());
    const panel = await waitForSheet();

    expect(panel.querySelector('[data-testid="shortcut-sheet-off"]')?.textContent).toContain('Single-key shortcuts are off');
    const rows = [...panel.querySelectorAll('.cmdk__row')];
    const next = rows.find((row) => row.textContent?.includes('Next borrower'));
    const palette = rows.find((row) => row.textContent?.includes('command palette'));
    expect(next?.querySelector('.cmdk__keys-off')?.textContent).toBe('Off');
    expect(palette?.querySelector('.cmdk__keys-off')).toBeNull();
  });
});
