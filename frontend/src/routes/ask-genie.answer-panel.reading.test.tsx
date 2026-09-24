/**
 * @vitest-environment happy-dom
 *
 * Reading the /ask-genie thread (audit 2026-09-21 wave 3, `genie-08`): a
 * thread of three or more turns this route never saw land renders its
 * earlier turns as their digest (question bubble and source chip unchanged),
 * the full answer unmounted until the reader opens it. A turn that became
 * earlier because a new answer landed stays as the reader saw it.
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
vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api');
  return {
    ...actual,
    api: {
      genieFeedback: vi.fn().mockResolvedValue({ accepted: true }),
      genieSessions: vi.fn().mockResolvedValue([]),
    },
  };
});

import { __resetGenieTurnStoreForTests } from '../lib/genieInFlightTurn';
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
    clearGenieTurns();
  });

  function render() {
    act(() =>
      root.render(
        <MemoryRouter>
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
});
