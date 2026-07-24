/**
 * @vitest-environment happy-dom
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
const navigate = vi.fn();
const setDrawer = vi.fn();

vi.mock('../lib/api', () => ({
  api: {
    growthAgent: (...args: unknown[]) => growthAgent(...args),
    growthAgentCapabilities: (...args: unknown[]) => growthAgentCapabilities(...args),
    genieStart: (...args: unknown[]) => genieStart(...args),
    genie: (...args: unknown[]) => genie(...args),
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

import AskGenie from './ask-genie';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const HOME: GrowthAgentHomeResponse = {
  workflows: [],
  monitors: [],
  capabilities: [],
};

const START: GenieStartResult = {
  conversation_id: null,
  trusted_assets: ['mip.gold.borrower_360'],
  sample_questions: [],
};

function answer(followUpQuestions: string[] = []): GenieAnswer {
  return {
    answer: 'The governed opportunity result is ready.',
    source: 'genie',
    trusted_assets: ['mip.gold.borrower_360'],
    conversation_id: 'conv-native-1',
    message_id: 'msg-native-1',
    genie_status: 'COMPLETED',
    question_hash: 'hash-native-1',
    metric_value: null,
    table_rows: null,
    follow_up_questions: followUpQuestions,
  };
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

function setTextAreaValue(el: HTMLTextAreaElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
  if (!setter) throw new Error('missing textarea value setter');
  setter.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
}

describe('Ask Genie conversation continuity', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.resetAllMocks();
    installLocalStorage();
    growthAgent.mockResolvedValue(HOME);
    growthAgentCapabilities.mockResolvedValue(HOME);
    genieStart.mockResolvedValue(START);
    genie.mockResolvedValue(null);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
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

  it.each(['Break this down by state', 'Which ZIPs lead'])(
    'uses the native conversation id for the %s suggestion',
    async (followUp) => {
      const seedQuestion = 'Show current opportunity volume';
      genieStart.mockResolvedValue({ ...START, sample_questions: [seedQuestion] });
      genie.mockResolvedValueOnce(answer([followUp])).mockResolvedValueOnce(answer());

      mount();
      await waitUntil(() => container.textContent?.includes(seedQuestion) ?? false);
      act(() => button(new RegExp(seedQuestion)).click());
      await waitUntil(() => genie.mock.calls.length === 1);
      expect(genie).toHaveBeenNthCalledWith(1, seedQuestion, null, expect.any(AbortSignal));
      await waitUntil(() => container.textContent?.includes(followUp) ?? false);

      act(() => button(new RegExp(followUp)).click());
      await waitUntil(() => genie.mock.calls.length === 2);
      expect(genie).toHaveBeenNthCalledWith(
        2,
        followUp,
        'conv-native-1',
        expect.any(AbortSignal),
      );
    },
  );

  it('continues the bootstrap conversation for ordinary typed text', async () => {
    genieStart.mockResolvedValue({ ...START, conversation_id: 'conv-bootstrap' });
    genie.mockResolvedValueOnce(answer());

    mount();
    await waitUntil(() => window.localStorage.getItem('mip.genie.conversationId') === 'conv-bootstrap');
    act(() => setTextAreaValue(questionInput(), 'Which ZIPs lead'));
    act(() => button(/^Ask Genie$/).click());

    await waitUntil(() => genie.mock.calls.length === 1);
    expect(genie).toHaveBeenCalledWith(
      'Which ZIPs lead',
      'conv-bootstrap',
      expect.any(AbortSignal),
    );
  });

  it('keeps a restored conversation for an ordinary sample question', async () => {
    const sampleQuestion = 'Show current opportunity volume';
    window.localStorage.setItem('mip.genie.conversationId', 'conv-restored');
    genieStart.mockResolvedValue({
      ...START,
      conversation_id: 'conv-bootstrap',
      sample_questions: [sampleQuestion],
    });
    genie.mockResolvedValueOnce(answer());

    mount();
    await waitUntil(() => container.textContent?.includes(sampleQuestion) ?? false);
    act(() => button(new RegExp(sampleQuestion)).click());

    await waitUntil(() => genie.mock.calls.length === 1);
    expect(genie).toHaveBeenCalledWith(
      sampleQuestion,
      'conv-restored',
      expect.any(AbortSignal),
    );
  });

  it('keeps New thread cleared before the next typed question', async () => {
    genieStart.mockResolvedValue({ ...START, conversation_id: 'conv-bootstrap' });
    genie.mockResolvedValueOnce(answer());

    mount();
    await waitUntil(() => window.localStorage.getItem('mip.genie.conversationId') === 'conv-bootstrap');
    const resetListener = vi.fn();
    window.addEventListener('mip:genie-conversation-reset', resetListener);
    act(() => button(/^New thread$/).click());
    expect(window.localStorage.getItem('mip.genie.conversationId')).toBeNull();
    expect(resetListener).toHaveBeenCalledOnce();
    window.removeEventListener('mip:genie-conversation-reset', resetListener);

    act(() => setTextAreaValue(questionInput(), 'Break this down by state'));
    act(() => button(/^Ask Genie$/).click());
    await waitUntil(() => genie.mock.calls.length === 1);
    expect(genie).toHaveBeenCalledWith(
      'Break this down by state',
      null,
      expect.any(AbortSignal),
    );
  });

  it('does not restore a late bootstrap response after New thread', async () => {
    let resolveStart: ((value: GenieStartResult) => void) | undefined;
    genieStart.mockReturnValue(new Promise((resolve) => { resolveStart = resolve; }));
    genie.mockResolvedValueOnce(answer());

    mount();
    act(() => button(/^New thread$/).click());
    await act(async () => {
      resolveStart?.({ ...START, conversation_id: 'conv-late-bootstrap' });
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(window.localStorage.getItem('mip.genie.conversationId')).toBeNull();

    act(() => setTextAreaValue(questionInput(), 'Start from the full portfolio'));
    act(() => button(/^Ask Genie$/).click());
    await waitUntil(() => genie.mock.calls.length === 1);
    expect(genie).toHaveBeenCalledWith(
      'Start from the full portfolio',
      null,
      expect.any(AbortSignal),
    );
  });
});
