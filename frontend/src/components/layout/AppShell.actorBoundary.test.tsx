// @vitest-environment happy-dom

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ACTOR_CACHE_KEY_STORAGE_KEY } from '../../lib/actorScopedBrowserState';
import { GENIE_IN_FLIGHT_TURN_KEY } from '../../lib/genieConversation';
import { GENIE_CONVERSATION_TURNS_KEY } from '../../lib/genieConversationStore';
import { PINNED_INSIGHTS_KEY } from '../../lib/pinnedInsights';
import { installLocalStorage } from '../../test/installLocalStorage';
import { AppShell } from './AppShell';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * Genie-turn residual #3 (the actor boundary across a reload), proven on the
 * REAL AppShell. The shell compared each /api/health `actor_cache_key` with
 * the previous one held in a ref, and treated the FIRST key after a reload as
 * no change: an actor switch across a reload (a shared booth machine, a
 * re-login in the same tab) kept the previous actor's Genie transcript,
 * pinned insights, in-flight turn record and last borrower. The last key is
 * now kept in sessionStorage (it is already opaque and actor-scoped), and a
 * first key that differs from the stored one is an actor change.
 *
 * The health probe is the only answered request; everything else 404s.
 */

const PREVIOUS_ACTOR_STATE = {
  local: {
    'mip.lastBorrowerId': 'B-0OXOBYLW8MNCK',
    [PINNED_INSIGHTS_KEY]: JSON.stringify([{ id: 'pin-1', title: 'Previous actor pin' }]),
  },
  session: {
    [GENIE_CONVERSATION_TURNS_KEY]: JSON.stringify({ v: 1, turns: [{ question: 'previous actor question' }] }),
    [GENIE_IN_FLIGHT_TURN_KEY]: JSON.stringify({ v: 1, phase: 'polling' }),
  },
};

function seedPreviousActor(storedKey: string | null): void {
  if (storedKey !== null) window.sessionStorage.setItem(ACTOR_CACHE_KEY_STORAGE_KEY, storedKey);
  for (const [key, value] of Object.entries(PREVIOUS_ACTOR_STATE.local)) window.localStorage.setItem(key, value);
  for (const [key, value] of Object.entries(PREVIOUS_ACTOR_STATE.session)) window.sessionStorage.setItem(key, value);
}

/** The previous actor's keys still holding something (cleared pins persist as `[]`). */
function survivingState(): string[] {
  const holds = (value: string | null) => value !== null && value !== '[]';
  return [
    ...Object.keys(PREVIOUS_ACTOR_STATE.local).filter((key) => holds(window.localStorage.getItem(key))),
    ...Object.keys(PREVIOUS_ACTOR_STATE.session).filter((key) => holds(window.sessionStorage.getItem(key))),
  ];
}

describe('AppShell actor boundary across a reload (Genie residual #3)', () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;
  let actorKey: string | null;

  beforeEach(() => {
    installLocalStorage();
    window.sessionStorage.clear();
    actorKey = 'actor_b';
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      if (new URL(url, 'http://localhost').pathname === '/api/v1/health') {
        return new Response(JSON.stringify({
          status: 'ok',
          mode: 'live',
          dependencies: { warehouse: 'up', lakebase: 'up', genie: 'up' },
          circuit_breakers: {},
          actor_cache_key: actorKey,
        }), { status: 200, headers: { 'content-type': 'application/json' } });
      }
      return new Response('{"detail":"not found"}', { status: 404 });
    }));
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    queryClient.clear();
    window.sessionStorage.clear();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  /** Mount the shell (a reload) and wait for its first health probe to land. */
  async function reload(): Promise<void> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/']}>
            <AppShell>
              <h1>Home</h1>
            </AppShell>
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    for (let attempt = 0; attempt < 50; attempt += 1) {
      if (window.sessionStorage.getItem(ACTOR_CACHE_KEY_STORAGE_KEY) === actorKey) break;
      await act(async () => {
        await new Promise((resolve) => window.setTimeout(resolve, 10));
      });
    }
  }

  it('a reload that brings a DIFFERENT actor key clears the transcript, pins, in-flight record and last borrower', async () => {
    seedPreviousActor('actor_a');
    await reload();
    expect(window.sessionStorage.getItem(ACTOR_CACHE_KEY_STORAGE_KEY), 'the new actor key is remembered').toBe('actor_b');
    expect(survivingState()).toEqual([]);
  });

  it('a reload by the SAME actor keeps every piece of that state', async () => {
    actorKey = 'actor_a';
    seedPreviousActor('actor_a');
    await reload();
    expect(window.sessionStorage.getItem(ACTOR_CACHE_KEY_STORAGE_KEY)).toBe('actor_a');
    expect(survivingState()).toEqual([
      ...Object.keys(PREVIOUS_ACTOR_STATE.local),
      ...Object.keys(PREVIOUS_ACTOR_STATE.session),
    ]);
  });

  it('a first visit in the tab (no stored key) is not an actor change', async () => {
    seedPreviousActor(null);
    await reload();
    expect(window.sessionStorage.getItem(ACTOR_CACHE_KEY_STORAGE_KEY)).toBe('actor_b');
    expect(survivingState()).toHaveLength(4);
  });
});
