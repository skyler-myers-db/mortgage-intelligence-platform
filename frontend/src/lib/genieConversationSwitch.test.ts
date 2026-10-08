/**
 * @vitest-environment happy-dom
 *
 * The conversation-switch signal and the link-cache sync (audit 2026-09-21
 * `shell-03` deep-link remainder). The rendered proofs live in
 * routes/ask-genie.deep-link.test.tsx and
 * components/mortgage/GenieChat.conversationSwitch.test.tsx; this pins the
 * module contract: the moved grammar is unchanged, the signal is synchronous
 * and unsubscribable, and the sync keeps an EXISTING entry current after a
 * settled turn (reading the thread WITH that turn), never creates one and
 * never fetches.
 */
import { QueryClient } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieAnswer } from '../types';
import { clearGenieTurns, setGenieTurns, type GenieTurn } from './genieConversationStore';
import {
  GENIE_CONVERSATION_ID_RE,
  _resetGenieLinkCacheSyncForTests,
  emitGenieConversationSwitch,
  ensureGenieLinkCacheSync,
  genieConversationQueryKey,
  isGenieConversationId,
  primeGenieLinkCache,
  subscribeGenieConversationSwitch,
} from './genieConversationSwitch';
import { __resetGenieTurnStoreForTests, __setGenieTurnLockForTests, startGenieTurn } from './genieInFlightTurn';
import * as deepLink from '../routes/ask-genie.deep-link';

const apiMocks = vi.hoisted(() => ({ genieSubmit: vi.fn(), genieProgress: vi.fn(), genieComplete: vi.fn() }));
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), api: apiMocks }));

const HEX_ID = '0123456789abcdef0123456789abcdef';
const OTHER_ID = 'fedcba9876543210fedcba9876543210';

function answer(text: string, conversationId: string): GenieAnswer {
  return {
    answer: text,
    question: text,
    source: 'genie',
    trusted_assets: ['mip.gold.borrower_360'],
    conversation_id: conversationId,
    message_id: `m-${text}`,
    genie_status: 'COMPLETED',
    follow_up_questions: [],
  };
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
}

async function settleTurnIn(conversationId: string, question: string): Promise<void> {
  apiMocks.genieSubmit.mockResolvedValueOnce({
    completed: true,
    conversation_id: conversationId,
    message_id: 'm-new',
    progress_token: null,
    question_hash: 'h',
    response: answer(`${question} answered`, conversationId),
  });
  startGenieTurn({ question, conversationId, surface: 'panel', startedAt: Date.now() });
  for (let i = 0; i < 50; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
}

describe('genieConversationSwitch', () => {
  beforeEach(() => {
    installStorage();
    Object.values(apiMocks).forEach((mock) => mock.mockReset());
    clearGenieTurns();
    __resetGenieTurnStoreForTests();
    __setGenieTurnLockForTests(() => Promise.resolve({ kind: 'held', release: () => undefined }));
  });

  afterEach(() => {
    _resetGenieLinkCacheSyncForTests();
    __resetGenieTurnStoreForTests();
    __setGenieTurnLockForTests(null);
    clearGenieTurns();
  });

  it('moved the grammar and the cache key verbatim; the route re-exports the same bindings', () => {
    expect(deepLink.GENIE_CONVERSATION_ID_RE).toBe(GENIE_CONVERSATION_ID_RE);
    expect(deepLink.isGenieConversationId).toBe(isGenieConversationId);
    expect(deepLink.genieConversationQueryKey).toBe(genieConversationQueryKey);
    expect(isGenieConversationId(HEX_ID)).toBe(true);
    expect(isGenieConversationId('01234567-89ab-cdef-0123-456789abcdef')).toBe(true);
    expect(isGenieConversationId('conv-1')).toBe(false);
    expect(genieConversationQueryKey(HEX_ID)).toEqual(['mip', 'genie', 'session', HEX_ID]);
  });

  it('delivers a switch synchronously to every subscriber until it unsubscribes', () => {
    const heard: string[] = [];
    const stop = subscribeGenieConversationSwitch(({ conversationId, surface }) => heard.push(`${surface}:${conversationId}`));
    emitGenieConversationSwitch({ conversationId: HEX_ID, surface: 'panel' });
    expect(heard).toEqual([`panel:${HEX_ID}`]);
    stop();
    emitGenieConversationSwitch({ conversationId: OTHER_ID, surface: 'route' });
    expect(heard).toEqual([`panel:${HEX_ID}`]);
  });

  it('primes an entry with the thread as it stands', () => {
    const client = new QueryClient();
    const thread: GenieTurn[] = [{ question: 'Earlier', response: answer('Earlier', HEX_ID) }];
    setGenieTurns(thread);
    primeGenieLinkCache(client, HEX_ID);
    expect(client.getQueryData(genieConversationQueryKey(HEX_ID))).toEqual({ conversationId: HEX_ID, turns: thread });
  });

  it('keeps an existing entry current after a settled turn, with that turn, and never creates one', async () => {
    const client = new QueryClient();
    const fetches = vi.spyOn(client, 'fetchQuery');
    setGenieTurns([{ question: 'Earlier', response: answer('Earlier', HEX_ID) }]);
    primeGenieLinkCache(client, HEX_ID);
    ensureGenieLinkCacheSync(client);
    ensureGenieLinkCacheSync(client);

    await settleTurnIn(HEX_ID, 'Follow-up');
    const entry = client.getQueryData<{ turns: GenieTurn[] }>(genieConversationQueryKey(HEX_ID));
    expect(entry?.turns.map((turn) => turn.question)).toEqual(['Earlier', 'Follow-up']);

    await settleTurnIn(OTHER_ID, 'Elsewhere');
    expect(client.getQueryData(genieConversationQueryKey(OTHER_ID))).toBeUndefined();
    expect(fetches).not.toHaveBeenCalled();
    expect(client.getQueryCache().findAll().map((query) => query.queryKey)).toEqual([genieConversationQueryKey(HEX_ID)]);
  });

  it('follows the client it was last given (a new provider re-subscribes once)', async () => {
    const first = new QueryClient();
    const second = new QueryClient();
    setGenieTurns([]);
    primeGenieLinkCache(first, HEX_ID);
    primeGenieLinkCache(second, HEX_ID);
    ensureGenieLinkCacheSync(first);
    ensureGenieLinkCacheSync(second);

    await settleTurnIn(HEX_ID, 'Only once');
    expect(first.getQueryData<{ turns: GenieTurn[] }>(genieConversationQueryKey(HEX_ID))?.turns).toEqual([]);
    expect(second.getQueryData<{ turns: GenieTurn[] }>(genieConversationQueryKey(HEX_ID))?.turns.map((t) => t.question)).toEqual(['Only once']);
  });
});
