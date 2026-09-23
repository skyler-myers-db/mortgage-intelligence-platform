/**
 * @vitest-environment happy-dom
 *
 * The topbar's `/` shortcut, now a global binding in the shared keymap
 * (audit wow-power-4): `/` focuses the borrower search from the page, never
 * while typing in a field, never over a modal layer, and not at all with the
 * Console's single-key switch off. The `?` sheet lists it.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { installLocalStorage } from '../../test/installLocalStorage';
import { listKeyBindings } from '../../lib/keymap';
import { clearSingleKeyShortcutsPreference, setSingleKeyShortcutsEnabled } from '../../lib/keymapPreference';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../../lib/api', () => ({ api: { borrowerSearch: vi.fn().mockResolvedValue([]) } }));
vi.mock('../AppContext', () => ({
  useApp: () => ({
    lender: 'Summit Mortgage',
    theme: 'dark',
    setTheme: vi.fn(),
    genieOpen: false,
    setGenieOpen: vi.fn(),
    consoleOpen: false,
    setConsoleOpen: vi.fn(),
  }),
}));
vi.mock('../HealthProvider', () => ({ useHealth: () => ({ health: null }) }));
vi.mock('../FootprintProvider', () => ({ useFootprint: () => ({ usingFallback: false }) }));

import { Topbar } from './Topbar';

describe('Topbar / shortcut through the keymap', () => {
  let container: HTMLDivElement;
  let root: Root;
  const strays: HTMLElement[] = [];

  beforeEach(() => {
    installLocalStorage();
    clearSingleKeyShortcutsPreference();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => root.render(<MemoryRouter><Topbar /></MemoryRouter>));
  });

  afterEach(() => {
    strays.splice(0).forEach((el) => el.remove());
    act(() => root.unmount());
    container.remove();
  });

  const search = () => container.querySelector<HTMLInputElement>('input[aria-label="Search borrowers"]')!;

  function slash(target: EventTarget = document.body): KeyboardEvent {
    const event = new KeyboardEvent('keydown', { key: '/', bubbles: true, cancelable: true });
    act(() => {
      target.dispatchEvent(event);
    });
    return event;
  }

  it('registers `/` in the global scope and focuses the search on it', () => {
    expect(listKeyBindings().some((binding) => binding.id === 'topbar-search' && binding.scope === 'global')).toBe(true);
    const event = slash();
    expect(document.activeElement).toBe(search());
    expect(event.defaultPrevented).toBe(true);
  });

  it('never fires while typing in a field or over a modal layer', () => {
    const field = document.createElement('textarea');
    document.body.appendChild(field);
    strays.push(field);
    field.focus();
    slash(field);
    expect(document.activeElement).toBe(field);

    field.blur();
    const drawer = document.createElement('aside');
    drawer.setAttribute('role', 'dialog');
    drawer.setAttribute('aria-modal', 'true');
    document.body.appendChild(drawer);
    strays.push(drawer);
    slash();
    expect(document.activeElement).not.toBe(search());
  });

  it('is off with the Console single-key switch off', () => {
    setSingleKeyShortcutsEnabled(false);
    const event = slash();
    expect(document.activeElement).not.toBe(search());
    expect(event.defaultPrevented).toBe(false);
  });
});
