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
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { consumeGeniePrefill } from '../../lib/genieOpen';
import { publishCommandSelection } from './commandSelection';

const navigate = vi.fn();
vi.mock('react-router', () => ({ useNavigate: () => navigate }));

const setTheme = vi.fn();
const setConsoleOpen = vi.fn();
const setGenieOpen = vi.fn();
const access = vi.hoisted(() => ({ admin: false }));
vi.mock('../AppContext', () => ({
  useApp: () => ({
    theme: 'dark',
    setTheme,
    consoleOpen: false,
    setConsoleOpen,
    setGenieOpen,
    canAccessAdmin: access.admin,
  }),
}));

const borrowerSearch = vi.fn();
vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: { borrowerSearch: (...a: unknown[]) => borrowerSearch(...a) },
}));

// The active-row chunk preload (audit bundle-09 item 2) is observed, never run.
const preloadRouteForPath = vi.fn();
vi.mock('../../lib/routePreloaders', () => ({ preloadRouteForPath: (path: string) => preloadRouteForPath(path) }));
const saveData = vi.hoisted(() => ({ on: false }));
vi.mock('../../lib/prefetch', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/prefetch')>()),
  saveDataRequested: () => saveData.on,
}));

import { CommandPalette, loadCommandPaletteDialog } from './CommandPalette';


// The palette dialog is a lazy chunk behind the shell host (audit bundle-04):
// load it once up front, as the idle preload leaves it in the app, so the
// host mounts it at once and ⌘K opens it synchronously.
beforeAll(async () => {
  await loadCommandPaletteDialog();
}, 60_000);

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
  // The palette is a native <dialog class="cmdk"> that stays mounted once
  // rendered (stack-05 / motion-01): "open" is its `open` attribute.
  const dialog = () => container.querySelector<HTMLDialogElement>('dialog.cmdk[open]');
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
    // Named by the dialog itself; role and modality are implicit.
    expect(dialog()!.getAttribute('aria-label')).toBe('Command palette');
    expect(dialog()!.hasAttribute('role')).toBe(false);
    expect(dialog()!.hasAttribute('aria-modal')).toBe(false);
    expect(container.querySelector('.cmdk__panel')!.hasAttribute('role')).toBe(false);
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

  /**
   * Follow-up #5: ⌘K checked only for an open native `<dialog>`, so it
   * opened the palette over the aria-modal layers too (the session dialog,
   * the evidence drawer, the ? sheet) and fought their focus traps.
   */
  describe('never opens over a modal layer', () => {
    function withLayer(markup: string, run: () => void) {
      const host = document.createElement('div');
      host.innerHTML = markup;
      document.body.appendChild(host);
      try {
        run();
      } finally {
        host.remove();
      }
    }

    it('stays closed under an open native dialog', () => {
      withLayer('<dialog open><form>Leave without saving?</form></dialog>', () => {
        pressMetaK();
        expect(dialog()).toBeNull();
      });
    });

    it('stays closed under an aria-modal layer', () => {
      withLayer('<div role="dialog" aria-modal="true" aria-label="Your session ended"></div>', () => {
        pressMetaK();
        expect(dialog()).toBeNull();
      });
    });

    it('ignores a closed aria-modal drawer that is still rendered but hidden', () => {
      withLayer('<aside class="drawer" role="dialog" aria-modal="true" aria-hidden="true"></aside>', () => {
        pressMetaK();
        expect(dialog()).not.toBeNull();
      });
    });

    it('still closes an open palette when a modal layer appears', () => {
      pressMetaK();
      expect(dialog()).not.toBeNull();
      withLayer('<div role="dialog" aria-modal="true" aria-label="Keyboard shortcuts"></div>', () => {
        pressMetaK();
        expect(dialog()).toBeNull();
      });
    });
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

  it('closes on a press on the backdrop layer but not on a press inside the panel', () => {
    pressMetaK();
    const press = (target: Element) => act(() => {
      target.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
      target.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    // A press inside the panel: stays open.
    press(container.querySelector('.cmdk__panel')!);
    expect(dialog()).not.toBeNull();
    // A press on the full-viewport .cmdk layer itself (its own backdrop): closes.
    press(dialog()!);
    expect(dialog()).toBeNull();
  });

  it('keeps the last results through its exit: closed, inert, hidden, no active descendant', () => {
    pressMetaK();
    setQuery('analytics');
    expect(input().getAttribute('aria-activedescendant')).toBe('cmdk-option-0');
    pressMetaK(); // toggle closed
    const closing = container.querySelector<HTMLDialogElement>('dialog.cmdk')!;
    expect(closing.open).toBe(false);
    expect(closing.hasAttribute('inert')).toBe(true);
    expect(closing.getAttribute('aria-hidden')).toBe('true');
    expect(input().hasAttribute('aria-activedescendant')).toBe(false);
    // The exit shows what was on screen, not an emptied palette.
    expect(input().value).toBe('analytics');
    expect(closing.querySelector('[role="option"]')?.textContent).toContain('Analytics');
  });

  it('shows an empty state when nothing matches, with the Ask Genie handoff as the only row', async () => {
    pressMetaK();
    setQuery('zzzznope');
    // The empty state waits for the borrower search to settle (shell-07 item 4).
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 220));
    });
    // Since audit 2026-09-21 `shell-07` the palette never dead-ends: the
    // typed text can always be handed to Genie (a prefill, never a submit).
    const options = container.querySelectorAll('[role="option"]');
    expect(options.length).toBe(1);
    expect(options[0].textContent).toContain('Ask Genie: zzzznope');
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
    consumeGeniePrefill();
  });

  const input = () => container.querySelector<HTMLInputElement>('input[role="combobox"]')!;
  const dialog = () => container.querySelector<HTMLDialogElement>('dialog.cmdk[open]');
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

  it('a borrower-only query + Enter at the FIRST row opens the dossier, never a Genie prefill', async () => {
    // The "Ask Genie: <text>" row (shell-07) is the fallback. It must never sit
    // above a borrower match: Enter with no ArrowDown opened the first
    // borrower before that row existed, and still has to.
    borrowerSearch.mockResolvedValue([lead('B-1EEEN00S99GXC')]);
    pressMetaK();
    setQuery('B-1EEEN00S99GXC');
    // Before the search lands the Genie row is the only option. Enter is held
    // there: the masked id must not reach Genie's composer.
    expect(container.querySelectorAll('[role="option"]').length).toBe(1);
    keyOnInput('Enter');
    expect(consumeGeniePrefill()).toBeNull();
    expect(dialog()).not.toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(220);
    });
    const options = Array.from(container.querySelectorAll('[role="option"]')).map((o) => o.textContent ?? '');
    expect(options[0]).toContain('B-1EEEN00S99GXC');
    expect(options[options.length - 1]).toContain('Ask Genie: B-1EEEN00S99GXC');
    expect(input().getAttribute('aria-activedescendant')).toBe('cmdk-option-0');
    keyOnInput('Enter');
    expect(navigate).toHaveBeenCalledWith('/borrower-360/B-1EEEN00S99GXC');
    expect(consumeGeniePrefill()).toBeNull();
    expect(dialog()).toBeNull();
  });

  it('a query no borrower matches still reaches the Genie fallback on Enter once the search settles', async () => {
    borrowerSearch.mockResolvedValue([]);
    pressMetaK();
    setQuery('zyrplax');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(220);
    });
    keyOnInput('Enter');
    expect(consumeGeniePrefill()).toBe('zyrplax');
    expect(navigate).not.toHaveBeenCalled();
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

  it('hides the empty state while the borrower search runs or has failed (shell-07 item 4)', async () => {
    let settle: (rows: unknown[]) => void = () => undefined;
    borrowerSearch.mockReturnValue(new Promise((resolve) => {
      settle = resolve;
    }));
    // Visible lines are aria-hidden; one live region outside the listbox
    // speaks (a listbox may own only options and groups).
    const visible = () => [...container.querySelectorAll('.cmdk__empty, .cmdk__status')].map((el) => el.textContent);
    const spoken = () => [...container.querySelectorAll('[role="status"]')].map((el) => el.textContent);
    pressMetaK();
    setQuery('zz');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(220);
    });
    expect(borrowerSearch).toHaveBeenCalledTimes(1);
    expect(visible()).toEqual(['Searching borrowers…']);
    expect(spoken()).toEqual(['Searching borrowers…']);

    await act(async () => settle([]));
    expect(visible()).toEqual(['No pages, actions, or borrowers match “zz”.']);
    expect(spoken()).toEqual(['No pages, actions, or borrowers match “zz”.']);

    borrowerSearch.mockRejectedValue(new Error('warehouse down'));
    setQuery('zzq');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(220);
    });
    expect(visible()).toEqual(['Borrower search is temporarily unavailable.']);
    expect(spoken()).toEqual(['Borrower search is temporarily unavailable.']);
    expect(container.querySelector('#cmdk-listbox [role="status"]')).toBeNull();
    expect([...container.querySelectorAll('#cmdk-listbox .cmdk__empty, #cmdk-listbox .cmdk__status')].every((el) => el.getAttribute('aria-hidden') === 'true')).toBe(true);
  });
});

describe('CommandPalette active-row chunk preload (bundle-09 item 2)', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    saveData.on = false;
    access.admin = false;
    borrowerSearch.mockResolvedValue([]);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => root.render(<CommandPalette />));
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.useRealTimers();
    consumeGeniePrefill();
  });

  const input = () => container.querySelector<HTMLInputElement>('input[role="combobox"]')!;
  const activeRow = () => container.querySelector<HTMLElement>('.cmdk__row.is-active');
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
  async function settleSearch() {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(220);
    });
  }
  const preloaded = () => preloadRouteForPath.mock.calls.map(([path]) => path as string);

  it('warms the first route row on open and the next one on ArrowDown, and nothing once closed', () => {
    expect(preloadRouteForPath).not.toHaveBeenCalled();
    pressMetaK();
    // A route row's hint is its route pattern (commandActions.routeAction).
    const first = activeRow()?.querySelector('.cmdk__row-hint')?.textContent;
    expect(first).toMatch(/^\//);
    expect(preloadRouteForPath).toHaveBeenLastCalledWith(first);
    keyOnInput('ArrowDown');
    const second = activeRow()?.querySelector('.cmdk__row-hint')?.textContent;
    expect(second).not.toBe(first);
    expect(preloadRouteForPath).toHaveBeenLastCalledWith(second);

    const calls = preloadRouteForPath.mock.calls.length;
    pressMetaK();
    expect(container.querySelector('dialog.cmdk[open]')).toBeNull();
    expect(preloadRouteForPath).toHaveBeenCalledTimes(calls);
    // Chunks only: the borrower search is the palette's one network call.
    expect(borrowerSearch).not.toHaveBeenCalled();
  });

  it("warms the dossier chunk for a borrower row (the id never leaves the palette's own path)", async () => {
    const lead = { borrower_id: 'B-1EEEN00S99GXC', city: 'Chicago', state: 'IL', zip: '60611' };
    borrowerSearch.mockResolvedValue([lead]);
    pressMetaK();
    setQuery('B-1EEEN00S99GXC');
    await settleSearch();
    expect(activeRow()?.textContent).toContain('B-1EEEN00S99GXC');
    expect(preloadRouteForPath).toHaveBeenLastCalledWith('/borrower-360/B-1EEEN00S99GXC');
    expect(borrowerSearch).toHaveBeenCalledTimes(1);
  });

  it('warms nothing for Ask Genie, workspace-command or selection-verb rows', async () => {
    pressMetaK();
    setQuery('zyrplax');
    await settleSearch();
    expect(activeRow()?.textContent).toContain('Ask Genie: zyrplax');
    preloadRouteForPath.mockClear();
    keyOnInput('ArrowDown');
    expect(preloadRouteForPath).not.toHaveBeenCalled();

    setQuery('toggle theme');
    await settleSearch();
    expect(activeRow()?.textContent).toContain('Toggle');
    expect(preloadRouteForPath).not.toHaveBeenCalled();

    pressMetaK();
    const unpublish = publishCommandSelection({
      selectedCount: 2, approveCount: 2, canApprove: true, rejectCount: 2, canReject: true, canAssign: false, run: () => undefined,
    });
    pressMetaK();
    expect(activeRow()?.textContent).toContain('Approve 2 selected');
    expect(preloadRouteForPath).not.toHaveBeenCalled();
    unpublish();
  });

  it('never warms /admin-config for a non-admin, and warms nothing under Save-Data', () => {
    // Control: an admin's Admin row is warmed like any route row.
    access.admin = true;
    pressMetaK();
    setQuery('admin');
    expect(preloaded()).toContain('/admin-config');
    pressMetaK();
    access.admin = false;
    preloadRouteForPath.mockClear();

    pressMetaK();
    setQuery('admin');
    for (let i = 0; i < 12; i += 1) keyOnInput('ArrowDown');
    expect(container.textContent).not.toContain('/admin-config');
    expect(preloaded()).not.toContain('/admin-config');
    pressMetaK();

    preloadRouteForPath.mockClear();
    saveData.on = true;
    pressMetaK();
    keyOnInput('ArrowDown');
    expect(preloadRouteForPath).not.toHaveBeenCalled();
  });
});

