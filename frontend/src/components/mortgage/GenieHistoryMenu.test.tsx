/**
 * @vitest-environment happy-dom
 *
 * Genie History menu on the query layer (audit 2026-09-21 `runtime-06`,
 * Genie slice), proven on the rendered menu: the rows are read only while the
 * menu is open (never on mount, never prefetched), once per open, a reopen
 * shows the cached rows while its read runs, closing aborts the read, and an
 * error shows "History unavailable" after exactly ONE request even when the
 * client's default would retry. Offline is a network failure like any other
 * (not a paused "Loading history…"), and only an explicit reopen re-reads.
 */

import { onlineManager, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieSessionSummary } from '../../types';

const mocks = vi.hoisted(() => ({
  genieSessions: vi.fn(),
  genieSession: vi.fn(),
  onToggle: vi.fn(),
  onLoad: vi.fn(),
}));

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: {
    genieSessions: mocks.genieSessions,
    genieSession: mocks.genieSession,
  },
}));

import { hasOpenOverlay } from '../../lib/keymap';
import { GenieHistoryMenu } from './GenieHistoryMenu';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const PAST: GenieSessionSummary = {
  conversation_id: 'conv-past',
  title: 'Equity sweep',
  last_activity_at: '2026-09-20T12:00:00Z',
  turn_count: 3,
};
const NO_ACTIVITY: GenieSessionSummary = {
  conversation_id: 'conv-quiet',
  title: 'Quiet thread',
  last_activity_at: null,
  turn_count: 1,
};

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve: (value: T) => void = () => undefined;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

describe('GenieHistoryMenu', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  beforeEach(() => {
    Object.values(mocks).forEach((mock) => mock.mockReset());
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    // A client whose DEFAULT retries at once: the menu's own `retry: false`
    // is what keeps an error to one request.
    client = new QueryClient({ defaultOptions: { queries: { retry: 2, retryDelay: 0 } } });
  });

  afterEach(() => {
    act(() => root.unmount());
    client.clear();
    container.remove();
    onlineManager.setOnline(true);
  });

  function render(open: boolean) {
    act(() => {
      root.render(
        <QueryClientProvider client={client}>
          <GenieHistoryMenu open={open} onToggle={mocks.onToggle} onLoad={mocks.onLoad} />
        </QueryClientProvider>,
      );
    });
  }

  async function settle(ticks = 5) {
    for (let i = 0; i < ticks; i += 1) {
      await act(async () => new Promise((resolve) => setTimeout(resolve, 0)));
    }
  }

  // Below vitest's own 5 s testTimeout, so a condition that never holds fails
  // here, with this message, instead of as an anonymous test timeout
  // (wave-3 api-contract review).
  async function waitUntil(condition: () => boolean, timeoutMs = 2_000) {
    const startedAt = Date.now();
    while (!condition()) {
      if (Date.now() - startedAt > timeoutMs) throw new Error('waitUntil timeout');
      await act(async () => new Promise((resolve) => setTimeout(resolve, 5)));
    }
  }

  const menu = () => container.querySelector('[role="menu"]');
  const rowTitles = () =>
    Array.from(container.querySelectorAll('.genie-history__title')).map((el) => el.textContent);

  it('never reads while closed: a closed mount makes no request', async () => {
    mocks.genieSessions.mockResolvedValue([PAST]);
    render(false);
    await settle();

    expect(menu()).toBeNull();
    expect(mocks.genieSessions).not.toHaveBeenCalled();
  });

  it('reads once per open, and a reopen shows the cached rows while its read runs', async () => {
    mocks.genieSessions.mockResolvedValueOnce([PAST]);
    render(true);
    await waitUntil(() => rowTitles().length === 1);
    expect(mocks.genieSessions).toHaveBeenCalledTimes(1);
    expect(rowTitles()).toEqual(['Equity sweep']);

    render(false);
    await settle();
    expect(menu()).toBeNull();
    expect(mocks.genieSessions).toHaveBeenCalledTimes(1);

    const reread = deferred<GenieSessionSummary[]>();
    mocks.genieSessions.mockReturnValueOnce(reread.promise);
    render(true);
    // The cached rows paint at once, not a loading row, while the reread runs.
    expect(rowTitles()).toEqual(['Equity sweep']);
    expect(container.textContent).not.toContain('Loading history');
    expect(mocks.genieSessions).toHaveBeenCalledTimes(2);

    await act(async () => reread.resolve([NO_ACTIVITY, PAST]));
    await waitUntil(() => rowTitles().length === 2);
    expect(rowTitles()).toEqual(['Quiet thread', 'Equity sweep']);
    expect(mocks.genieSessions).toHaveBeenCalledTimes(2);
  });

  it('shows "History unavailable" after exactly one request when the read fails', async () => {
    mocks.genieSessions.mockRejectedValue(new Error('fixture: history is down'));
    render(true);
    await waitUntil(() => container.textContent?.includes('History unavailable') ?? false);
    await settle(10);

    expect(mocks.genieSessions).toHaveBeenCalledTimes(1);
    expect(container.querySelector('.genie-history__state--error')?.textContent).toBe('History unavailable');
    expect(container.querySelectorAll('.genie-history__item')).toHaveLength(0);
  });

  it('offline, fails into "History unavailable" after one attempt; only a reopen reads again', async () => {
    act(() => onlineManager.setOnline(false));
    mocks.genieSessions.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    render(true);
    // Shorter than the test timeout, so a paused read fails here, by name.
    await waitUntil(() => container.textContent?.includes('History unavailable') ?? false, 2_000);
    await settle(10);

    // The client's default networkMode ('online') would pause here on
    // "Loading history…" with no request at all.
    expect(mocks.genieSessions).toHaveBeenCalledTimes(1);
    expect(container.textContent).not.toContain('Loading history');

    // Reconnecting is not an open: no background read while the menu is open.
    mocks.genieSessions.mockResolvedValue([PAST]);
    act(() => onlineManager.setOnline(true));
    await settle(10);
    expect(mocks.genieSessions).toHaveBeenCalledTimes(1);
    expect(container.textContent).toContain('History unavailable');

    render(false);
    render(true);
    await waitUntil(() => rowTitles().length === 1);
    expect(rowTitles()).toEqual(['Equity sweep']);
    expect(mocks.genieSessions).toHaveBeenCalledTimes(2);
  });

  it('hides rows cached by an earlier open once the reread fails', async () => {
    mocks.genieSessions.mockResolvedValueOnce([PAST]);
    render(true);
    await waitUntil(() => rowTitles().length === 1);
    render(false);

    mocks.genieSessions.mockRejectedValueOnce(new Error('fixture: history is down'));
    render(true);
    await waitUntil(() => container.textContent?.includes('History unavailable') ?? false);

    expect(rowTitles()).toEqual([]);
    expect(mocks.genieSessions).toHaveBeenCalledTimes(2);
  });

  it('aborts the read when the menu closes', async () => {
    let signal: AbortSignal | undefined;
    mocks.genieSessions.mockImplementation((next: AbortSignal) => {
      signal = next;
      return new Promise<GenieSessionSummary[]>(() => undefined);
    });
    render(true);
    await waitUntil(() => signal !== undefined);
    expect(signal?.aborted).toBe(false);

    render(false);
    await settle();
    expect(signal?.aborted).toBe(true);
  });

  it('renders a session with no recorded activity as "1 turn", with no timestamp and no null', async () => {
    mocks.genieSessions.mockResolvedValue([NO_ACTIVITY]);
    render(true);
    await waitUntil(() => rowTitles().length === 1);

    const meta = container.querySelector('.genie-history__meta');
    expect(meta?.textContent).toBe('1 turn');
    expect(container.textContent).not.toContain('null');
    expect(container.textContent).not.toContain('Invalid Date');
  });

  it('a session that cannot be read hides the rows until the menu is reopened', async () => {
    mocks.genieSessions.mockResolvedValue([PAST]);
    mocks.genieSession.mockRejectedValue(new Error('fixture: detail is down'));
    render(true);
    await waitUntil(() => rowTitles().length === 1);

    await act(async () => container.querySelector<HTMLButtonElement>('.genie-history__item')?.click());
    await waitUntil(() => container.textContent?.includes('History unavailable') ?? false);
    expect(rowTitles()).toEqual([]);
    expect(mocks.onLoad).not.toHaveBeenCalled();

    render(false);
    render(true);
    await waitUntil(() => rowTitles().length === 1);
    expect(container.textContent).not.toContain('History unavailable');
  });

  it('hands a readable session to onLoad', async () => {
    mocks.genieSessions.mockResolvedValue([PAST]);
    mocks.genieSession.mockResolvedValue({
      conversation_id: 'conv-past',
      turns: [{ question: 'Which segments lead?', response: { answer: 'Restored.' } }],
    });
    render(true);
    await waitUntil(() => rowTitles().length === 1);

    await act(async () => container.querySelector<HTMLButtonElement>('.genie-history__item')?.click());
    await waitUntil(() => mocks.onLoad.mock.calls.length === 1);
    expect(mocks.onLoad).toHaveBeenCalledWith('conv-past', [
      { question: 'Which segments lead?', response: { answer: 'Restored.' } },
    ]);
  });

  describe('the exit (motion-01 item 2e)', () => {
    let restoreStyle: (() => void) | null = null;

    /** The lazy sheet's exit, as the browser computes it (DOM tests have no stylesheets). */
    function withExitTransition() {
      const original = window.getComputedStyle;
      window.getComputedStyle = ((element: Element, pseudo?: string | null) => {
        const style = original.call(window, element, pseudo);
        if (!(element as HTMLElement).classList?.contains('genie-history__menu--exiting')) return style;
        // useExitRetained reads these two only.
        return { transitionDuration: '0.12s', transitionDelay: '0s' } as unknown as CSSStyleDeclaration;
      }) as typeof window.getComputedStyle;
      restoreStyle = () => {
        window.getComputedStyle = original;
      };
    }

    afterEach(() => {
      restoreStyle?.();
      restoreStyle = null;
    });

    it('closes at once where nothing transitions (a DOM test, or reduced motion)', async () => {
      mocks.genieSessions.mockResolvedValue([PAST]);
      render(true);
      await waitUntil(() => rowTitles().length === 1);
      render(false);
      expect(menu()).toBeNull();
    });

    it('a closing menu stays through its exit: inert, aria-hidden, no open overlay, focus back on the toggle', async () => {
      withExitTransition();
      mocks.genieSessions.mockResolvedValue([PAST]);
      render(true);
      await waitUntil(() => rowTitles().length === 1);
      act(() => container.querySelector<HTMLButtonElement>('.genie-history__item')?.focus());

      render(false);
      const exiting = menu();
      expect(exiting?.classList.contains('genie-history__menu--exiting')).toBe(true);
      expect(exiting?.getAttribute('aria-hidden')).toBe('true');
      expect(exiting?.hasAttribute('inert')).toBe(true);
      expect(hasOpenOverlay(document), 'an exiting menu is no open overlay for the keymap').toBe(false);
      expect(document.activeElement).toBe(container.querySelector('button[aria-label="Genie conversation history"]'));

      act(() => exiting?.dispatchEvent(new Event('transitionend')));
      expect(menu()).toBeNull();
    });

    it('a reopen during the exit remounts the rows and reads again: one read per open', async () => {
      withExitTransition();
      mocks.genieSessions.mockResolvedValue([PAST]);
      render(true);
      await waitUntil(() => rowTitles().length === 1);
      expect(mocks.genieSessions).toHaveBeenCalledTimes(1);

      render(false);
      expect(menu()?.classList.contains('genie-history__menu--exiting')).toBe(true);
      render(true);
      await settle();
      expect(menu()?.classList.contains('genie-history__menu--exiting')).toBe(false);
      expect(mocks.genieSessions, 'the reopen read again').toHaveBeenCalledTimes(2);
      expect(menu()?.hasAttribute('aria-hidden')).toBe(false);
    });

    /**
     * A browser drops focus from a row that turns disabled (the pick's
     * loading state) before the menu closes; happy-dom does not, so the
     * tests below blur the row themselves. The browser-layer proof is
     * genie-context-deeplink.cross-engine.fixture.spec.ts (h).
     */
    const toggleButton = () => container.querySelector<HTMLButtonElement>('button[aria-label="Genie conversation history"]');

    it('a keyboard pick whose row lost focus to <body> hands focus back to the toggle as the menu closes', async () => {
      withExitTransition();
      mocks.genieSessions.mockResolvedValue([PAST]);
      const session = deferred<{ conversation_id: string; turns: [] }>();
      mocks.genieSession.mockReturnValue(session.promise);
      render(true);
      await waitUntil(() => rowTitles().length === 1);
      const row = container.querySelector<HTMLButtonElement>('.genie-history__item');
      act(() => row?.focus());
      act(() => row?.click());
      // The browser's focus fixup for a disabled element (happy-dom will not
      // blur a disabled button, so focus passes through a node that leaves).
      act(() => {
        const sink = document.createElement('input');
        document.body.appendChild(sink);
        sink.focus();
        sink.remove();
      });
      expect(document.activeElement).toBe(document.body);

      await act(async () => session.resolve({ conversation_id: 'conv-past', turns: [] }));
      await waitUntil(() => mocks.onLoad.mock.calls.length === 1);
      render(false);
      expect(document.activeElement).toBe(toggleButton());
    });

    it('a pick whose focus the user moved elsewhere leaves it there', async () => {
      mocks.genieSessions.mockResolvedValue([PAST]);
      const session = deferred<{ conversation_id: string; turns: [] }>();
      mocks.genieSession.mockReturnValue(session.promise);
      render(true);
      await waitUntil(() => rowTitles().length === 1);
      const row = container.querySelector<HTMLButtonElement>('.genie-history__item');
      act(() => row?.focus());
      act(() => row?.click());
      const elsewhere = document.createElement('input');
      container.appendChild(elsewhere);
      act(() => elsewhere.focus());

      await act(async () => session.resolve({ conversation_id: 'conv-past', turns: [] }));
      await waitUntil(() => mocks.onLoad.mock.calls.length === 1);
      render(false);
      expect(document.activeElement).toBe(elsewhere);
    });

    it('a failed pick keeps the menu open and hands the row\'s focus to the toggle', async () => {
      mocks.genieSessions.mockResolvedValue([PAST]);
      mocks.genieSession.mockRejectedValue(new Error('fixture: detail is down'));
      render(true);
      await waitUntil(() => rowTitles().length === 1);
      const row = container.querySelector<HTMLButtonElement>('.genie-history__item');
      act(() => row?.focus());
      await act(async () => row?.click());
      await waitUntil(() => container.textContent?.includes('History unavailable') ?? false);
      expect(menu()).not.toBeNull();
      expect(document.activeElement).toBe(toggleButton());
    });

    it('a closed menu never reads, exit or not', async () => {
      withExitTransition();
      mocks.genieSessions.mockResolvedValue([PAST]);
      render(false);
      render(false);
      await settle();
      expect(mocks.genieSessions).not.toHaveBeenCalled();
      expect(menu()).toBeNull();
    });
  });
});
