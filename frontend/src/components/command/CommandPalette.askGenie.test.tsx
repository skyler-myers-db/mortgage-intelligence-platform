/**
 * @vitest-environment happy-dom
 *
 * "Ask Genie: <typed text>" palette row (audit 2026-09-21 `shell-07` /
 * `genie-04`): the typed text reaches the floating panel's composer through
 * the same guarded ask path every question takes -- a prefill, never a
 * submit, and never a new endpoint.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { consumeGeniePrefill, subscribeGenieOpenRequests } from '../../lib/genieOpen';

const navigate = vi.fn();
vi.mock('react-router', () => ({ useNavigate: () => navigate }));

const setGenieOpen = vi.fn();
vi.mock('../AppContext', () => ({
  useApp: () => ({
    theme: 'dark',
    setTheme: vi.fn(),
    consoleOpen: false,
    setConsoleOpen: vi.fn(),
    setGenieOpen,
    canAccessAdmin: false,
  }),
}));

const borrowerSearch = vi.fn();
vi.mock('../../lib/api', () => ({
  api: { borrowerSearch: (...a: unknown[]) => borrowerSearch(...a) },
}));

import { CommandPalette, loadCommandPaletteDialog } from './CommandPalette';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;


// The palette dialog is a lazy chunk behind the shell host (audit bundle-04):
// load it once up front, as the idle preload leaves it in the app, so the
// host mounts it at once and ⌘K opens it synchronously.
beforeAll(async () => {
  await loadCommandPaletteDialog();
}, 60_000);

describe('CommandPalette Ask Genie row', () => {
  let container: HTMLDivElement;
  let root: Root;
  const openRequests: number[] = [];
  let unsubscribe = () => undefined as void;

  beforeEach(() => {
    vi.clearAllMocks();
    openRequests.length = 0;
    consumeGeniePrefill();
    unsubscribe = subscribeGenieOpenRequests(() => openRequests.push(1));
    borrowerSearch.mockResolvedValue([]);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => root.render(<CommandPalette />));
  });
  afterEach(() => {
    unsubscribe();
    act(() => root.unmount());
    container.remove();
    consumeGeniePrefill();
  });

  function pressMetaK() {
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
  /** Let the debounced borrower search resolve (real timers). */
  async function settleSearch(timeoutMs = 5_000) {
    const startedAt = Date.now();
    await act(async () => new Promise((resolve) => setTimeout(resolve, 0)));
    while (container.querySelector('.cmdk__status')?.textContent?.includes('Searching borrowers')) {
      if (Date.now() - startedAt > timeoutMs) throw new Error('borrower search never settled');
      await act(async () => new Promise((resolve) => setTimeout(resolve, 20)));
    }
    expect(borrowerSearch).toHaveBeenCalled();
  }
  const genieRow = () =>
    Array.from(container.querySelectorAll<HTMLButtonElement>('[role="option"]')).find((el) =>
      el.textContent?.startsWith('Ask Genie:'),
    ) ?? null;

  it('is absent until the operator has typed something', () => {
    pressMetaK();
    expect(genieRow()).toBeNull();
    setQuery('r');
    expect(genieRow()).toBeNull();
  });

  it('offers "Ask Genie: <text>" in its own group and opens the panel prefilled, never submitting', () => {
    pressMetaK();
    // A vocabulary hit lists the pages first; the Genie row follows them.
    setQuery('refi');
    expect(container.querySelector('#cmdk-option-0')?.textContent).toContain('Segment Intelligence');
    expect(genieRow()?.textContent).toContain('Ask Genie: refi');

    setQuery('refi candidates in Texas');
    const row = genieRow();
    expect(row).not.toBeNull();
    expect(row?.textContent).toContain('Ask Genie: refi candidates in Texas');
    expect(container.querySelector('[role="group"][aria-label="Ask Genie"]')).not.toBeNull();

    act(() => row!.click());
    expect(openRequests).toEqual([1]);
    expect(consumeGeniePrefill()).toBe('refi candidates in Texas');
    expect(container.querySelector('dialog.cmdk[open]')).toBeNull();
    expect(navigate).not.toHaveBeenCalled();
    expect(borrowerSearch.mock.calls.length, 'the borrower search is the only network call').toBeLessThanOrEqual(1);
  });

  it('a word no page matches still offers the Genie row, so the palette never dead-ends', async () => {
    pressMetaK();
    setQuery('zyrplax');
    // The empty state still says no page matched; the Genie row is the way out.
    expect(container.querySelector('.cmdk__empty')?.textContent).toContain('No pages, actions, or borrowers');
    expect(genieRow()?.textContent).toContain('Ask Genie: zyrplax');
    // Enter reaches the fallback once the borrower search has settled (it is
    // held while a borrower row could still land above it).
    await settleSearch();
    act(() => {
      input().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
    });
    expect(consumeGeniePrefill()).toBe('zyrplax');
    expect(openRequests).toEqual([1]);
  });

  it('a user who arrowed onto the Genie row stays on it when borrower rows land above it', async () => {
    let resolveSearch: (rows: unknown[]) => void = () => undefined;
    borrowerSearch.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveSearch = resolve;
        }),
    );
    pressMetaK();
    setQuery('refi');
    // Pages first, the Genie row last; ArrowUp wraps onto it while the
    // borrower search is still in flight.
    act(() => {
      input().dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true, cancelable: true }));
    });
    const selected = () => container.querySelector('[role="option"][aria-selected="true"]');
    expect(selected()?.textContent).toContain('Ask Genie: refi');
    // Wait for the debounced search to be issued (it stays pending).
    const startedAt = Date.now();
    while (borrowerSearch.mock.calls.length === 0) {
      if (Date.now() - startedAt > 5_000) throw new Error('borrower search never issued');
      await act(async () => new Promise((resolve) => setTimeout(resolve, 20)));
    }
    expect(selected()?.textContent).toContain('Ask Genie: refi');

    await act(async () => {
      resolveSearch([{ borrower_id: 'B-1EEEN00S99GXC', city: 'Chicago', state: 'IL', zip: '60611' }]);
    });
    expect(container.querySelector('[role="group"][aria-label="Borrowers"]')).not.toBeNull();
    // The selection followed the row, not the index the borrower now holds.
    expect(selected()?.textContent).toContain('Ask Genie: refi');
    expect(input().getAttribute('aria-activedescendant')).toBe(selected()?.id);
    act(() => {
      input().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
    });
    expect(navigate).not.toHaveBeenCalled();
    expect(consumeGeniePrefill()).toBe('refi');
  });

  it('the Genie row comes after the borrower rows, as the fallback', async () => {
    borrowerSearch.mockResolvedValue([
      { borrower_id: 'B-1EEEN00S99GXC', city: 'Chicago', state: 'IL', zip: '60611' },
    ]);
    pressMetaK();
    setQuery('60611');
    await settleSearch();
    const groups = Array.from(container.querySelectorAll('[role="group"]')).map((g) => g.getAttribute('aria-label'));
    expect(groups).toEqual(['Borrowers', 'Ask Genie']);
    const options = Array.from(container.querySelectorAll('[role="option"]'));
    expect(options[options.length - 1].textContent).toContain('Ask Genie: 60611');
  });
});
