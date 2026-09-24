/**
 * @vitest-environment happy-dom
 *
 * Route composer controls (audit 2026-09-21 `genie-03`, client-only slice):
 * ArrowUp in an empty composer recalls the last question, Edit reloads a
 * sent question, Regenerate re-asks it as a NEW turn through `onAsk` (never
 * on a refusal, which would only repeat itself). The
 * panel is rendered with its props (callbacks out), the way the route
 * mounts it; no network is involved.
 */
import { act, createRef } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieAnswer } from '../types';
import { clearGenieTurns, setGenieTurns } from '../lib/genieConversationStore';

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
      // A turn held in flight for the busy-rule cases; it never settles.
      genieSubmit: vi.fn(() => new Promise(() => undefined)),
    },
  };
});

import { __resetGenieTurnStoreForTests, getGenieTurnSnapshot, startGenieTurn } from '../lib/genieInFlightTurn';
import { AskGenieAnswerPanel } from './ask-genie.answer-panel';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function answer(text: string, id: string): GenieAnswer {
  return {
    answer: text,
    source: 'genie',
    trusted_assets: ['mip.gold.borrower_360'],
    conversation_id: 'conv-1',
    message_id: id,
    genie_status: 'COMPLETED',
    question_hash: id,
    follow_up_questions: [],
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

describe('AskGenieAnswerPanel composer controls', () => {
  let container: HTMLDivElement;
  let root: Root;
  const onQuestionChange = vi.fn();
  const onAsk = vi.fn();
  const questionRef = createRef<HTMLTextAreaElement>();

  beforeEach(() => {
    vi.clearAllMocks();
    installSessionStorage();
    clearGenieTurns();
    setGenieTurns([
      { question: 'Which segments have the highest approval rate?', response: answer('Retention Risk leads.', 'm1') },
      { question: 'What is the approval trend over the last 30 days?', response: answer('Rising.', 'm2') },
    ]);
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

  function render(question: string, inFlight = false) {
    act(() => {
      // The in-flight turn is the tab's, read from the store (runtime-01).
      if (inFlight && !getGenieTurnSnapshot().inFlight) {
        startGenieTurn({ question: 'A question in flight', conversationId: null, surface: 'route', startedAt: 0 });
      }
      root.render(
        <MemoryRouter>
          <AskGenieAnswerPanel
            questionRef={questionRef}
            question={question}
            onQuestionChange={onQuestionChange}
            onAsk={onAsk}
            onNewThread={() => undefined}
            onLoadSession={() => undefined}
            sampleQuestions={[]}
            onFollowUp={() => undefined}
            onAction={() => undefined}
            actionStatus={null}
          />
        </MemoryRouter>,
      );
    });
  }

  const textarea = () => container.querySelector<HTMLTextAreaElement>('textarea[aria-label="Ask Genie — question"]')!;
  const arrowUp = () =>
    act(() => {
      textarea().dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true, cancelable: true }));
    });

  it('ArrowUp in an empty composer recalls the latest question of the thread', () => {
    render('');
    arrowUp();
    expect(onQuestionChange).toHaveBeenCalledWith('What is the approval trend over the last 30 days?');
    expect(onAsk).not.toHaveBeenCalled();
  });

  it('ArrowUp leaves a draft alone', () => {
    render('a draft');
    arrowUp();
    expect(onQuestionChange).not.toHaveBeenCalled();
  });

  it('ArrowUp prefers the question currently in flight', () => {
    render('', true);
    arrowUp();
    expect(onQuestionChange).toHaveBeenCalledWith('A question in flight');
  });

  it('Edit under a sent question reloads it into the composer and focuses it', () => {
    render('');
    const edits = container.querySelectorAll<HTMLButtonElement>('button[aria-label="Edit question"]');
    // Oldest turn first, the floating panel's order: two questions, two Edit controls.
    expect(edits.length).toBe(2);
    act(() => edits[0].click());
    expect(onQuestionChange).toHaveBeenCalledWith('Which segments have the highest approval rate?');
    expect(document.activeElement).toBe(textarea());
    expect(onAsk).not.toHaveBeenCalled();
  });

  it('Regenerate re-asks a sent question as a new turn, and is held while a turn is in flight', () => {
    render('');
    // The latest turn is the last one in the thread, right above the composer.
    const regenerates = container.querySelectorAll<HTMLButtonElement>('button[aria-label="Regenerate answer"]');
    const regenerate = regenerates[regenerates.length - 1];
    expect(regenerate.title).toContain('cannot rewrite its history');
    act(() => regenerate.click());
    expect(onAsk).toHaveBeenCalledWith('What is the approval trend over the last 30 days?');

    render('', true);
    const helds = container.querySelectorAll<HTMLButtonElement>('button[aria-label="Regenerate answer"]');
    const held = helds[helds.length - 1];
    expect(held.disabled).toBe(true);
    expect(held.title).toContain('still answering');
  });

  it('labels each answer source in plain words, the governed path in its tooltip (flow-10)', () => {
    render('');
    const chips = [...container.querySelectorAll<HTMLButtonElement>('.genie-thread .evidence-chip')];
    expect(chips).toHaveLength(2);
    for (const chip of chips) {
      expect(chip.textContent).toBe('Borrower 360');
      expect(chip.title).toBe('mip.gold.borrower_360');
    }
  });

  it('a refused turn keeps Edit but offers no Regenerate or Retry', () => {
    setGenieTurns([
      {
        question: 'Rank borrowers by zyrplax.',
        response: { ...answer('I cannot select or rank borrowers on that criterion.', 'm3'), source: 'refused', trusted_assets: [] },
      },
    ]);
    render('');
    expect(container.querySelectorAll('button[aria-label="Edit question"]').length).toBe(1);
    expect(container.querySelector('button[aria-label="Regenerate answer"]')).toBeNull();
    expect(container.querySelector('button[aria-label="Retry question"]')).toBeNull();
  });
});

describe('AskGenieAnswerPanel docked composer (visual-07)', () => {
  let container: HTMLDivElement;
  let root: Root;
  const onAsk = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    installSessionStorage();
    clearGenieTurns();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    __resetGenieTurnStoreForTests();
  });

  function render(question: string) {
    act(() => {
      root.render(
        <MemoryRouter>
          <AskGenieAnswerPanel
            questionRef={createRef<HTMLTextAreaElement>()}
            question={question}
            onQuestionChange={() => undefined}
            onAsk={onAsk}
            onNewThread={() => undefined}
            onLoadSession={() => undefined}
            sampleQuestions={['Which states have the most prime refi candidates?']}
            onFollowUp={() => undefined}
            onAction={() => undefined}
            actionStatus={null}
          />
        </MemoryRouter>,
      );
    });
  }

  const composer = () => container.querySelector<HTMLFormElement>('form.genie-composer')!;
  const ask = () =>
    [...composer().querySelectorAll<HTMLButtonElement>('button')].find((b) => b.textContent === 'Ask Genie')!;

  it('is the card footer, after the suggestions, with a real placeholder', () => {
    render('');
    const textarea = composer().querySelector('textarea')!;
    expect(textarea.getAttribute('aria-label')).toBe('Ask Genie — question');
    expect(textarea.placeholder).toMatch(/prime refi candidates/);
    expect(textarea.getAttribute('rows')).toBe('2');
    expect(composer().classList.contains('surface__ft')).toBe(true);
    const samples = container.querySelector('[aria-label="Suggested Genie questions"]')!;
    expect(samples.compareDocumentPosition(composer()) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('keeps Ask primary, disabled until there is text, and submits through the form', () => {
    render('');
    expect(ask().classList.contains('btn--primary')).toBe(true);
    expect(ask().type).toBe('submit');
    expect(ask().disabled).toBe(true);

    render('Which states lead?');
    expect(ask().disabled).toBe(false);
    act(() => {
      composer().dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });
    expect(onAsk).toHaveBeenCalledWith('Which states lead?');
  });

  it('never submits an empty question', () => {
    render('   ');
    act(() => {
      composer().dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });
    expect(onAsk).not.toHaveBeenCalled();
  });
});
