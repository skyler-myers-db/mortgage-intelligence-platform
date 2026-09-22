/**
 * @vitest-environment happy-dom
 *
 * Genie survivability (audit 2026-09-21, wave 0b): rendered-DOM proofs for
 * the floating panel.
 *
 *   runtime-01 / genie-02  a turn survives the panel being closed; the
 *                          actor-boundary reset still aborts it (fail-closed)
 *   runtime-v2             one Escape closes one layer, and Genie only when
 *                          focus is inside it
 *   genie-v2               a second ask mid-turn never replaces the first
 *   genie-v1 / a11y-06     one persistent announcer, outside the panel
 *   motion-v2              a landed answer is scrolled to its START
 */

import { act, useRef } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieLiveProgress, GenieSubmitResult } from '../../lib/api';
import { GENIE_CONVERSATION_RESET_EVENT } from '../../lib/genieConversation';
import { clearGenieTurns } from '../../lib/genieConversationStore';
import { getGenieTurnStatus } from '../../lib/genieTurnStatus';
import { useFocusTrap } from '../../hooks/useFocusTrap';
import type { GenieAnswer, GenieStartResult } from '../../types';

const mocks = vi.hoisted(() => ({
  genieAction: vi.fn(),
  genieFeedback: vi.fn(),
  genieStart: vi.fn(),
  genieSubmit: vi.fn(),
  genieProgress: vi.fn(),
  genieComplete: vi.fn(),
  refreshWorkspace: vi.fn(),
  setDrawer: vi.fn(),
  setGenieOpen: vi.fn(),
}));

const appState = vi.hoisted(() => ({ genieOpen: true }));

vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api');
  return {
    ...actual,
    api: {
      genieAction: mocks.genieAction,
      genieFeedback: mocks.genieFeedback,
      genieStart: mocks.genieStart,
      genieSubmit: mocks.genieSubmit,
      genieProgress: mocks.genieProgress,
      genieComplete: mocks.genieComplete,
    },
  };
});

vi.mock('../AppContext', () => ({
  useApp: () => ({
    genieOpen: appState.genieOpen,
    setGenieOpen: mocks.setGenieOpen,
    lender: 'Test Lender',
    refreshWorkspace: mocks.refreshWorkspace,
    setDrawer: mocks.setDrawer,
  }),
}));

import { GenieChat } from './GenieChat';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const START: GenieStartResult = {
  conversation_id: null,
  trusted_assets: ['mip.gold.borrower_360'],
  sample_questions: [],
};

const LIVE_SUBMIT: GenieSubmitResult = {
  completed: false,
  conversation_id: 'conv-live',
  message_id: 'msg-live',
  progress_token: 'tok-live',
  question_hash: 'hash-live',
};

const TERMINAL: GenieLiveProgress = {
  status: 'COMPLETED',
  stage: 'complete',
  stage_label: 'Verifying the answer against its rows',
  terminal: true,
  failed: false,
  reasoning_trace: [],
  sql_preview: null,
  error_hint: null,
};

function answer(overrides: Partial<GenieAnswer> = {}): GenieAnswer {
  return {
    answer: 'There are 124,946 borrowers in the money.',
    source: 'genie',
    trusted_assets: ['mip.gold.borrower_360'],
    conversation_id: 'conv-live',
    message_id: 'msg-live',
    genie_status: 'COMPLETED',
    follow_up_questions: [],
    ...overrides,
  };
}

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve: (value: T) => void = () => undefined;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

function installLocalStorage() {
  const values = new Map<string, string>();
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
      clear: () => values.clear(),
    },
  });
}

function setInputValue(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  if (!setter) throw new Error('missing input value setter');
  setter.call(input, value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.dispatchEvent(new Event('change', { bubbles: true }));
}

/** A modal layer built on the REAL useFocusTrap — the hook the evidence
 *  drawer, the Genie proof drawer and the command palette all close through. */
function DrawerLayer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  useFocusTrap({ open, containerRef: panelRef, initialFocusRef: closeRef, onClose });
  if (!open) return null;
  return (
    <div ref={panelRef} role="dialog" aria-modal="true" aria-label="Evidence" tabIndex={-1}>
      <button ref={closeRef} type="button">Close evidence</button>
    </div>
  );
}

describe('floating Genie survivability', () => {
  let container: HTMLDivElement;
  let root: Root;
  let drawerOpen = false;
  // The topbar's Genie toggle (the launcher rendered at every width).
  let topbarToggle = true;
  const closeDrawer = vi.fn();
  const scrollIntoView = vi.fn();

  beforeEach(() => {
    Object.values(mocks).forEach((mock) => mock.mockReset());
    closeDrawer.mockReset();
    scrollIntoView.mockReset();
    appState.genieOpen = true;
    drawerOpen = false;
    topbarToggle = true;
    installLocalStorage();
    clearGenieTurns();
    mocks.genieStart.mockResolvedValue(START);
    mocks.genieFeedback.mockResolvedValue({ accepted: true });
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      writable: true,
      value: function scrollIntoViewSpy(this: Element, options?: ScrollIntoViewOptions) {
        scrollIntoView(this, options);
      },
    });
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      writable: true,
      value: (query: string) => ({
        matches: false,
        media: query,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
      }),
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    clearGenieTurns();
  });

  function render() {
    act(() => {
      root.render(
        <MemoryRouter>
          <button type="button" id="page-control">Page control</button>
          {topbarToggle && (
            <button type="button" aria-label="Toggle Genie chat" aria-pressed={appState.genieOpen}>
              Genie
            </button>
          )}
          <GenieChat />
          <DrawerLayer open={drawerOpen} onClose={closeDrawer} />
        </MemoryRouter>,
      );
    });
  }

  function setOpen(next: boolean) {
    appState.genieOpen = next;
    render();
  }

  async function waitUntil(condition: () => boolean, timeoutMs = 10_000) {
    const startedAt = Date.now();
    while (!condition()) {
      if (Date.now() - startedAt > timeoutMs) throw new Error('waitUntil timeout');
      await act(async () => new Promise((resolve) => setTimeout(resolve, 10)));
    }
  }

  async function flush() {
    await act(async () => new Promise((resolve) => setTimeout(resolve, 0)));
  }

  const dialog = () => {
    const el = container.querySelector<HTMLElement>('[role="dialog"][aria-label="Genie chat"]');
    if (!el) throw new Error('Genie panel not rendered');
    return el;
  };
  const input = () => {
    const el = container.querySelector<HTMLInputElement>('input[aria-label="Ask Genie"]');
    if (!el) throw new Error('Genie input not rendered');
    return el;
  };
  const askButton = () => {
    const el = container.querySelector<HTMLButtonElement>('button[aria-label="Ask"]');
    if (!el) throw new Error('Ask button not rendered');
    return el;
  };
  const fab = () => {
    const el = container.querySelector<HTMLButtonElement>('button.genie__fab');
    if (!el) throw new Error('FAB not rendered');
    return el;
  };
  const announcer = () => {
    const el = container.querySelector<HTMLElement>('[data-genie-announcer="panel"]');
    if (!el) throw new Error('panel announcer not rendered');
    return el;
  };
  const pressEscape = () => {
    act(() => {
      window.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }),
      );
    });
  };

  /** Start a LIVE turn that stays in flight until the returned handles resolve. */
  async function startLiveTurn(question: string, submit: GenieSubmitResult = LIVE_SUBMIT) {
    const progress = deferred<GenieLiveProgress>();
    const complete = deferred<GenieAnswer>();
    mocks.genieSubmit.mockResolvedValueOnce(submit);
    mocks.genieProgress.mockReturnValueOnce(progress.promise);
    mocks.genieComplete.mockReturnValueOnce(complete.promise);
    const callsBefore = mocks.genieSubmit.mock.calls.length;
    act(() => setInputValue(input(), question));
    await act(async () => {
      askButton().click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await waitUntil(() => mocks.genieProgress.mock.calls.length > 0);
    const signal = mocks.genieSubmit.mock.calls[callsBefore][2] as AbortSignal;
    return { progress, complete, signal };
  }

  it('keeps an in-flight turn alive while the panel is closed, badges the launcher, and lands the answer', async () => {
    render();
    const turn = await startLiveTurn('How many borrowers are in the money?');

    setOpen(false);

    // Closed means hidden, not unmounted — and nothing was aborted.
    expect(dialog().getAttribute('aria-hidden')).toBe('true');
    expect(dialog().classList.contains('is-open')).toBe(false);
    expect(turn.signal.aborted).toBe(false);
    expect(container.textContent).toContain('How many borrowers are in the money?');
    expect(fab().classList.contains('is-genie-running')).toBe(true);
    expect(fab().getAttribute('aria-describedby')).toBe('genie-launcher-status');
    expect(document.getElementById('genie-launcher-status')?.textContent).toContain('still working');
    expect(getGenieTurnStatus()).toBe('running');

    // An Escape anywhere while Genie is closed must not reach the turn either.
    pressEscape();
    expect(turn.signal.aborted).toBe(false);

    await act(async () => {
      turn.progress.resolve(TERMINAL);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await waitUntil(() => mocks.genieComplete.mock.calls.length === 1);
    expect((mocks.genieComplete.mock.calls[0][4] as AbortSignal).aborted).toBe(false);
    await act(async () => {
      turn.complete.resolve(answer());
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    await waitUntil(() => container.textContent?.includes('124,946 borrowers') ?? false);
    expect(fab().classList.contains('is-genie-running')).toBe(false);
    expect(fab().classList.contains('is-genie-ready')).toBe(true);
    expect(document.getElementById('genie-launcher-status')?.textContent).toContain('answer ready');
    expect(getGenieTurnStatus()).toBe('ready');
    expect(announcer().textContent).toBe('Answer ready');
    // The closed panel did not scroll anything yet.
    expect(scrollIntoView).not.toHaveBeenCalled();

    setOpen(true);
    await flush();

    expect(fab().classList.contains('is-genie-ready')).toBe(false);
    expect(getGenieTurnStatus()).toBe('idle');
    // Opening lands on the START of the unseen answer, without animation.
    const answers = container.querySelectorAll('.genie__msg--ai');
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    expect(scrollIntoView.mock.calls[0][0]).toBe(answers[answers.length - 1]);
    expect(scrollIntoView.mock.calls[0][1]).toEqual({ block: 'start', behavior: 'auto' });
  });

  it('still aborts the turn and clears everything on an actor-boundary reset (fail-closed)', async () => {
    render();
    const turn = await startLiveTurn('How many borrowers are in the money?');
    setOpen(false);
    expect(turn.signal.aborted).toBe(false);

    act(() => {
      window.dispatchEvent(new Event(GENIE_CONVERSATION_RESET_EVENT));
    });

    expect(turn.signal.aborted).toBe(true);
    expect(container.textContent).not.toContain('How many borrowers are in the money?');
    expect(getGenieTurnStatus()).toBe('idle');
    expect(fab().classList.contains('is-genie-running')).toBe(false);

    // The previous actor's turn resolving late must not resurrect anything.
    await act(async () => {
      turn.progress.resolve(TERMINAL);
      turn.complete.resolve(answer({ conversation_id: 'conv-previous-actor' }));
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(container.querySelectorAll('.genie__msg')).toHaveLength(0);
    expect(window.localStorage.getItem('mip.genie.conversationId')).toBeNull();
    expect(fab().classList.contains('is-genie-ready')).toBe(false);
    expect(announcer().textContent).toBe('');

    // And the composer is usable again for the new actor.
    setOpen(true);
    expect(askButton().disabled).toBe(false);
  });

  it('holds a second ask while a turn is in flight instead of replacing it (genie-v2)', async () => {
    render();
    // A settled answer with follow-up chips, resolved inline.
    mocks.genieSubmit.mockResolvedValueOnce({
      completed: true,
      response: answer({
        answer: 'Opportunity volume is steady.',
        follow_up_questions: ['Break this down by state'],
      }),
    });
    act(() => setInputValue(input(), 'Show current opportunity volume'));
    await act(async () => {
      askButton().click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await waitUntil(() => container.textContent?.includes('Opportunity volume is steady.') ?? false);
    const chip = () => {
      const el = container.querySelector<HTMLButtonElement>('.genie-answer__followups button');
      if (!el) throw new Error('follow-up chip not rendered');
      return el;
    };
    expect(chip().disabled).toBe(false);

    const turn = await startLiveTurn('Which states lead on refinance opportunity?');
    expect(mocks.genieSubmit).toHaveBeenCalledTimes(2);

    // Sending is held, with a reason a screen reader can reach from the input.
    expect(askButton().disabled).toBe(true);
    expect(chip().disabled).toBe(true);
    expect(chip().title).toContain('Genie is still answering');
    const hint = document.getElementById('genie-composer-busy');
    expect(hint?.textContent).toContain('Genie is still answering');
    expect(input().getAttribute('aria-describedby')).toBe('genie-composer-busy');

    // Drafting is not: the input stays editable.
    expect(input().disabled).toBe(false);
    act(() => setInputValue(input(), 'And by county?'));
    expect(input().value).toBe('And by county?');

    // Enter (a raw form submit, which bypasses the disabled button) and a
    // stale chip click must not start a second turn or touch the first.
    const form = container.querySelector<HTMLFormElement>('form.genie__input');
    await act(async () => {
      form?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
      chip().click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(mocks.genieSubmit).toHaveBeenCalledTimes(2);
    expect(turn.signal.aborted).toBe(false);
    expect(input().value).toBe('And by county?');
    expect(container.querySelectorAll('.genie__msg--user')).toHaveLength(2);

    await act(async () => {
      turn.progress.resolve(TERMINAL);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await waitUntil(() => mocks.genieComplete.mock.calls.length === 1);
    await act(async () => {
      turn.complete.resolve(answer());
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await waitUntil(() => container.textContent?.includes('124,946 borrowers') ?? false);

    // The running turn got its answer; the draft survived; sending is back.
    expect(askButton().disabled).toBe(false);
    expect(chip().disabled).toBe(false);
    expect(document.getElementById('genie-composer-busy')).toBeNull();
    expect(input().value).toBe('And by county?');
  });

  it('closes only the top layer per Escape, and Genie only when focus is inside it (runtime-v2)', async () => {
    drawerOpen = true;
    render();
    await flush();

    // Drawer and Genie both open: the first Escape closes the drawer ONLY.
    pressEscape();
    expect(closeDrawer).toHaveBeenCalledTimes(1);
    expect(mocks.setGenieOpen).not.toHaveBeenCalled();

    drawerOpen = false;
    render();
    await flush();

    // Genie is now the top layer, but the user is working on the page.
    act(() => document.getElementById('page-control')?.focus());
    pressEscape();
    expect(mocks.setGenieOpen).not.toHaveBeenCalled();

    // Focus inside the panel: Escape closes Genie.
    act(() => input().focus());
    pressEscape();
    expect(mocks.setGenieOpen).toHaveBeenCalledTimes(1);
    expect(mocks.setGenieOpen).toHaveBeenCalledWith(false);
    expect(closeDrawer).toHaveBeenCalledTimes(1);
  });

  it('closes a trap layer opened ABOVE Genie, never Genie, even with focus inside the panel (runtime-v2 stack)', async () => {
    // The case above passes on Genie's own focus-inside decline alone: focus
    // sat in the drawer. This one pins the STACK. Genie opens first, so its
    // layer is below; the trap opens above it; then the user works in Genie —
    // the exact state in which Genie's handler would accept the key. Only the
    // top-only walk keeps Genie open and closes the drawer. An uncoordinated
    // trap (its own bubble-phase window listener) is pre-empted here: Genie
    // consumes the capture-phase keypress and the WRONG layer closes.
    render();
    await flush();
    drawerOpen = true;
    render();
    await flush();

    act(() => input().focus());
    expect(document.activeElement).toBe(input());

    pressEscape();
    expect(closeDrawer).toHaveBeenCalledTimes(1);
    expect(mocks.setGenieOpen).not.toHaveBeenCalled();
    expect(dialog().classList.contains('is-open')).toBe(true);
  });

  /** Open the panel from a transient control (a command-palette item) that is
   *  gone by the time the panel closes. Returns the closed-panel focus target. */
  async function closeAfterOpenerLeft(): Promise<Element | null> {
    const opener = document.createElement('button');
    opener.type = 'button';
    document.body.appendChild(opener);
    opener.focus();
    render();
    await flush();
    opener.remove();

    setOpen(false);
    await flush();
    return document.activeElement;
  }

  it('returns focus to the topbar toggle when the opener has left the document', async () => {
    // The panel's own FAB is display:none on desktop, so it cannot take focus
    // there; the topbar toggle exists at every width.
    const focused = await closeAfterOpenerLeft();
    expect(focused).toBe(container.querySelector('button[aria-label="Toggle Genie chat"]'));
    expect(focused).not.toBe(fab());
  });

  it('falls back to the FAB only when there is no topbar toggle', async () => {
    topbarToggle = false;
    expect(await closeAfterOpenerLeft()).toBe(fab());
  });

  it('announces through one persistent region outside the panel, never from inside it (a11y-06)', async () => {
    render();
    const turn = await startLiveTurn('How many borrowers are in the money?');

    // The announcer is not inside the dialog (which is aria-hidden when
    // closed), and nothing inside the dialog is a live region mid-turn.
    expect(dialog().contains(announcer())).toBe(false);
    expect(dialog().querySelectorAll('[role="status"], [aria-live]')).toHaveLength(0);
    const ticker = dialog().querySelector('.genie-progress__elapsed');
    expect(ticker).not.toBeNull();
    expect(ticker?.closest('[role="status"], [aria-live]')).toBeNull();
    expect(announcer().textContent).toBe('Waiting for Genie response');

    await act(async () => {
      turn.progress.resolve(TERMINAL);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await waitUntil(() => mocks.genieComplete.mock.calls.length === 1);
    // Genie's own turn is done but the answer is NOT renderable yet.
    expect(announcer().textContent).toBe('Verifying the answer against its rows');
    expect(container.textContent).not.toContain('Answer ready');

    await act(async () => {
      turn.complete.resolve(answer());
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await waitUntil(() => announcer().textContent === 'Answer ready');
    // The settled answer mounts no second, pre-populated status region.
    expect(dialog().querySelectorAll('[role="status"]')).toHaveLength(0);
  });

  it('scrolls a landed answer to its START instead of jumping to the end (motion-v2)', async () => {
    render();
    const body = container.querySelector<HTMLElement>('.genie__body');
    if (!body) throw new Error('transcript body not rendered');
    Object.defineProperty(body, 'scrollHeight', { configurable: true, value: 1_000 });

    const turn = await startLiveTurn('How many borrowers are in the money?');
    // Sending a question still sticks to the bottom.
    expect(body.scrollTop).toBe(1_000);
    body.scrollTop = 120;

    await act(async () => {
      turn.progress.resolve(TERMINAL);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await waitUntil(() => mocks.genieComplete.mock.calls.length === 1);
    body.scrollTop = 120;
    await act(async () => {
      turn.complete.resolve(answer());
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await waitUntil(() => container.textContent?.includes('124,946 borrowers') ?? false);

    const answers = container.querySelectorAll('.genie__msg--ai');
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    expect(scrollIntoView.mock.calls[0][0]).toBe(answers[answers.length - 1]);
    expect(scrollIntoView.mock.calls[0][1]).toEqual({ block: 'start', behavior: 'smooth' });
    // The end-jump is gone: nothing forced the transcript to scrollHeight.
    expect(body.scrollTop).toBe(120);
  });

  it('does not animate the answer scroll under prefers-reduced-motion', async () => {
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      writable: true,
      value: (query: string) => ({
        matches: query.includes('prefers-reduced-motion'),
        media: query,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
      }),
    });
    render();
    const turn = await startLiveTurn('How many borrowers are in the money?');
    await act(async () => {
      turn.progress.resolve(TERMINAL);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await waitUntil(() => mocks.genieComplete.mock.calls.length === 1);
    await act(async () => {
      turn.complete.resolve(answer());
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await waitUntil(() => scrollIntoView.mock.calls.length === 1);
    expect(scrollIntoView.mock.calls[0][1]).toEqual({ block: 'start', behavior: 'auto' });
  });

  it('shows a turn the /ask-genie route settled while the panel sat closed', async () => {
    const { appendGenieTurn } = await import('../../lib/genieConversationStore');
    render();
    setOpen(false);

    act(() => {
      appendGenieTurn('Asked on the route', answer({ answer: 'Settled on the route.' }));
    });
    setOpen(true);

    expect(container.textContent).toContain('Asked on the route');
    expect(container.textContent).toContain('Settled on the route.');
    // Seen on the route already: no unseen-answer badge for it.
    expect(getGenieTurnStatus()).toBe('idle');
  });
});
