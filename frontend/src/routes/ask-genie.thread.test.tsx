/**
 * @vitest-environment happy-dom
 *
 * Ask Genie thread view. The route used to show ONE answer — a second
 * question erased the first, so the deep-dive surface had no conversation
 * even though the floating panel kept one. Both now read the shared
 * transcript store, newest turn first.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieAnswer, GenieStartResult, GrowthAgentHomeResponse } from '../types';

const growthAgent = vi.fn();
const growthAgentCapabilities = vi.fn();
const genieStart = vi.fn();
const genie = vi.fn();
const genieSessions = vi.fn();
const genieSession = vi.fn();
const navigate = vi.fn();
const setDrawer = vi.fn();

vi.mock('../lib/api', () => ({
  api: {
    growthAgent: (...args: unknown[]) => growthAgent(...args),
    growthAgentCapabilities: (...args: unknown[]) => growthAgentCapabilities(...args),
    genieStart: (...args: unknown[]) => genieStart(...args),
    genie: (...args: unknown[]) => genie(...args),
    genieSessions: (...args: unknown[]) => genieSessions(...args),
    genieSession: (...args: unknown[]) => genieSession(...args),
    genieFeedback: vi.fn().mockResolvedValue({ accepted: true }),
    genieSubmit: async (
      question: string,
      conversationId?: string | null,
      signal?: AbortSignal,
    ) => ({
      completed: true,
      conversation_id: null,
      message_id: null,
      progress_token: null,
      question_hash: null,
      response: await genie(question, conversationId, signal),
    }),
  },
  ApiError: class ApiError extends Error {
    status = 500;
  },
  isAbortError: () => false,
  isWarmingUpError: () => false,
}));

vi.mock('../components/AppContext', () => ({
  useApp: () => ({
    refreshWorkspace: vi.fn(),
    setDrawer,
    showEvidence: true,
    showConfidence: true,
  }),
}));

vi.mock('react-router', async () => {
  const actual = await vi.importActual<typeof import('react-router')>('react-router');
  return { ...actual, useNavigate: () => navigate };
});

import { clearGenieTurns, getGenieTurns } from '../lib/genieConversationStore';
import AskGenie from './ask-genie';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const HOME: GrowthAgentHomeResponse = { workflows: [], monitors: [], capabilities: [] };
const START: GenieStartResult = {
  conversation_id: null,
  trusted_assets: ['mip.gold.borrower_360'],
  sample_questions: [],
};

function answer(text: string, id: string): GenieAnswer {
  return {
    answer: text,
    source: 'genie',
    trusted_assets: ['mip.gold.borrower_360'],
    conversation_id: 'conv-1',
    message_id: id,
    genie_status: 'COMPLETED',
    question_hash: id,
    metric_value: null,
    table_rows: null,
    follow_up_questions: [],
  };
}

function installStorage() {
  for (const key of ['localStorage', 'sessionStorage'] as const) {
    const values = new Map<string, string>();
    Object.defineProperty(window, key, {
      configurable: true,
      value: {
        getItem: (k: string) => values.get(k) ?? null,
        setItem: (k: string, v: string) => values.set(k, v),
        removeItem: (k: string) => values.delete(k),
        clear: () => values.clear(),
      },
    });
  }
}

function setTextAreaValue(el: HTMLTextAreaElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
  if (!setter) throw new Error('missing textarea value setter');
  setter.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
}

// Generous per-test budget: this mounts the whole route and drives real
// query/effect cycles on a machine that often runs several suites at once.
const TIMEOUT_MS = 30_000;

describe('Ask Genie thread view', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.resetAllMocks();
    installStorage();
    clearGenieTurns();
    growthAgent.mockResolvedValue(HOME);
    growthAgentCapabilities.mockResolvedValue(HOME);
    genieStart.mockResolvedValue(START);
    genie.mockResolvedValue(null);
    genieSessions.mockResolvedValue([]);
    genieSession.mockResolvedValue({ conversation_id: null, turns: [] });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    clearGenieTurns();
  });

  function mount() {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/ask-genie']}>
            <AskGenie />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  async function waitUntil(condition: () => boolean, timeoutMs = 10_000) {
    const startedAt = Date.now();
    while (!condition()) {
      if (Date.now() - startedAt > timeoutMs) throw new Error('waitUntil timeout');
      await act(async () => new Promise((resolve) => setTimeout(resolve, 10)));
    }
  }

  function button(name: RegExp) {
    const match = Array.from(container.querySelectorAll<HTMLButtonElement>('button'))
      .find((candidate) => name.test(candidate.textContent ?? ''));
    if (!match) throw new Error(`button not rendered: ${name}`);
    return match;
  }

  function questionInput() {
    const input = container.querySelector<HTMLTextAreaElement>(
      'textarea[aria-label="Ask Genie — question"]',
    );
    if (!input) throw new Error('Genie question input not rendered');
    return input;
  }

  function userBubbles() {
    return Array.from(container.querySelectorAll('.genie__msg--user')).map(
      (el) => el.textContent ?? '',
    );
  }

  async function ask(question: string) {
    act(() => setTextAreaValue(questionInput(), question));
    act(() => button(/^Ask Genie$/).click());
    await waitUntil(() => container.textContent?.includes(question) ?? false);
  }

  it('keeps both turns and shows the latest one first', async () => {
    genie
      .mockResolvedValueOnce(answer('First governed answer.', 'msg-1'))
      .mockResolvedValueOnce(answer('Second governed answer.', 'msg-2'));

    mount();
    await waitUntil(() => container.querySelector('textarea') !== null);

    await ask('Which states lead');
    await waitUntil(() => container.textContent?.includes('First governed answer.') ?? false);
    await ask('Which ZIPs lead');
    await waitUntil(() => container.textContent?.includes('Second governed answer.') ?? false);

    // The first turn survives the second ask …
    expect(container.textContent).toContain('First governed answer.');
    // … and the newest exchange sits at the top of the thread.
    expect(userBubbles()).toEqual(['Which ZIPs lead', 'Which states lead']);
    expect(container.textContent).toContain('Earlier in this thread');
    expect(getGenieTurns().map((turn) => turn.question)).toEqual([
      'Which states lead',
      'Which ZIPs lead',
    ]);
  }, TIMEOUT_MS);

  it('shows the submitted question and the progress rail while in flight', async () => {
    let settle: ((value: GenieAnswer) => void) | null = null;
    genie.mockImplementation(
      () => new Promise<GenieAnswer>((resolve) => {
        settle = resolve;
      }),
    );

    mount();
    await waitUntil(() => container.querySelector('textarea') !== null);
    act(() => setTextAreaValue(questionInput(), 'Which states lead'));
    act(() => button(/^Ask Genie$/).click());
    await waitUntil(() => container.querySelector('.genie-progress') !== null);

    expect(userBubbles()).toEqual(['Which states lead']);
    expect(container.querySelector('.genie-progress')).not.toBeNull();
    expect(getGenieTurns()).toHaveLength(0);

    await act(async () => {
      settle?.(answer('Settled answer.', 'msg-1'));
    });
    await waitUntil(() => container.textContent?.includes('Settled answer.') ?? false);
    expect(container.querySelector('.genie-progress')).toBeNull();
  }, TIMEOUT_MS);

  it('clears the composer once the turn it asked has settled', async () => {
    genie.mockResolvedValueOnce(answer('Settled answer.', 'msg-1'));

    mount();
    await waitUntil(() => container.querySelector('textarea') !== null);
    await ask('Which states lead');
    await waitUntil(() => container.textContent?.includes('Settled answer.') ?? false);

    expect(questionInput().value).toBe('');
    // The question itself stays visible — in the thread, not the composer.
    expect(userBubbles()).toEqual(['Which states lead']);
  }, TIMEOUT_MS);

  it('empties the thread on New thread', async () => {
    genie.mockResolvedValueOnce(answer('Settled answer.', 'msg-1'));

    mount();
    await waitUntil(() => container.querySelector('textarea') !== null);
    await ask('Which states lead');
    await waitUntil(() => container.textContent?.includes('Settled answer.') ?? false);

    act(() => button(/^New thread$/).click());

    expect(getGenieTurns()).toHaveLength(0);
    expect(userBubbles()).toEqual([]);
    expect(container.textContent).not.toContain('Settled answer.');
    expect(container.textContent).toContain('Started a new Genie thread.');
  }, TIMEOUT_MS);

  it('replaces the thread with a conversation loaded from History', async () => {
    genie.mockResolvedValueOnce(answer('Settled answer.', 'msg-1'));
    genieSessions.mockResolvedValue([
      {
        conversation_id: 'conv-past',
        title: 'Equity sweep',
        last_activity_at: '2026-09-01T12:00:00Z',
        turn_count: 1,
      },
    ]);
    genieSession.mockResolvedValue({
      conversation_id: 'conv-past',
      turns: [{ question: 'Which cohorts converted', response: answer('Restored answer.', 'msg-old') }],
    });

    mount();
    await waitUntil(() => container.querySelector('textarea') !== null);
    await ask('Which states lead');
    await waitUntil(() => container.textContent?.includes('Settled answer.') ?? false);

    const historyToggle = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Genie conversation history"]',
    );
    if (!historyToggle) throw new Error('History control not rendered');
    act(() => historyToggle.click());
    await waitUntil(() => container.textContent?.includes('Equity sweep') ?? false);
    act(() => button(/Equity sweep/).click());
    await waitUntil(() => container.textContent?.includes('Restored answer.') ?? false);

    // The restored thread REPLACES the live one, and the follow-up will
    // continue the Databricks conversation that was loaded.
    expect(userBubbles()).toEqual(['Which cohorts converted']);
    expect(container.textContent).not.toContain('Settled answer.');
    expect(window.localStorage.getItem('mip.genie.conversationId')).toBe('conv-past');
  }, TIMEOUT_MS);
});
