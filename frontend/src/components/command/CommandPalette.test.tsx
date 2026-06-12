/**
 * @vitest-environment happy-dom
 *
 * ⌘K command palette contract (re-audit #4 Buyer-Wow #1): opens on the
 * chord, exposes an accessible combobox+listbox, arrow keys move
 * aria-activedescendant, Enter routes, Esc closes. Borrower search is
 * mocked (the palette merges live rows under the local actions).
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const navigate = vi.fn();
vi.mock('react-router-dom', () => ({ useNavigate: () => navigate }));

const setTheme = vi.fn();
const setConsoleOpen = vi.fn();
const setGenieOpen = vi.fn();
vi.mock('../AppContext', () => ({
  useApp: () => ({
    theme: 'dark',
    setTheme,
    consoleOpen: false,
    setConsoleOpen,
    setGenieOpen,
  }),
}));

const borrowerSearch = vi.fn();
vi.mock('../../lib/api', () => ({
  api: { borrowerSearch: (...a: unknown[]) => borrowerSearch(...a) },
}));

import { CommandPalette } from './CommandPalette';

describe('CommandPalette', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    borrowerSearch.mockResolvedValue([]);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => root.render(<CommandPalette />));
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function pressMetaK() {
    act(() => {
      window.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'k', metaKey: true, bubbles: true, cancelable: true }),
      );
    });
  }
  const dialog = () => container.querySelector('[role="dialog"]');
  const input = () => container.querySelector<HTMLInputElement>('input[role="combobox"]')!;
  function keyOnInput(key: string) {
    act(() => {
      input().dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
    });
  }
  function setQuery(value: string) {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
    act(() => {
      setter.call(input(), value);
      input().dispatchEvent(new Event('input', { bubbles: true }));
    });
  }

  it('is closed until the ⌘K chord, then renders an accessible dialog/combobox/listbox', () => {
    expect(dialog()).toBeNull();
    pressMetaK();
    expect(dialog()).not.toBeNull();
    expect(dialog()!.getAttribute('aria-modal')).toBe('true');
    const combo = input();
    expect(combo.getAttribute('aria-controls')).toBe('cmdk-listbox');
    expect(container.querySelector('#cmdk-listbox')!.getAttribute('role')).toBe('listbox');
    // First option is active by default.
    expect(combo.getAttribute('aria-activedescendant')).toBe('cmdk-option-0');
    expect(container.querySelector('#cmdk-option-0')!.getAttribute('aria-selected')).toBe('true');
  });

  it('toggles closed on a second ⌘K', () => {
    pressMetaK();
    expect(dialog()).not.toBeNull();
    pressMetaK();
    expect(dialog()).toBeNull();
  });

  it('moves aria-activedescendant with ArrowDown/ArrowUp (wrapping)', () => {
    pressMetaK();
    const optionCount = container.querySelectorAll('[role="option"]').length;
    expect(optionCount).toBeGreaterThan(1);
    keyOnInput('ArrowDown');
    expect(input().getAttribute('aria-activedescendant')).toBe('cmdk-option-1');
    keyOnInput('ArrowUp');
    keyOnInput('ArrowUp'); // wrap to last
    expect(input().getAttribute('aria-activedescendant')).toBe(`cmdk-option-${optionCount - 1}`);
  });

  it('filters actions as the operator types and routes on Enter', () => {
    pressMetaK();
    setQuery('lead');
    // Lead Queue is the top match.
    const firstOption = container.querySelector('[role="option"]')!;
    expect(firstOption.textContent).toContain('Lead Queue');
    keyOnInput('Enter');
    expect(navigate).toHaveBeenCalledWith('/lead-queue');
    // Routing closes the palette.
    expect(dialog()).toBeNull();
  });

  it('runs a workspace command (toggle theme) and closes', () => {
    pressMetaK();
    setQuery('toggle theme');
    keyOnInput('Enter');
    expect(setTheme).toHaveBeenCalledWith('light'); // current theme is dark
    expect(dialog()).toBeNull();
  });

  it('closes on Escape', () => {
    pressMetaK();
    expect(dialog()).not.toBeNull();
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    });
    expect(dialog()).toBeNull();
  });
});
