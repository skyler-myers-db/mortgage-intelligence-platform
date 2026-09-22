/**
 * @vitest-environment happy-dom
 *
 * Conversational controls of the floating Genie panel (audit 2026-09-21
 * `genie-03`, client-only slice), proven on the rendered panel:
 *
 *   Stop        aborts the client turn (no further polls), the generation
 *               counter makes a late reply land nowhere, the question is back
 *               in the composer (never over a draft), a "Stopped" note marks
 *               the bubble, New thread / History unlock
 *   Retry       a failed (degraded) bubble re-asks as a new turn
 *   Edit        a sent question reloads into the composer without sending
 *   Regenerate  re-asks the same question as a NEW turn (second submit);
 *               never offered on a refusal, which would only repeat itself
 *   ArrowUp     an empty composer recalls the last question
 *
 * And the page-context slice (`genie-04`, phase 1): `openGenie({ prompt })`
 * PREFILLS the composer and never submits; the empty state shows the
 * starters curated for the current route.
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieLiveProgress, GenieSubmitResult } from '../../lib/api';
import { clearGenieTurns, getGenieTurns } from '../../lib/genieConversationStore';
import { genieStartersForRoute } from '../../lib/genieContext';
import { consumeGeniePrefill, openGenie } from '../../lib/genieOpen';
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
  sample_questions: ['Server starter one', 'Server starter two'],
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
  reject: (reason: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve: (value: T) => void = () => undefined;
  let reject: (reason: unknown) => void = () => undefined;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
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

describe('floating Genie conversational controls', () => {
  let container: HTMLDivElement;
  let root: Root;
  let route = '/';

  beforeEach(() => {
    Object.values(mocks).forEach((mock) => mock.mockReset());
    appState.genieOpen = true;
    route = '/';
    installLocalStorage();
    clearGenieTurns();
    mocks.genieStart.mockResolvedValue(START);
    mocks.genieFeedback.mockResolvedValue({ accepted: true });
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      writable: true,
      value: () => undefined,
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
    consumeGeniePrefill();
  });

  function render() {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={[route]}>
          <button type="button" aria-label="Toggle Genie chat" aria-pressed={appState.genieOpen}>
            Genie
          </button>
          <GenieChat />
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

  const query = <T extends Element>(selector: string, label: string): T => {
    const el = container.querySelector<T>(selector);
    if (!el) throw new Error(`${label} not rendered`);
    return el;
  };
  const input = () => query<HTMLInputElement>('input[aria-label="Ask Genie"]', 'Genie input');
  const askButton = () => query<HTMLButtonElement>('button[aria-label="Ask"]', 'Ask button');
  const stopButton = () => query<HTMLButtonElement>('button[aria-label="Stop this Genie turn"]', 'Stop button');
  const newThread = () => query<HTMLButtonElement>('button[aria-label="Start a new Genie thread"]', 'New thread');
  const history = () => query<HTMLButtonElement>('button[aria-label="Genie conversation history"]', 'History');
  const click = (el: HTMLElement) => act(() => el.click());

  /** Start a LIVE turn that stays in flight until the returned handles resolve. */
  async function startLiveTurn(question: string) {
    const progress = deferred<GenieLiveProgress>();
    const complete = deferred<GenieAnswer>();
    mocks.genieSubmit.mockResolvedValueOnce(LIVE_SUBMIT);
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

  /** A turn that completes inline (deterministic submit) with `payload`. */
  function armInlineAnswer(payload: GenieAnswer) {
    mocks.genieSubmit.mockResolvedValueOnce({
      completed: true,
      conversation_id: payload.conversation_id ?? null,
      message_id: payload.message_id ?? null,
      progress_token: null,
      question_hash: null,
      response: payload,
    });
  }

  it('Stop aborts the turn, restores the question, marks the bubble Stopped, unlocks the header, and a late reply lands nowhere', async () => {
    render();
    expect(newThread().disabled).toBe(false);
    const turn = await startLiveTurn('How many borrowers are in the money?');
    expect(newThread().disabled).toBe(true);
    expect(history().disabled).toBe(true);
    expect(askButton().disabled).toBe(true);

    await click(stopButton());

    // The client turn is aborted and the composer has the question back.
    expect(turn.signal.aborted).toBe(true);
    expect(input().value).toBe('How many borrowers are in the money?');
    expect(container.querySelector('.genie__msg--stopped')).not.toBeNull();
    expect(container.querySelector('.genie__msg--stopped')?.textContent).toContain('Stopped');
    expect(container.querySelector('.genie-progress')).toBeNull();
    // New thread and History are usable again; Ask is unlocked.
    expect(newThread().disabled).toBe(false);
    expect(history().disabled).toBe(false);
    expect(askButton().disabled).toBe(false);
    expect(container.querySelector('[data-genie-announcer="panel"]')?.textContent).toContain('Stopped');

    // The server keeps working (no cancel endpoint yet). When its reply
    // arrives after Stop, the generation counter makes it land nowhere.
    await act(async () => {
      turn.progress.resolve(TERMINAL);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await act(async () => {
      turn.complete.resolve(answer());
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    await flush();
    expect(getGenieTurns()).toEqual([]);
    expect(container.textContent).not.toContain('124,946 borrowers');
    expect(window.localStorage.getItem('mip.genie.conversationId')).toBeNull();
    // Nothing was polled after the abort.
    expect(mocks.genieProgress.mock.calls.length).toBe(1);
  });

  it('a stopped turn survives the panel being closed and reopened, then the composer can be edited and sent again', async () => {
    render();
    await startLiveTurn('Which states lead?');
    await click(stopButton());
    setOpen(false);
    setOpen(true);
    expect(container.querySelector('.genie__msg--stopped')).not.toBeNull();
    expect(input().value).toBe('Which states lead?');

    // Regenerate under the stopped question sends it as a NEW turn.
    armInlineAnswer(answer({ answer: 'Texas leads.' }));
    const regenerate = container.querySelector<HTMLButtonElement>(
      '.genie__msg-actions--user button[aria-label="Regenerate answer"]',
    );
    expect(regenerate).not.toBeNull();
    await click(regenerate!);
    await waitUntil(() => container.textContent?.includes('Texas leads.') ?? false);
    expect(mocks.genieSubmit.mock.calls.length).toBe(2);
    expect(mocks.genieSubmit.mock.calls[1][0]).toBe('Which states lead?');
  });

  it('Retry on a failed bubble re-asks the question as a new turn', async () => {
    render();
    mocks.genieSubmit.mockRejectedValueOnce(new Error('network down'));
    act(() => setInputValue(input(), 'How many HELOC candidates are there?'));
    await click(askButton());
    await waitUntil(() => container.textContent?.includes('Genie session reset') ?? false);
    const retry = container.querySelector<HTMLButtonElement>('button[aria-label="Retry question"]');
    expect(retry, 'the failed bubble offers Retry').not.toBeNull();
    expect(container.querySelector('button[aria-label="Regenerate answer"]')).toBeNull();

    armInlineAnswer(answer({ answer: 'There are 2,405 HELOC candidates.' }));
    await click(retry!);
    await waitUntil(() => container.textContent?.includes('2,405 HELOC candidates') ?? false);
    expect(mocks.genieSubmit.mock.calls.length).toBe(2);
    expect(mocks.genieSubmit.mock.calls[1][0]).toBe('How many HELOC candidates are there?');
    // The failed turn stays in the transcript: a Genie thread is not rewritten.
    expect(getGenieTurns().length).toBe(2);
  });

  it('Regenerate re-asks an answered question as a second submit and keeps the first answer', async () => {
    render();
    armInlineAnswer(answer({ answer: 'First answer: 100 borrowers.' }));
    act(() => setInputValue(input(), 'How many borrowers?'));
    await click(askButton());
    await waitUntil(() => container.textContent?.includes('First answer') ?? false);

    const regenerate = container.querySelector<HTMLButtonElement>(
      '.genie__msg--ai button[aria-label="Regenerate answer"]',
    );
    expect(regenerate).not.toBeNull();
    expect(regenerate!.title).toContain('cannot rewrite its history');
    armInlineAnswer(answer({ answer: 'Second answer: 101 borrowers.' }));
    await click(regenerate!);
    await waitUntil(() => container.textContent?.includes('Second answer') ?? false);
    expect(mocks.genieSubmit.mock.calls.map((call) => call[0])).toEqual(['How many borrowers?', 'How many borrowers?']);
    expect(container.textContent).toContain('First answer');
    expect(getGenieTurns().length).toBe(2);
  });

  it('Edit reloads a sent question into the composer without sending it', async () => {
    render();
    armInlineAnswer(answer());
    act(() => setInputValue(input(), 'Show the top 10 borrowers by lead score.'));
    await click(askButton());
    await waitUntil(() => container.textContent?.includes('124,946') ?? false);
    expect(input().value).toBe('');

    const edit = container.querySelector<HTMLButtonElement>('button[aria-label="Edit question"]');
    expect(edit).not.toBeNull();
    await click(edit!);
    await flush();
    expect(input().value).toBe('Show the top 10 borrowers by lead score.');
    expect(mocks.genieSubmit.mock.calls.length).toBe(1);
  });

  it('ArrowUp in an empty composer recalls the last question; a draft is left alone', async () => {
    render();
    armInlineAnswer(answer());
    act(() => setInputValue(input(), 'Which segments have the highest approval rate?'));
    await click(askButton());
    await waitUntil(() => container.textContent?.includes('124,946') ?? false);
    expect(input().value).toBe('');

    const arrowUp = () =>
      act(() => {
        input().dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true, cancelable: true }));
      });
    arrowUp();
    await flush();
    expect(input().value).toBe('Which segments have the highest approval rate?');

    act(() => setInputValue(input(), 'a draft'));
    arrowUp();
    expect(input().value).toBe('a draft');
    expect(mocks.genieSubmit.mock.calls.length).toBe(1);
  });

  it('Stop never overwrites a draft: the draft stays and the note offers Edit for the stopped question', async () => {
    render();
    const turn = await startLiveTurn('How many HELOC candidates are there?');
    act(() => setInputValue(input(), 'A follow-up draft'));

    await click(stopButton());

    expect(turn.signal.aborted).toBe(true);
    expect(input().value).toBe('A follow-up draft');
    expect(container.querySelector('[data-genie-announcer="panel"]')?.textContent).toContain('draft was kept');
    const note = container.querySelector('.genie__msg--stopped');
    expect(note).not.toBeNull();
    const edit = container.querySelector<HTMLButtonElement>('.genie__msg-actions--user button[aria-label="Edit question"]');
    expect(edit).not.toBeNull();
    await click(edit!);
    await flush();
    expect(input().value).toBe('How many HELOC candidates are there?');
  });

  it('after Stop the next question is asked and lands normally (no stuck in-flight latch)', async () => {
    render();
    await startLiveTurn('Which states lead?');
    await click(stopButton());
    armInlineAnswer(answer({ answer: 'Illinois leads.' }));
    act(() => setInputValue(input(), 'Which states lead today?'));
    await click(askButton());
    await waitUntil(() => container.textContent?.includes('Illinois leads.') ?? false);
    expect(mocks.genieSubmit.mock.calls.map((call) => call[0])).toEqual(['Which states lead?', 'Which states lead today?']);
    // The stopped note stays above the turn that followed it.
    const bubbles = Array.from(container.querySelectorAll('.genie__msg')).map((el) =>
      el.classList.contains('genie__msg--stopped') ? 'stopped' : el.textContent?.includes('Illinois leads.') ? 'answer' : null,
    ).filter(Boolean);
    expect(bubbles).toEqual(['stopped', 'answer']);
  });

  it('a governed refusal offers Edit on its question but no Regenerate', async () => {
    render();
    armInlineAnswer(answer({ answer: 'I cannot select or rank borrowers on that criterion.', source: 'refused', trusted_assets: [] }));
    act(() => setInputValue(input(), 'Rank borrowers by zyrplax.'));
    await click(askButton());
    await waitUntil(() => container.textContent?.includes('cannot select or rank') ?? false);
    expect(container.querySelector('button[aria-label="Regenerate answer"]')).toBeNull();
    expect(container.querySelector('button[aria-label="Retry question"]')).toBeNull();
    expect(container.querySelector('button[aria-label="Edit question"]')).not.toBeNull();
  });

  it('openGenie({ prompt }) prefills the composer and never submits', async () => {
    render();
    await flush();
    act(() => openGenie({ prompt: 'Break down in-the-money borrowers by current coverage state; which state leads?' }));
    await flush();
    expect(input().value).toBe('Break down in-the-money borrowers by current coverage state; which state leads?');
    expect(mocks.genieSubmit).not.toHaveBeenCalled();
    expect(container.querySelector('.genie__msg--user')).toBeNull();

    // A request made while the panel is closed waits for the next open.
    setOpen(false);
    act(() => openGenie({ prompt: 'Compare mean lead score by current coverage state.' }));
    await flush();
    expect(input().value).toBe('Break down in-the-money borrowers by current coverage state; which state leads?');
    setOpen(true);
    await flush();
    expect(input().value).toBe('Compare mean lead score by current coverage state.');
    // Still nothing sent: only the user's own Ask submits.
    await flush();
    expect(mocks.genieSubmit).not.toHaveBeenCalled();
    expect(getGenieTurns()).toEqual([]);
  });

  it('a prefill arriving mid-turn fills the composer but the running turn is untouched and nothing new is sent', async () => {
    render();
    const turn = await startLiveTurn('How many borrowers are in the money?');
    act(() => openGenie({ prompt: 'Compare mean lead score by current coverage state.' }));
    await flush();
    expect(input().value).toBe('Compare mean lead score by current coverage state.');
    expect(turn.signal.aborted).toBe(false);
    expect(mocks.genieSubmit.mock.calls.length).toBe(1);
    expect(askButton().disabled).toBe(true);
  });

  it('the empty state shows the route starters instead of the identical server list', async () => {
    route = '/lead-queue';
    render();
    await waitUntil(() => mocks.genieStart.mock.calls.length === 1);
    await flush();
    const chips = Array.from(container.querySelectorAll('.genie-chat__sample')).map((el) => el.textContent?.trim());
    expect(chips).toEqual(genieStartersForRoute('/lead-queue'));
    expect(chips).not.toContain('Server starter one');
  });

  it('falls back to the server starters on a route without curated ones', async () => {
    route = '/ask-genie';
    render();
    await waitUntil(() => container.querySelectorAll('.genie-chat__sample').length === 2);
    const chips = Array.from(container.querySelectorAll('.genie-chat__sample')).map((el) => el.textContent?.trim());
    expect(chips).toEqual(['Server starter one', 'Server starter two']);
  });
});
