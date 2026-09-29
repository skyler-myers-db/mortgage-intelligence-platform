/**
 * @vitest-environment happy-dom
 *
 * `/ask-genie/:conversationId` (audit 2026-09-21 `shell-03` remainder,
 * constraint 14) at the rendered layer: the real route under a real router
 * with both of its patterns, on the REAL transcript and in-flight turn
 * stores; only the api boundary is mocked. The link hydrates only a
 * shape-valid id the actor owns, fails closed with one neutral state for a
 * 404, a 403 and a malformed id (which never reaches the network), holds
 * while another conversation's turn is in flight, is written by History load
 * with one request in total, and is dropped by a reset.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieAnswer, GenieStartResult, GrowthAgentHomeResponse } from '../types';

const apiMocks = vi.hoisted(() => ({
  genieStart: vi.fn(),
  genieSubmit: vi.fn(),
  genieProgress: vi.fn(),
  genieComplete: vi.fn(),
  genieSessions: vi.fn(),
  genieSession: vi.fn(),
  genieFeedback: vi.fn(),
  growthAgent: vi.fn(),
  growthAgentCapabilities: vi.fn(),
}));

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: apiMocks,
}));

vi.mock('../components/AppContext', () => ({
  useApp: () => ({
    genieOpen: false,
    setGenieOpen: vi.fn(),
    lender: 'Summit Mortgage',
    refreshWorkspace: vi.fn(),
    setDrawer: vi.fn(),
    showEvidence: true,
    showConfidence: true,
  }),
}));

import { ApiError } from '../lib/api';
import { GENIE_CONVERSATION_RESET_EVENT, GENIE_CONVERSATION_STORAGE_KEY } from '../lib/genieConversation';
import { clearGenieTurns, getGenieTurns, setGenieTurns } from '../lib/genieConversationStore';
import {
  __resetGenieTurnStoreForTests,
  __setGenieTurnLockForTests,
  startGenieTurn,
  stopGenieTurn,
} from '../lib/genieInFlightTurn';
import AskGenie from './ask-genie';
import { GENIE_CONVERSATION_ID_RE, genieConversationQueryKey } from './ask-genie.deep-link';
import { GENIE_LINK_NOT_FOUND_COPY, GENIE_LINK_UNAVAILABLE_COPY } from '../components/mortgage/GenieConversationLinkState';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// Mounts the whole route plus a lazy chunk on a shared, often loaded machine.
const TIMEOUT_MS = 30_000;

const HEX_ID = '0123456789abcdef0123456789abcdef';
const UUID_ID = '01234567-89ab-cdef-0123-456789abcdef';
const OTHER_HEX_ID = 'fedcba9876543210fedcba9876543210';

const HOME: GrowthAgentHomeResponse = { workflows: [], monitors: [], capabilities: [] };
const START: GenieStartResult = {
  // A bootstrap conversation the link must never be overridden by.
  conversation_id: 'ffffffffffffffffffffffffffffffff',
  trusted_assets: ['mip.gold.borrower_360'],
  sample_questions: [],
};

function answer(text: string, conversationId: string, messageId: string): GenieAnswer {
  return {
    answer: text,
    question: `Question for ${messageId}`,
    source: 'genie',
    trusted_assets: ['mip.gold.borrower_360'],
    conversation_id: conversationId,
    message_id: messageId,
    genie_status: 'COMPLETED',
    question_hash: messageId,
    follow_up_questions: [],
  };
}

function session(conversationId: string, text = 'Restored answer.') {
  return {
    conversation_id: conversationId,
    turns: [{ question: 'Which cohorts converted', response: answer(text, conversationId, 'msg-old') }],
  };
}

function apiError(status: number): ApiError {
  return new ApiError('ignored server text', { path: '/api/genie/sessions/x', status });
}

function installStorage(): void {
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
  Object.defineProperty(Element.prototype, 'scrollIntoView', {
    configurable: true,
    writable: true,
    value: () => undefined,
  });
}

const where = { pathname: '', search: '' };

function LocationProbe() {
  const location = useLocation();
  useEffect(() => {
    where.pathname = location.pathname;
    where.search = location.search;
  });
  return null;
}

function setTextAreaValue(el: HTMLTextAreaElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
  if (!setter) throw new Error('missing textarea value setter');
  setter.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
}

async function waitUntil(condition: () => boolean, timeoutMs = 10_000): Promise<void> {
  const startedAt = Date.now();
  while (!condition()) {
    if (Date.now() - startedAt > timeoutMs) throw new Error('waitUntil timeout');
    await act(async () => new Promise((resolve) => setTimeout(resolve, 10)));
  }
}

describe('/ask-genie/:conversationId', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    installStorage();
    Object.values(apiMocks).forEach((mock) => mock.mockReset());
    apiMocks.genieStart.mockResolvedValue(START);
    apiMocks.genieSessions.mockResolvedValue([]);
    apiMocks.genieFeedback.mockResolvedValue({ accepted: true });
    apiMocks.growthAgent.mockResolvedValue(HOME);
    apiMocks.growthAgentCapabilities.mockResolvedValue(HOME);
    clearGenieTurns();
    __resetGenieTurnStoreForTests();
    __setGenieTurnLockForTests(() => Promise.resolve({ kind: 'held', release: () => undefined }));
    where.pathname = '';
    where.search = '';
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    __resetGenieTurnStoreForTests();
    __setGenieTurnLockForTests(null);
    clearGenieTurns();
  });

  function mountAt(path: string): QueryClient {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={[path]}>
            <LocationProbe />
            <Routes>
              <Route path="/ask-genie" element={<AskGenie />} />
              <Route path="/ask-genie/:conversationId" element={<AskGenie />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    return queryClient;
  }

  const askPanel = () => {
    const panel = container.querySelector<HTMLElement>('section[role="tabpanel"][id$="ask"]');
    if (!panel) throw new Error('Ask tabpanel not rendered');
    return panel;
  };
  const composer = () => container.querySelector<HTMLTextAreaElement>('textarea[aria-label="Ask Genie — question"]');
  const linkState = () => askPanel().querySelector('[data-genie-link]')?.getAttribute('data-genie-link') ?? null;
  const userBubbles = () => Array.from(askPanel().querySelectorAll('.genie__msg--user')).map((el) => el.textContent ?? '');
  const button = (label: string) =>
    Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find((b) => b.textContent?.trim() === label);

  it.each([
    ['a 32-hex id', HEX_ID],
    ['a UUID id', UUID_ID],
  ])('hydrates %s: the thread, the stored id, and a follow-up that carries it', async (_label, id) => {
    apiMocks.genieSession.mockResolvedValue(session(id));
    setGenieTurns([{ question: 'An older thread', response: answer('Older answer.', 'conv-old', 'm-0') }]);
    mountAt(`/ask-genie/${id}`);

    await waitUntil(() => container.textContent?.includes('Restored answer.') ?? false);
    expect(apiMocks.genieSession).toHaveBeenCalledTimes(1);
    expect(apiMocks.genieSession.mock.calls[0][0]).toBe(id);
    expect(userBubbles()).toEqual(['Which cohorts converted']);
    expect(container.textContent).not.toContain('Older answer.');
    expect(window.localStorage.getItem(GENIE_CONVERSATION_STORAGE_KEY)).toBe(id);
    expect(where.pathname).toBe(`/ask-genie/${id}`);

    // The URL wins over the genieStart bootstrap conversation.
    // The wire shape of a completed submit (GenieSubmitResult).
    const submit = {
      completed: true,
      conversation_id: id,
      message_id: 'msg-new',
      progress_token: null,
      question_hash: 'h',
      response: answer('Follow-up answer.', id, 'msg-new'),
    };
    apiMocks.genieSubmit.mockResolvedValue(submit);
    const field = composer();
    if (!field) throw new Error('composer not rendered');
    act(() => setTextAreaValue(field, 'Which counties lead?'));
    await act(async () => {
      container.querySelector<HTMLButtonElement>('form.genie-composer button[type="submit"]')?.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await waitUntil(() => apiMocks.genieSubmit.mock.calls.length > 0);
    expect(apiMocks.genieSubmit.mock.calls[0][1]).toBe(id);
    await waitUntil(() => container.textContent?.includes('Follow-up answer.') ?? false);
    // A settled turn never rewrites the URL.
    expect(where.pathname).toBe(`/ask-genie/${id}`);
  }, TIMEOUT_MS);

  it('renders byte-identical DOM for a 404 and a 403, with no thread, no composer and nothing written', async () => {
    const html: string[] = [];
    for (const status of [404, 403]) {
      apiMocks.genieSession.mockReset();
      apiMocks.genieSession.mockRejectedValue(apiError(status));
      setGenieTurns([{ question: 'An older thread', response: answer('Older answer.', 'conv-old', 'm-0') }]);
      window.localStorage.setItem(GENIE_CONVERSATION_STORAGE_KEY, 'conv-old');
      mountAt(`/ask-genie/${HEX_ID}`);
      await waitUntil(() => linkState() === 'not-found');
      expect(askPanel().textContent).toContain(GENIE_LINK_NOT_FOUND_COPY);
      expect(askPanel().textContent).not.toContain('ignored server text');
      expect(composer()).toBeNull();
      expect(askPanel().querySelector('.genie__msg--user')).toBeNull();
      expect(apiMocks.genieSession).toHaveBeenCalledTimes(1);
      expect(apiMocks.genieSubmit).not.toHaveBeenCalled();
      expect(window.localStorage.getItem(GENIE_CONVERSATION_STORAGE_KEY)).toBe('conv-old');
      expect(getGenieTurns().map((turn) => turn.question)).toEqual(['An older thread']);
      html.push(askPanel().innerHTML);
      act(() => root.unmount());
      root = createRoot(container);
    }
    expect(html[0]).toBe(html[1]);
  }, TIMEOUT_MS);

  it.each(['not-an-id', '0123456789abcdef', `${HEX_ID}0`, 'B-0123456789ABC', 'conv-1'])(
    'a malformed id (%s) shows the same neutral state and makes zero session requests',
    async (id) => {
      mountAt(`/ask-genie/${id}`);
      await waitUntil(() => linkState() === 'not-found');
      expect(GENIE_CONVERSATION_ID_RE.test(id)).toBe(false);
      expect(askPanel().textContent).toContain(GENIE_LINK_NOT_FOUND_COPY);
      expect(apiMocks.genieSession).not.toHaveBeenCalled();
      expect(composer()).toBeNull();
    },
    TIMEOUT_MS,
  );

  it('an unavailable read offers Retry with fixed copy, and a retry that succeeds hydrates', async () => {
    apiMocks.genieSession.mockRejectedValueOnce(apiError(503)).mockResolvedValueOnce(session(HEX_ID));
    mountAt(`/ask-genie/${HEX_ID}`);
    await waitUntil(() => linkState() === 'unavailable');
    expect(askPanel().textContent).toContain(GENIE_LINK_UNAVAILABLE_COPY);
    expect(askPanel().textContent).not.toContain('ignored server text');
    expect(composer()).toBeNull();

    act(() => button('Retry')?.click());
    await waitUntil(() => container.textContent?.includes('Restored answer.') ?? false);
    expect(apiMocks.genieSession).toHaveBeenCalledTimes(2);
  }, TIMEOUT_MS);

  it('a Retry in flight keeps focus on the button, ignores a second press, and a failed retry leaves focus there', async () => {
    let failRetry: (reason: unknown) => void = () => undefined;
    apiMocks.genieSession
      .mockRejectedValueOnce(apiError(503))
      .mockImplementationOnce(() => new Promise((_resolve, reject) => { failRetry = reject; }));
    mountAt(`/ask-genie/${HEX_ID}`);
    await waitUntil(() => linkState() === 'unavailable');
    const retry = button('Retry');
    if (!retry) throw new Error('Retry not rendered');
    act(() => retry.focus());

    act(() => retry.click());
    await waitUntil(() => retry.textContent?.trim() === 'Retrying…');
    expect(retry.disabled, 'never native disabled mid-retry').toBe(false);
    expect(retry.getAttribute('aria-disabled')).toBe('true');
    expect(document.activeElement).toBe(retry);
    act(() => retry.click());
    expect(apiMocks.genieSession, 'a press during the retry starts no second read').toHaveBeenCalledTimes(2);

    await act(async () => failRetry(apiError(503)));
    await waitUntil(() => retry.textContent?.trim() === 'Retry');
    expect(retry.hasAttribute('aria-disabled')).toBe(false);
    expect(document.activeElement).toBe(retry);
    expect(linkState()).toBe('unavailable');
  }, TIMEOUT_MS);

  it('Open Ask Genie replaces the URL with /ask-genie and hands focus to the composer', async () => {
    mountAt('/ask-genie/not-an-id?tab=ask');
    await waitUntil(() => linkState() === 'not-found');
    act(() => button('Open Ask Genie')?.click());
    await waitUntil(() => composer() !== null);
    expect(where.pathname).toBe('/ask-genie');
    expect(document.activeElement).toBe(composer());
  }, TIMEOUT_MS);

  it('holds while a turn in another conversation is in flight, then hydrates once it ends', async () => {
    apiMocks.genieSession.mockResolvedValue(session(HEX_ID));
    // A turn in flight in a DIFFERENT conversation, asked before the link opened.
    apiMocks.genieSubmit.mockReturnValue(new Promise(() => undefined));
    setGenieTurns([{ question: 'Current thread question', response: answer('Current answer.', OTHER_HEX_ID, 'm-1') }]);
    startGenieTurn({ question: 'Still running', conversationId: OTHER_HEX_ID, surface: 'route', startedAt: Date.now() });

    mountAt(`/ask-genie/${HEX_ID}`);
    await waitUntil(() => askPanel().querySelector('[data-genie-link="held"]') !== null);
    expect(askPanel().textContent).toContain('Opens after the current answer lands.');
    expect(container.textContent).toContain('Current answer.');
    expect(container.textContent).not.toContain('Restored answer.');
    expect(askPanel().querySelector('button[aria-label="Stop this Genie turn"]')).not.toBeNull();

    act(() => {
      stopGenieTurn();
    });
    await waitUntil(() => container.textContent?.includes('Restored answer.') ?? false);
    expect(askPanel().querySelector('[data-genie-link="held"]')).toBeNull();
    expect(window.localStorage.getItem(GENIE_CONVERSATION_STORAGE_KEY)).toBe(HEX_ID);
  }, TIMEOUT_MS);

  it('hydrates at once when the turn in flight is in the linked conversation', async () => {
    apiMocks.genieSession.mockResolvedValue(session(HEX_ID));
    apiMocks.genieSubmit.mockReturnValue(new Promise(() => undefined));
    startGenieTurn({ question: 'Same thread, running', conversationId: HEX_ID, surface: 'route', startedAt: Date.now() });

    mountAt(`/ask-genie/${HEX_ID}`);
    await waitUntil(() => container.textContent?.includes('Restored answer.') ?? false);
    expect(askPanel().querySelector('[data-genie-link="held"]')).toBeNull();
  }, TIMEOUT_MS);

  it('History load writes the URL and the link hydrates from the cache: one request in total', async () => {
    apiMocks.genieSessions.mockResolvedValue([
      { conversation_id: HEX_ID, title: 'Equity sweep', last_activity_at: '2026-09-01T12:00:00Z', turn_count: 1 },
    ]);
    apiMocks.genieSession.mockResolvedValue(session(HEX_ID));
    const queryClient = mountAt('/ask-genie?tab=ask');
    await waitUntil(() => composer() !== null);

    act(() => container.querySelector<HTMLButtonElement>('button[aria-label="Genie conversation history"]')?.click());
    await waitUntil(() => container.textContent?.includes('Equity sweep') ?? false);
    act(() => {
      Array.from(container.querySelectorAll<HTMLButtonElement>('button'))
        .find((b) => b.textContent?.includes('Equity sweep'))
        ?.click();
    });
    await waitUntil(() => container.textContent?.includes('Restored answer.') ?? false);

    expect(where.pathname).toBe(`/ask-genie/${HEX_ID}`);
    expect(where.search).toBe('?tab=ask');
    expect(apiMocks.genieSession).toHaveBeenCalledTimes(1);
    expect(queryClient.getQueryData(genieConversationQueryKey(HEX_ID))).toEqual({
      conversationId: HEX_ID,
      turns: session(HEX_ID).turns,
    });
    expect(composer()).not.toBeNull();
  }, TIMEOUT_MS);

  it('History load of a legacy id adopts it directly and keeps it out of the URL', async () => {
    apiMocks.genieSessions.mockResolvedValue([
      { conversation_id: 'conv-past', title: 'Legacy sweep', last_activity_at: null, turn_count: 1 },
    ]);
    apiMocks.genieSession.mockResolvedValue(session('conv-past'));
    mountAt('/ask-genie');
    await waitUntil(() => composer() !== null);
    act(() => container.querySelector<HTMLButtonElement>('button[aria-label="Genie conversation history"]')?.click());
    await waitUntil(() => container.textContent?.includes('Legacy sweep') ?? false);
    act(() => {
      Array.from(container.querySelectorAll<HTMLButtonElement>('button'))
        .find((b) => b.textContent?.includes('Legacy sweep'))
        ?.click();
    });
    await waitUntil(() => container.textContent?.includes('Restored answer.') ?? false);
    expect(where.pathname).toBe('/ask-genie');
    expect(window.localStorage.getItem(GENIE_CONVERSATION_STORAGE_KEY)).toBe('conv-past');
  }, TIMEOUT_MS);

  it('a reset (New thread, an actor change) takes the id out of the URL', async () => {
    apiMocks.genieSession.mockResolvedValue(session(HEX_ID));
    mountAt(`/ask-genie/${HEX_ID}?tab=ask`);
    await waitUntil(() => container.textContent?.includes('Restored answer.') ?? false);

    act(() => {
      window.dispatchEvent(new Event(GENIE_CONVERSATION_RESET_EVENT));
    });
    await waitUntil(() => where.pathname === '/ask-genie');
    expect(where.search).toBe('?tab=ask');
    expect(container.textContent).not.toContain('Restored answer.');
    expect(composer()).not.toBeNull();

    // New thread on the route goes through the same reset.
    act(() => root.unmount());
    root = createRoot(container);
    mountAt(`/ask-genie/${HEX_ID}`);
    await waitUntil(() => container.textContent?.includes('Restored answer.') ?? false);
    act(() => button('New thread')?.click());
    await waitUntil(() => where.pathname === '/ask-genie');
  }, TIMEOUT_MS);
});
