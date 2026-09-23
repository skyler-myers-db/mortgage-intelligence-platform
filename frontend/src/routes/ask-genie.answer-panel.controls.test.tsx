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
    },
  };
});

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
    clearGenieTurns();
  });

  function render(question: string, inFlight = false) {
    act(() => {
      root.render(
        <MemoryRouter>
          <AskGenieAnswerPanel
            questionRef={questionRef}
            question={question}
            onQuestionChange={onQuestionChange}
            onAsk={onAsk}
            onNewThread={() => undefined}
            onLoadSession={() => undefined}
            loading={inFlight}
            warmingUp={null}
            errorMsg={null}
            onRetry={() => undefined}
            sampleQuestions={[]}
            payload={null}
            submittedQuestion={inFlight ? 'A question in flight' : null}
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
    // Latest turn first, then the earlier one: two questions, two Edit controls.
    expect(edits.length).toBe(2);
    act(() => edits[1].click());
    expect(onQuestionChange).toHaveBeenCalledWith('Which segments have the highest approval rate?');
    expect(document.activeElement).toBe(textarea());
    expect(onAsk).not.toHaveBeenCalled();
  });

  it('Regenerate re-asks a sent question as a new turn, and is held while a turn is in flight', () => {
    render('');
    const regenerate = container.querySelector<HTMLButtonElement>('button[aria-label="Regenerate answer"]')!;
    expect(regenerate.title).toContain('cannot rewrite its history');
    act(() => regenerate.click());
    expect(onAsk).toHaveBeenCalledWith('What is the approval trend over the last 30 days?');

    render('', true);
    const held = container.querySelector<HTMLButtonElement>('button[aria-label="Regenerate answer"]')!;
    expect(held.disabled).toBe(true);
    expect(held.title).toContain('still answering');
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
