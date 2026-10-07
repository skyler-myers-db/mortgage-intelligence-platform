/**
 * @vitest-environment happy-dom
 *
 * The floating panel mounted before the actor gate opened (lib/actorScope,
 * D-identity-review-b), at the rendered layer: the stored Genie conversation
 * id is a PRIVATE_LOCAL key, so the panel's first read returns null while the
 * gate is pending. When the gate opens for the id's owner the panel re-reads
 * it, so that owner's next question continues their conversation instead of
 * silently starting a new Genie thread. When it opens for another actor the
 * gate has removed the id, and the question starts a new thread.
 */
import { act } from 'react';
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { _resetActorScopeForTests, actorScopeStatus, observeActor } from '../../lib/actorScope';
import {
  GENIE_CONVERSATION_RESET_EVENT,
  GENIE_CONVERSATION_STORAGE_KEY,
  readGenieConversationId,
} from '../../lib/genieConversation';
import { __resetGenieTurnStoreForTests } from '../../lib/genieInFlightTurn';
import { createMipQueryClient } from '../../lib/queryClient';
import { ACTOR_A, ACTOR_B } from '../../test/actorKeys';
import type { GenieStartResult } from '../../types';
import { __resetGenieAnnouncerForTests } from './useGenieAnnouncer';

const mocks = vi.hoisted(() => ({
  genie: vi.fn(),
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
      genie: mocks.genie,
      genieAction: mocks.genieAction,
      genieFeedback: mocks.genieFeedback,
      genieStart: mocks.genieStart,
      genieSubmit: mocks.genieSubmit,
      genieProgress: mocks.genieProgress,
      genieComplete: mocks.genieComplete,
    },
  };
});

// The panel is OPEN from its first render: its open effect reads the id while
// the gate is still pending, so only the gate's 'opened' re-read can find it.
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

const A_CONVERSATION = 'conv-of-a';
const A_LATER_CONVERSATION = 'conv-of-a-later';
/** No bootstrap conversation: the stored id is the only one there is. */
const START: GenieStartResult = { conversation_id: null, trusted_assets: [], sample_questions: [] };

function installStorage(area: 'localStorage' | 'sessionStorage'): Map<string, string> {
  const values = new Map<string, string>();
  Object.defineProperty(window, area, {
    configurable: true,
    value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
      clear: () => values.clear(),
    },
  });
  return values;
}

function setInputValue(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  if (!setter) throw new Error('missing input value setter');
  setter.call(input, value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.dispatchEvent(new Event('change', { bubbles: true }));
}

function testQueryClient(): QueryClient {
  const client = createMipQueryClient();
  client.setDefaultOptions({ queries: { ...client.getDefaultOptions().queries, retry: false } });
  return client;
}

describe('the floating panel mounted while the actor gate is pending', () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;
  let local: Map<string, string>;

  beforeEach(() => {
    Object.values(mocks).forEach((mock) => mock.mockReset());
    // Fresh storage per test: nothing persisted by another suite leaks in.
    local = installStorage('localStorage');
    installStorage('sessionStorage');
    // A new document: pending, both areas stamped A, A's conversation stored.
    _resetActorScopeForTests({ status: 'pending', owner: ACTOR_A });
    local.set(GENIE_CONVERSATION_STORAGE_KEY, A_CONVERSATION);
    mocks.genieStart.mockResolvedValue(START);
    mocks.genieSubmit.mockResolvedValue({
      completed: true,
      conversation_id: null,
      message_id: null,
      progress_token: null,
      question_hash: null,
      response: {
        answer: 'The governed opportunity result is ready.',
        question: 'Break this down by state',
        source: 'genie',
        trusted_assets: [],
        conversation_id: A_CONVERSATION,
        message_id: 'msg-of-a',
        genie_status: 'COMPLETED',
        follow_up_questions: [],
      },
    });
    queryClient = testQueryClient();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
    __resetGenieTurnStoreForTests();
    __resetGenieAnnouncerForTests();
  });

  async function flush(): Promise<void> {
    await act(async () => new Promise((resolve) => setTimeout(resolve, 10)));
  }

  async function waitUntil(condition: () => boolean, timeoutMs = 10_000): Promise<void> {
    const startedAt = Date.now();
    while (!condition()) {
      if (Date.now() - startedAt > timeoutMs) throw new Error('waitUntil timeout');
      await flush();
    }
  }

  async function mountWhilePending(): Promise<void> {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/']}>
            <GenieChat />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await waitUntil(() => mocks.genieStart.mock.calls.length > 0);
    await flush();
    expect(actorScopeStatus(), 'the panel mounted and settled before the gate opened').toBe('pending');
  }

  async function ask(question: string): Promise<void> {
    const input = container.querySelector<HTMLInputElement>('input[aria-label="Ask Genie"]');
    if (!input) throw new Error('floating Genie input not rendered');
    act(() => setInputValue(input, question));
    const askButton = container.querySelector<HTMLButtonElement>('button[aria-label="Ask"]');
    if (!askButton) throw new Error('Ask button not rendered');
    await waitUntil(() => !askButton.disabled);
    await act(async () => {
      askButton.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await waitUntil(() => mocks.genieSubmit.mock.calls.length === 1);
  }

  it("re-reads the stored conversation when the gate opens for its owner, so A's next question continues it", async () => {
    await mountWhilePending();

    act(() => observeActor({ key: ACTOR_A }));
    expect(actorScopeStatus()).toBe('open');
    await flush();
    await ask('Break this down by state');

    expect(mocks.genieSubmit.mock.calls[0]?.[0]).toBe('Break this down by state');
    expect(mocks.genieSubmit.mock.calls[0]?.[1], "A's question continues A's conversation").toBe(A_CONVERSATION);
  });

  it("a trusted nobody closes the gate and the panel drops A's id; A's return reopens it and A's next submit continues A's conversation (D-identity-review-a2)", async () => {
    await mountWhilePending();
    act(() => observeActor({ key: ACTOR_A }));
    await flush();

    const reset = vi.fn();
    window.addEventListener(GENIE_CONVERSATION_RESET_EVENT, reset);
    try {
      act(() => observeActor({ key: null }));
    } finally {
      window.removeEventListener(GENIE_CONVERSATION_RESET_EVENT, reset);
    }
    expect(actorScopeStatus()).toBe('closed');
    expect(reset, 'the panel is reset through the event').toHaveBeenCalledOnce();
    expect(readGenieConversationId(), 'nothing is readable while closed').toBeNull();
    expect(local.get(GENIE_CONVERSATION_STORAGE_KEY), 'and nothing is removed').toBe(A_CONVERSATION);
    await flush();

    // Meanwhile A's other tab moved on to a newer conversation. Only a panel
    // whose own id went null re-reads it when the gate reopens; one that kept
    // the old id would send that instead.
    local.set(GENIE_CONVERSATION_STORAGE_KEY, A_LATER_CONVERSATION);
    act(() => observeActor({ key: ACTOR_A }));
    expect(actorScopeStatus()).toBe('open');
    await flush();
    await ask('Break this down by state');

    expect(mocks.genieSubmit.mock.calls[0]?.[1], "A's question continues A's (current) conversation").toBe(A_LATER_CONVERSATION);
  });

  it("starts a new thread when the gate opens for another actor: the re-read goes through the gate, which removed A's id", async () => {
    await mountWhilePending();

    act(() => observeActor({ key: ACTOR_B }));
    expect(actorScopeStatus()).toBe('open');
    expect(local.get(GENIE_CONVERSATION_STORAGE_KEY), "the gate removed A's id").toBeUndefined();
    await flush();
    await ask('Break this down by state');

    expect(mocks.genieSubmit.mock.calls[0]?.[1], "B's first question carries none of A's conversation").toBeNull();
  });
});
