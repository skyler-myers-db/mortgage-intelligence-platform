/**
 * @vitest-environment happy-dom
 *
 * The floating panel on the conversation-switch signal (audit 2026-09-21
 * `shell-03` deep-link remainder), at the rendered layer: the real panel on
 * the REAL transcript and in-flight turn stores; only the api boundary is
 * mocked. A conversation /ask-genie adopts (its History load, a deep link)
 * becomes the open panel's follow-up target, unless a turn in flight owns the
 * id it was submitted with; the panel's own History load tells the route; and
 * the panel keeps an existing link entry current after its own turns.
 */

import { act } from 'react';
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { writeGenieConversationId } from '../../lib/genieConversation';
import { clearGenieTurns, setGenieTurns, type GenieTurn } from '../../lib/genieConversationStore';
import {
  _resetGenieLinkCacheSyncForTests,
  emitGenieConversationSwitch,
  genieConversationQueryKey,
  subscribeGenieConversationSwitch,
  type GenieConversationSwitchEvent,
} from '../../lib/genieConversationSwitch';
import { __resetGenieTurnStoreForTests, startGenieTurn } from '../../lib/genieInFlightTurn';
import { createMipQueryClient } from '../../lib/queryClient';
import type { GenieAnswer, GenieStartResult } from '../../types';
import { __resetGenieAnnouncerForTests } from './useGenieAnnouncer';

const mocks = vi.hoisted(() => ({
  genieStart: vi.fn(),
  genieSubmit: vi.fn(),
  genieProgress: vi.fn(),
  genieComplete: vi.fn(),
  genieFeedback: vi.fn(),
  genieSessions: vi.fn(),
  genieSession: vi.fn(),
}));

vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api');
  return { ...actual, api: mocks };
});

vi.mock('../AppContext', () => ({
  useApp: () => ({
    genieOpen: true,
    setGenieOpen: vi.fn(),
    lender: 'Test Lender',
    refreshWorkspace: vi.fn(),
    setDrawer: vi.fn(),
  }),
}));

import { GenieChat } from './GenieChat';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const START: GenieStartResult = { conversation_id: 'conv-bootstrap', trusted_assets: [], sample_questions: [] };
const ROUTE_ID = '0123456789abcdef0123456789abcdef';
const PANEL_ID = 'fedcba9876543210fedcba9876543210';

function answer(text: string, conversationId: string, messageId: string): GenieAnswer {
  return {
    answer: text,
    question: `Question for ${messageId}`,
    source: 'genie',
    trusted_assets: ['mip.gold.borrower_360'],
    conversation_id: conversationId,
    message_id: messageId,
    genie_status: 'COMPLETED',
    follow_up_questions: [],
  };
}

function turns(conversationId: string, text: string): GenieTurn[] {
  return [{ question: `Earlier in ${text}`, response: answer(text, conversationId, `m-${text}`) }];
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
  Object.defineProperties(window, {
    innerWidth: { configurable: true, value: 1_440 },
    innerHeight: { configurable: true, value: 900 },
  });
}

function setInputValue(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  if (!setter) throw new Error('missing input value setter');
  setter.call(input, value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

describe('the floating panel on the conversation switch', () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    Object.values(mocks).forEach((mock) => mock.mockReset());
    installStorage();
    clearGenieTurns();
    __resetGenieTurnStoreForTests();
    mocks.genieStart.mockResolvedValue(START);
    mocks.genieSessions.mockResolvedValue([]);
    queryClient = createMipQueryClient();
    queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/lead-queue']}>
            <GenieChat />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
    __resetGenieTurnStoreForTests();
    __resetGenieAnnouncerForTests();
    _resetGenieLinkCacheSyncForTests();
    clearGenieTurns();
  });

  async function waitUntil(condition: () => boolean, timeoutMs = 10_000) {
    const startedAt = Date.now();
    while (!condition()) {
      if (Date.now() - startedAt > timeoutMs) throw new Error('waitUntil timeout');
      await act(async () => new Promise((resolve) => setTimeout(resolve, 10)));
    }
  }

  const input = () => {
    const field = container.querySelector<HTMLInputElement>('input[aria-label="Ask Genie"]');
    if (!field) throw new Error('floating Genie input not rendered');
    return field;
  };

  async function askFromPanel(question: string) {
    act(() => setInputValue(input(), question));
    await act(async () => {
      container.querySelector<HTMLButtonElement>('button[aria-label="Ask"]')?.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await waitUntil(() => mocks.genieSubmit.mock.calls.length > 0);
  }

  /** What /ask-genie's adoptGenieConversation does on a route History load. */
  function routeAdopts(conversationId: string, text: string) {
    act(() => {
      setGenieTurns(turns(conversationId, text));
      writeGenieConversationId(conversationId);
      emitGenieConversationSwitch({ conversationId, surface: 'route' });
    });
  }

  function completed(text: string, conversationId: string) {
    return {
      completed: true,
      conversation_id: conversationId,
      message_id: 'msg-next',
      progress_token: null,
      question_hash: 'h',
      response: answer(text, conversationId, 'msg-next'),
    };
  }

  it('a route History load with the panel open: the panel’s next submit carries the loaded id', async () => {
    await waitUntil(() => mocks.genieStart.mock.calls.length > 0);
    routeAdopts(ROUTE_ID, 'Route-loaded answer.');
    await waitUntil(() => container.textContent?.includes('Route-loaded answer.') ?? false);

    mocks.genieSubmit.mockResolvedValue(completed('Panel follow-up.', ROUTE_ID));
    await askFromPanel('Which counties lead?');
    expect(mocks.genieSubmit.mock.calls[0][0]).toBe('Which counties lead?');
    expect(mocks.genieSubmit.mock.calls[0][1]).toBe(ROUTE_ID);
  });

  it('a switch while a turn is in flight leaves that turn’s conversation alone', async () => {
    await waitUntil(() => mocks.genieStart.mock.calls.length > 0);
    let land: (value: unknown) => void = () => undefined;
    mocks.genieSubmit.mockReturnValueOnce(new Promise((resolve) => { land = resolve; }));
    startGenieTurn({ question: 'Still running', conversationId: PANEL_ID, surface: 'panel', startedAt: Date.now() });
    act(() => emitGenieConversationSwitch({ conversationId: ROUTE_ID, surface: 'route' }));
    await act(async () => land(completed('Landed.', PANEL_ID)));
    await waitUntil(() => container.textContent?.includes('Landed.') ?? false);

    mocks.genieSubmit.mockResolvedValue(completed('Next.', PANEL_ID));
    await askFromPanel('And then?');
    expect(mocks.genieSubmit.mock.calls[1][1]).toBe(PANEL_ID);
  });

  it('the panel’s own History load tells the route (surface panel)', async () => {
    mocks.genieSessions.mockResolvedValue([
      { conversation_id: PANEL_ID, title: 'Equity sweep', last_activity_at: '2026-09-01T12:00:00Z', turn_count: 1 },
    ]);
    mocks.genieSession.mockResolvedValue({ conversation_id: PANEL_ID, turns: turns(PANEL_ID, 'Panel-loaded answer.') });
    const heard: GenieConversationSwitchEvent[] = [];
    const stop = subscribeGenieConversationSwitch((event) => heard.push(event));

    act(() => container.querySelector<HTMLButtonElement>('button[aria-label="Genie conversation history"]')?.click());
    await waitUntil(() => container.textContent?.includes('Equity sweep') ?? false);
    act(() => {
      Array.from(container.querySelectorAll<HTMLButtonElement>('button'))
        .find((b) => b.textContent?.includes('Equity sweep'))
        ?.click();
    });
    await waitUntil(() => container.textContent?.includes('Panel-loaded answer.') ?? false);
    stop();
    expect(heard).toEqual([{ conversationId: PANEL_ID, surface: 'panel' }]);
  });

  it('keeps an existing link entry current after its own turn, and never creates one', async () => {
    await waitUntil(() => mocks.genieStart.mock.calls.length > 0);
    routeAdopts(ROUTE_ID, 'Route-loaded answer.');
    queryClient.setQueryData(genieConversationQueryKey(ROUTE_ID), { conversationId: ROUTE_ID, turns: turns(ROUTE_ID, 'Route-loaded answer.') });

    mocks.genieSubmit.mockResolvedValue(completed('Panel follow-up.', ROUTE_ID));
    await askFromPanel('Which counties lead?');
    await waitUntil(() => container.textContent?.includes('Panel follow-up.') ?? false);
    await act(async () => Promise.resolve());
    const cached = queryClient.getQueryData<{ turns: GenieTurn[] }>(genieConversationQueryKey(ROUTE_ID));
    expect(cached?.turns.map((turn) => turn.question)).toEqual(['Earlier in Route-loaded answer.', 'Which counties lead?']);
    expect(queryClient.getQueryData(genieConversationQueryKey(PANEL_ID))).toBeUndefined();
  });
});
