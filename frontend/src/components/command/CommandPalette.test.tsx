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
    canAccessAdmin: false,
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
    expect(container.textContent).not.toContain('Admin');
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

  it('runs an action when a row is CLICKED (mouse path), not just on Enter', () => {
    pressMetaK();
    setQuery('analytics');
    const firstOption = container.querySelector<HTMLButtonElement>('[role="option"]')!;
    expect(firstOption.textContent).toContain('Analytics');
    act(() => firstOption.click());
    expect(navigate).toHaveBeenCalledWith('/analytics');
    expect(dialog()).toBeNull();
  });

  it('closes on a backdrop mousedown but not on a click inside the panel', () => {
    pressMetaK();
    const panel = dialog()!;
    // Click inside the panel: stays open.
    act(() => panel.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })));
    expect(dialog()).not.toBeNull();
    // Mousedown on the backdrop (the .cmdk presentation root): closes.
    const backdrop = container.querySelector('.cmdk')!;
    act(() => backdrop.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })));
    expect(dialog()).toBeNull();
  });

  it('shows an empty state when nothing matches', () => {
    pressMetaK();
    setQuery('zzzznope');
    expect(container.querySelectorAll('[role="option"]').length).toBe(0);
    expect(container.querySelector('.cmdk__empty')!.textContent).toContain('No pages, actions, or borrowers');
  });

  it('resets the query when toggled closed and reopened with ⌘K (state hygiene)', () => {
    pressMetaK();
    setQuery('admin');
    expect(input().value).toBe('admin');
    pressMetaK(); // toggle closed
    expect(dialog()).toBeNull();
    pressMetaK(); // reopen
    expect(input().value).toBe(''); // not the stale "admin"
  });
});

describe('CommandPalette borrower search (networked path)', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => root.render(<CommandPalette />));
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.useRealTimers();
  });

  const input = () => container.querySelector<HTMLInputElement>('input[role="combobox"]')!;
  const dialog = () => container.querySelector('[role="dialog"]');
  function pressMetaK() {
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true, bubbles: true, cancelable: true }));
    });
  }
  function setQuery(value: string) {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
    act(() => {
      setter.call(input(), value);
      input().dispatchEvent(new Event('input', { bubbles: true }));
    });
  }
  function keyOnInput(key: string) {
    act(() => {
      input().dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
    });
  }
  const lead = (id: string) =>
    ({ borrower_id: id, city: 'Chicago', state: 'IL', zip: '60611' }) as unknown as import('../../types').LeadSummary;

  it('merges live borrower rows under the actions and navigates to the dossier on Enter', async () => {
    borrowerSearch.mockResolvedValue([lead('B-1EEEN00S99GXC'), lead('B-0CPWBTJMAPFY2')]);
    pressMetaK();
    setQuery('60611');
    // Debounced 160ms search; advance + flush the resolved promise.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(220);
    });
    expect(borrowerSearch).toHaveBeenCalled();
    const borrowerGroup = container.querySelector('[aria-label="Borrowers"]');
    expect(borrowerGroup).not.toBeNull();
    expect(borrowerGroup!.textContent).toContain('B-1EEEN00S99GXC');
    // The borrower rows come AFTER the (filtered) actions; move to the first
    // borrower and Enter → navigate to its dossier + close.
    const firstBorrowerOption = Array.from(container.querySelectorAll<HTMLElement>('[role="option"]')).find((o) =>
      o.textContent?.includes('B-1EEEN00S99GXC'),
    )!;
    const idx = firstBorrowerOption.getAttribute('data-cmd-index')!;
    // Drive activeIndex to that row via ArrowDown from 0.
    for (let i = 0; i < Number(idx); i += 1) keyOnInput('ArrowDown');
    keyOnInput('Enter');
    expect(navigate).toHaveBeenCalledWith('/borrower-360/B-1EEEN00S99GXC');
    expect(dialog()).toBeNull();
  });

  it('navigates to the dossier when a borrower row is CLICKED', async () => {
    borrowerSearch.mockResolvedValue([lead('B-1IB0UGBTFYM20')]);
    pressMetaK();
    setQuery('chicago');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(220);
    });
    const row = Array.from(container.querySelectorAll<HTMLButtonElement>('[role="option"]')).find((o) =>
      o.textContent?.includes('B-1IB0UGBTFYM20'),
    )!;
    act(() => row.click());
    expect(navigate).toHaveBeenCalledWith('/borrower-360/B-1IB0UGBTFYM20');
  });

  it('caps borrower rows at the MAX and shows an error state on search failure', async () => {
    borrowerSearch.mockResolvedValue(
      Array.from({ length: 20 }, (_, i) => lead(`B-CAP${String(i).padStart(10, '0')}`)),
    );
    pressMetaK();
    setQuery('cook');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(220);
    });
    const borrowerOptions = Array.from(container.querySelectorAll('[role="option"]')).filter((o) =>
      o.textContent?.includes('B-CAP'),
    );
    expect(borrowerOptions.length).toBeLessThanOrEqual(6); // MAX_BORROWERS

    borrowerSearch.mockRejectedValue(new Error('warehouse down'));
    setQuery('cooks');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(220);
    });
    expect(container.querySelector('.cmdk__status--error')).not.toBeNull();
  });
});
