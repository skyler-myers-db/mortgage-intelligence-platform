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
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { installLocalStorage } from '../../test/installLocalStorage';
import { OPEN_SHORTCUTS_EVENT, openShortcutOverlay, registerKeyBinding } from '../../lib/keymap';
import { clearSingleKeyShortcutsPreference, setSingleKeyShortcutsEnabled } from '../../lib/keymapPreference';
import { ShortcutOverlayHost } from './ShortcutOverlayHost';

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
    await vi.waitFor(() => expect(sheet()).not.toBeNull());
    return sheet()!;
  }

  it('opens on the mip:open-shortcuts event and lists the page scope before the global one', async () => {
    expect(OPEN_SHORTCUTS_EVENT).toBe('mip:open-shortcuts');
    act(() => openShortcutOverlay());
    const panel = await waitForSheet();

    expect(panel.getAttribute('role')).toBe('dialog');
    expect(panel.getAttribute('aria-modal')).toBe('true');
    const groups = [...panel.querySelectorAll('.cmdk__group-label')].map((label) => label.textContent);
    expect(groups).toEqual(['Ranked borrowers table', 'Everywhere']);
    const next = [...panel.querySelectorAll('.cmdk__row')].find((row) => row.textContent?.includes('Next borrower'));
    expect([...(next?.querySelectorAll('kbd') ?? [])].map((kbd) => kbd.textContent)).toEqual(['J', '↓']);
    // The sheet's own `?` binding is listed with the rest.
    expect(panel.textContent).toContain('Show keyboard shortcuts');
    expect(panel.querySelector('[data-testid="shortcut-sheet-off"]')).toBeNull();
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
