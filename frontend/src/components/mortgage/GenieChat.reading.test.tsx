/**
 * @vitest-environment happy-dom
 *
 * Reading in the floating Genie panel (audit 2026-09-21 wave 3, `genie-08`):
 * a reader who scrolled up to an earlier turn is never moved when an answer
 * lands. The panel offers "New answer" instead, which scrolls the answer's
 * start into view and focuses it. A reader who follows the transcript still
 * gets the landed answer anchored at its start (`motion-v2`).
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieLiveProgress, GenieSubmitResult } from '../../lib/api';
import { appendGenieTurn, clearGenieTurns } from '../../lib/genieConversationStore';
import { __resetGenieTurnStoreForTests } from '../../lib/genieInFlightTurn';
import { __resetGenieAnnouncerForTests } from './useGenieAnnouncer';
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
    genieOpen: true,
    setGenieOpen: mocks.setGenieOpen,
    lender: 'Test Lender',
    refreshWorkspace: mocks.refreshWorkspace,
    setDrawer: mocks.setDrawer,
  }),
}));

import { GenieChat } from './GenieChat';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const START: GenieStartResult = { conversation_id: null, trusted_assets: ['mip.gold.borrower_360'], sample_questions: [] };
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
    answer: 'There are 124,946 borrowers passing the screen.',
    source: 'genie',
    trusted_assets: ['mip.gold.borrower_360'],
    conversation_id: 'conv-live',
    message_id: 'msg-live',
    genie_status: 'COMPLETED',
    follow_up_questions: [],
    ...overrides,
  };
}

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

function setInputValue(input: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

describe('floating Genie panel respects the reader (genie-08)', () => {
  let container: HTMLDivElement;
  let root: Root;
  const scrollIntoView = vi.fn();

  beforeEach(() => {
    Object.values(mocks).forEach((mock) => mock.mockReset());
    scrollIntoView.mockReset();
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
      value: (query: string) => ({ matches: false, media: query, addEventListener: () => undefined, removeEventListener: () => undefined }),
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    __resetGenieTurnStoreForTests();
    __resetGenieAnnouncerForTests();
    clearGenieTurns();
  });

  async function waitUntil(condition: () => boolean, timeoutMs = 10_000) {
    const startedAt = Date.now();
    while (!condition()) {
      if (Date.now() - startedAt > timeoutMs) throw new Error('waitUntil timeout');
      await act(async () => new Promise((resolve) => setTimeout(resolve, 10)));
    }
  }

  /** Render the open panel over one earlier settled turn, with a sized transcript. */
  function renderWithEarlierTurn(): HTMLElement {
    appendGenieTurn('An earlier question?', answer({ answer: 'The earlier answer.', message_id: 'msg-earlier' }));
    act(() => {
      root.render(
        <MemoryRouter>
          <GenieChat />
        </MemoryRouter>,
      );
    });
    const body = container.querySelector<HTMLElement>('.genie__body');
    if (!body) throw new Error('transcript body not rendered');
    Object.defineProperty(body, 'scrollHeight', { configurable: true, value: 1_000 });
    Object.defineProperty(body, 'clientHeight', { configurable: true, value: 300 });
    return body;
  }

  async function ask(question: string) {
    const progress = deferred<GenieLiveProgress>();
    const complete = deferred<GenieAnswer>();
    mocks.genieSubmit.mockResolvedValueOnce(LIVE_SUBMIT);
    mocks.genieProgress.mockReturnValueOnce(progress.promise);
    mocks.genieComplete.mockReturnValueOnce(complete.promise);
    const input = container.querySelector<HTMLInputElement>('input[aria-label="Ask Genie"]')!;
    act(() => setInputValue(input, question));
    await act(async () => {
      container.querySelector<HTMLButtonElement>('button[aria-label="Ask"]')!.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await waitUntil(() => mocks.genieProgress.mock.calls.length > 0);
    return {
      async land(payload: GenieAnswer) {
        await act(async () => {
          progress.resolve(TERMINAL);
          await new Promise((resolve) => setTimeout(resolve, 0));
        });
        await waitUntil(() => mocks.genieComplete.mock.calls.length === 1);
        await act(async () => {
          complete.resolve(payload);
          await new Promise((resolve) => setTimeout(resolve, 0));
        });
        await waitUntil(() => container.querySelectorAll('.genie__msg--ai .genie-answer').length === 2);
      },
    };
  }

  function scrollTo(body: HTMLElement, top: number) {
    act(() => {
      body.scrollTop = top;
      body.dispatchEvent(new Event('scroll'));
    });
  }

  const jump = () => container.querySelector<HTMLButtonElement>('button.genie__jump');
  const latestAnswer = () => {
    const answers = container.querySelectorAll<HTMLElement>('.genie__msg--ai');
    return answers[answers.length - 1];
  };

  it('an answer landing while the reader reads an earlier turn does not move them; New answer appears', async () => {
    const body = renderWithEarlierTurn();
    const turn = await ask('How many borrowers pass the screen?');
    // Sending follows the transcript to its end.
    expect(body.scrollTop).toBe(1_000);
    // The reader scrolls up to the earlier turn while the answer is running.
    scrollTo(body, 100);

    await turn.land(answer());

    expect(body.scrollTop).toBe(100);
    expect(scrollIntoView).not.toHaveBeenCalled();
    expect(jump()?.textContent).toBe('New answer');
    expect(jump()?.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true');
  });

  it('New answer scrolls the answer start into view, focuses its bubble and goes away', async () => {
    const body = renderWithEarlierTurn();
    const turn = await ask('How many borrowers pass the screen?');
    scrollTo(body, 100);
    await turn.land(answer());

    act(() => jump()!.click());

    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    expect(scrollIntoView.mock.calls[0][0]).toBe(latestAnswer());
    expect(scrollIntoView.mock.calls[0][1]).toEqual({ block: 'start', behavior: 'smooth' });
    expect(document.activeElement).toBe(latestAnswer());
    expect(latestAnswer().getAttribute('tabindex')).toBe('-1');
    expect(jump()).toBeNull();
  });

  it('New answer clears once the reader scrolls the answer top into view', async () => {
    const body = renderWithEarlierTurn();
    const turn = await ask('How many borrowers pass the screen?');
    scrollTo(body, 100);
    await turn.land(answer());
    expect(jump()).not.toBeNull();

    body.getBoundingClientRect = () => ({ top: 0, bottom: 300, left: 0, right: 400, width: 400, height: 300, x: 0, y: 0, toJSON: () => ({}) });
    latestAnswer().getBoundingClientRect = () => ({ top: 120, bottom: 600, left: 0, right: 400, width: 400, height: 480, x: 0, y: 120, toJSON: () => ({}) });
    scrollTo(body, 500);

    expect(jump()).toBeNull();
  });

  it('a reader following the transcript still gets the landed answer anchored at its start', async () => {
    const body = renderWithEarlierTurn();
    const turn = await ask('How many borrowers pass the screen?');
    // Still within one line of the end: following.
    scrollTo(body, 690);

    await turn.land(answer());

    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    expect(scrollIntoView.mock.calls[0][0]).toBe(latestAnswer());
    expect(jump()).toBeNull();
  });

  it('collapses earlier turns the panel never saw land; the latest and its chips stay', () => {
    appendGenieTurn('First question?', answer({ answer: 'First **answer**.', message_id: 'msg-1' }));
    appendGenieTurn('Second question?', answer({ answer: 'Second answer.', message_id: 'msg-2' }));
    appendGenieTurn('Third question?', answer({ answer: 'Third answer.', message_id: 'msg-3' }));
    act(() => {
      root.render(
        <MemoryRouter>
          <GenieChat />
        </MemoryRouter>,
      );
    });
    const bubbles = Array.from(container.querySelectorAll<HTMLElement>('.genie__msg--ai'));
    expect(bubbles.map((b) => (b.querySelector('.genie-answer') ? 'full' : 'collapsed'))).toEqual([
      'collapsed',
      'collapsed',
      'full',
    ]);
    expect(bubbles[0].querySelector('.genie-collapse__digest')?.textContent).toBe('First answer.');
    // Questions and the evidence chips are unchanged.
    expect(container.querySelectorAll('.genie__msg--user')).toHaveLength(3);
    expect(bubbles.map((b) => b.querySelector('.sources') !== null)).toEqual([true, true, true]);
    act(() => bubbles[1].querySelector<HTMLButtonElement>('button.genie-collapse__toggle')!.click());
    expect(bubbles[1].querySelector('.genie-answer')).not.toBeNull();
  });

  it('sending again from the panel follows the transcript once more', async () => {
    const body = renderWithEarlierTurn();
    const first = await ask('How many borrowers pass the screen?');
    scrollTo(body, 100);
    await first.land(answer());
    expect(jump()).not.toBeNull();

    mocks.genieProgress.mockReset();
    mocks.genieComplete.mockReset();
    await ask('And by state?');

    expect(jump()).toBeNull();
    expect(body.scrollTop).toBe(1_000);
  });
});
