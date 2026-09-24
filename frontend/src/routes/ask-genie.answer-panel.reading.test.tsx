/**
 * @vitest-environment happy-dom
 *
 * Reading the /ask-genie thread (audit 2026-09-21 wave 3, `genie-08`): a
 * thread of three or more turns this route never saw land renders its
 * earlier turns as their digest (question bubble and source chip unchanged),
 * the full answer unmounted until the reader opens it. A turn that became
 * earlier because a new answer landed stays as the reader saw it. A new
 * answer card and the question just sent play the one-shot entrance
 * (`motion-v2`) only while the Ask tab is shown.
 */
import { act, createRef } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieAnswer } from '../types';
import { appendGenieTurn, clearGenieTurns, setGenieTurns } from '../lib/genieConversationStore';

vi.mock('../components/AppContext', () => ({
  useApp: () => ({ setDrawer: vi.fn(), showEvidence: true, showConfidence: true }),
}));
vi.mock('../components/HealthProvider', () => ({ useWorkspaceHost: () => null }));
const genieSubmit = vi.hoisted(() => vi.fn());
vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api');
  return {
    ...actual,
    api: {
      genieFeedback: vi.fn().mockResolvedValue({ accepted: true }),
      genieSessions: vi.fn().mockResolvedValue([]),
      genieSubmit,
    },
  };
});

import { __resetGenieTurnStoreForTests, startGenieTurn } from '../lib/genieInFlightTurn';
import { GenieAnnouncerRegion } from '../components/mortgage/GenieAnnouncerRegion';
import { __resetGenieAnnouncerForTests } from '../components/mortgage/useGenieAnnouncer';
import { AskGenieAnswerPanel } from './ask-genie.answer-panel';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function answer(text: string, id: string, overrides: Partial<GenieAnswer> = {}): GenieAnswer {
  return {
    answer: text,
    source: 'genie',
    trusted_assets: ['mip.gold.borrower_360'],
    conversation_id: 'conv-1',
    message_id: id,
    genie_status: 'COMPLETED',
    question_hash: id,
    follow_up_questions: [],
    ...overrides,
  };
}

function installSessionStorage() {
  const values = new Map<string, string>();
  Object.defineProperty(window, 'sessionStorage', {
    configurable: true,
    value: {
      getItem: (k: string) => values.get(k) ?? null,
      setItem: (k: string, v: string) => values.set(k, v),
      removeItem: (k: string) => values.delete(k),
      clear: () => values.clear(),
    },
  });
}

describe('AskGenieAnswerPanel reading (genie-08)', () => {
  let container: HTMLDivElement;
  let root: Root;
  const questionRef = createRef<HTMLTextAreaElement>();

  beforeEach(() => {
    vi.clearAllMocks();
    installSessionStorage();
    clearGenieTurns();
    Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, writable: true, value: vi.fn() });
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

  /** `askTabShown` registers the route surface the way ask-genie.tsx does. */
  function render(askTabShown = true) {
    act(() =>
      root.render(
        <MemoryRouter>
          <GenieAnnouncerRegion surface="route" visible={askTabShown} />
          <AskGenieAnswerPanel
            questionRef={questionRef}
            question=""
            onQuestionChange={vi.fn()}
            onAsk={vi.fn()}
            onNewThread={vi.fn()}
            onLoadSession={vi.fn()}
            sampleQuestions={[]}
            onFollowUp={vi.fn()}
            onAction={vi.fn()}
            actionStatus={null}
          />
        </MemoryRouter>,
      ),
    );
  }

  const thread = () => container.querySelector<HTMLElement>('.genie-thread')!;
  const answerCards = () => Array.from(thread().querySelectorAll<HTMLElement>(':scope > .surface--inset'));

  it('collapses the earlier turns of a stored 3-turn thread; question and source chip stay', () => {
    setGenieTurns([
      { question: 'Which states lead?', response: answer('**Illinois** leads with 1,204 borrowers.', 'm1') },
      { question: 'And by county?', response: answer('Cook County leads.', 'm2') },
      { question: 'And the trend?', response: answer('Rising for three months.', 'm3') },
    ]);
    render();

    expect(thread().querySelectorAll('.genie__msg--user')).toHaveLength(3);
    const cards = answerCards();
    expect(cards).toHaveLength(3);
    // Every card keeps its "Source:" chip row.
    expect(cards.map((card) => card.querySelector('.chip-row') !== null)).toEqual([true, true, true]);
    // Only the latest mounts its full answer.
    expect(cards.map((card) => card.querySelector('.genie-answer') !== null)).toEqual([false, false, true]);
    expect(cards[0].querySelector('.genie-collapse__digest')?.textContent).toBe('Illinois leads with 1,204 borrowers.');

    const toggle = cards[0].querySelector<HTMLButtonElement>('button.genie-collapse__toggle')!;
    act(() => toggle.click());
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(cards[0].querySelector('.genie-answer')).not.toBeNull();
  });

  it('keeps the previous latest in full when a new answer lands on the route', () => {
    setGenieTurns([
      { question: 'Which states lead?', response: answer('Illinois leads.', 'm1') },
      { question: 'And by county?', response: answer('Cook County leads.', 'm2') },
      { question: 'And the trend?', response: answer('Rising.', 'm3') },
    ]);
    render();
    act(() => {
      appendGenieTurn('And the top offer?', answer('Rate-and-term refinance.', 'm4'));
    });
    expect(answerCards().map((card) => card.querySelector('.genie-answer') !== null)).toEqual([false, false, true, true]);
  });

  it('never collapses a 2-turn thread', () => {
    setGenieTurns([
      { question: 'Which states lead?', response: answer('Illinois leads.', 'm1') },
      { question: 'And by county?', response: answer('Cook County leads.', 'm2') },
    ]);
    render();
    expect(thread().querySelector('.genie-collapse')).toBeNull();
    expect(thread().querySelectorAll('.genie-answer')).toHaveLength(2);
  });

  /** Ask on the route; the submit answers at once with `payload`. */
  async function askAndLand(payload: GenieAnswer) {
    let settle: (value: unknown) => void = () => undefined;
    genieSubmit.mockReturnValueOnce(
      new Promise((resolve) => {
        settle = resolve;
      }),
    );
    act(() => {
      startGenieTurn({ question: 'And the top offer?', conversationId: 'conv-1', surface: 'route', startedAt: Date.now() });
    });
    const pendingEntering = Array.from(thread().querySelectorAll('.genie__msg--user')).some((el) =>
      el.classList.contains('genie__msg--entering'),
    );
    await act(async () => {
      settle({
        completed: true,
        conversation_id: 'conv-1',
        message_id: payload.message_id,
        progress_token: null,
        question_hash: null,
        response: payload,
      });
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    return { pendingEntering };
  }

  it('a just-landed answer card and the question just sent enter once (motion-v2)', async () => {
    setGenieTurns([{ question: 'Which states lead?', response: answer('Illinois leads.', 'm1') }]);
    render(true);
    const { pendingEntering } = await askAndLand(answer('Rate-and-term refinance.', 'm2'));
    expect(pendingEntering).toBe(true);
    const cards = answerCards();
    expect(cards).toHaveLength(2);
    expect(cards.map((card) => card.classList.contains('genie-thread__answer--entering'))).toEqual([false, true]);
    const event = new Event('animationend', { bubbles: true });
    Object.defineProperty(event, 'animationName', { value: 'genie-msg-in' });
    act(() => {
      cards[1].dispatchEvent(event);
    });
    expect(thread().querySelector('.genie-thread__answer--entering')).toBeNull();
  });

  it('a question already in flight when the route mounts (coming back mid-turn) does not enter again', () => {
    // The Ask tab is registered as shown BEFORE the thread mounts, so only
    // the mount guard can keep the bubble still.
    act(() =>
      root.render(
        <MemoryRouter>
          <GenieAnnouncerRegion surface="route" visible />
        </MemoryRouter>,
      ),
    );
    genieSubmit.mockReturnValueOnce(new Promise(() => undefined));
    act(() => {
      startGenieTurn({ question: 'Still running?', conversationId: null, surface: 'panel', startedAt: Date.now() });
    });
    render(true);
    const pending = Array.from(thread().querySelectorAll('.genie__msg--user'));
    expect(pending.map((el) => el.textContent)).toEqual(['Still running?']);
    expect(pending[0].classList.contains('genie__msg--entering')).toBe(false);
  });

  it('nothing enters while the Ask tab is hidden, and a stored thread never enters', async () => {
    setGenieTurns([{ question: 'Which states lead?', response: answer('Illinois leads.', 'm1') }]);
    render(false);
    expect(thread().querySelector('.genie-thread__answer--entering')).toBeNull();
    const { pendingEntering } = await askAndLand(answer('Rate-and-term refinance.', 'm2'));
    expect(pendingEntering).toBe(false);
    expect(answerCards()).toHaveLength(2);
    expect(thread().querySelector('.genie-thread__answer--entering, .genie__msg--entering')).toBeNull();
  });
});
