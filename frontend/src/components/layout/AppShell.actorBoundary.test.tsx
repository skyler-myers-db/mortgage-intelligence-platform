// @vitest-environment happy-dom

import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ACTOR_CACHE_KEY_STORAGE_KEY } from '../../lib/actorScopedBrowserState';
import { api } from '../../lib/api';
import { GENIE_IN_FLIGHT_TURN_KEY } from '../../lib/genieConversation';
import { GENIE_CONVERSATION_TURNS_KEY } from '../../lib/genieConversationStore';
import { PINNED_INSIGHTS_KEY } from '../../lib/pinnedInsights';
import { _resetSessionStatusForTests } from '../../lib/sessionStatus';
import { installLocalStorage } from '../../test/installLocalStorage';
import { useApp } from '../AppContext';
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

/**
 * The identity boundary (genie-02 item 1, Genie residual #3's unreachable-probe
 * half; bundle-04 item 4), proven on the REAL AppShell with a fetch stub and
 * fake timers. `api.health` renders every failed probe as the same
 * `unreachable` snapshot, and the shell used to read its missing
 * `actor_cache_key` as "the actor changed to nobody": a 502 or a dropped
 * connection wiped the transcript, pins, the in-flight record and the last
 * borrower. Only a TRUSTED observation (lib/healthTrust) may move the actor now.
 */
type HealthAnswer = () => Response | Promise<Response>;

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

const okFor = (key: string): HealthAnswer => () => jsonResponse(200, {
  status: 'ok',
  mode: 'live',
  dependencies: { warehouse: 'up', lakebase: 'up', genie: 'up' },
  circuit_breakers: {},
  actor_cache_key: key,
});
const anonymous: HealthAnswer = () => jsonResponse(200, { status: 'ok', mode: 'live' });
const badGateway: HealthAnswer = () => jsonResponse(502, { detail: 'Bad gateway' });
const dropped: HealthAnswer = () => {
  throw new TypeError('Failed to fetch');
};
const unauthorized: HealthAnswer = () => jsonResponse(401, {});
const forbidden: HealthAnswer = () => jsonResponse(403, { detail: 'forbidden' });

const LAST_BORROWER = 'B-0OXOBYLW8MNCK';

/** Sets the in-memory last borrower once, and shows what the shell holds. */
function LastBorrowerProbe() {
  const { lastBorrowerId, setLastBorrowerId } = useApp();
  useEffect(() => {
    setLastBorrowerId(LAST_BORROWER);
  }, [setLastBorrowerId]);
  return <output data-last-borrower="">{lastBorrowerId ?? ''}</output>;
}

describe('AppShell identity boundary: only a trusted probe moves the actor', () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;
  let healthAnswer: HealthAnswer;
  let healthCalls: number;

  beforeEach(() => {
    vi.useFakeTimers();
    installLocalStorage();
    window.sessionStorage.clear();
    _resetSessionStatusForTests();
    healthCalls = 0;
    healthAnswer = okFor('actor_a');
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      if (new URL(url, 'http://localhost').pathname === '/api/v1/health') {
        healthCalls += 1;
        return healthAnswer();
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
    _resetSessionStatusForTests();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  const lastBorrower = () => container.querySelector('[data-last-borrower]')?.textContent ?? null;
  const everything = () => [
    ...Object.keys(PREVIOUS_ACTOR_STATE.local),
    ...Object.keys(PREVIOUS_ACTOR_STATE.session),
  ];

  /** Mount the shell (a reload) and let its first probe land. */
  async function mountShell(): Promise<void> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/']}>
            <AppShell>
              <LastBorrowerProbe />
            </AppShell>
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
  }

  /** Answer the NEXT poll with `answer`, advancing the clock until it runs. */
  async function nextProbe(answer: HealthAnswer): Promise<void> {
    healthAnswer = answer;
    const before = healthCalls;
    for (let step = 0; step < 20 && healthCalls === before; step += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
    }
    expect(healthCalls, 'a probe ran').toBeGreaterThan(before);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
  }

  async function midSession(): Promise<void> {
    seedPreviousActor('actor_a');
    await mountShell();
    expect(window.sessionStorage.getItem(ACTOR_CACHE_KEY_STORAGE_KEY)).toBe('actor_a');
    expect(survivingState()).toEqual(everything());
    expect(lastBorrower()).toBe(LAST_BORROWER);
  }

  it('(i) trusted a, then a 502 and a dropped connection, then a: keeps everything', async () => {
    await midSession();
    await nextProbe(badGateway);
    expect(survivingState(), 'a 502 is not an actor change').toEqual(everything());
    expect(lastBorrower()).toBe(LAST_BORROWER);
    await nextProbe(dropped);
    expect(survivingState(), 'a dropped connection is not an actor change').toEqual(everything());
    expect(lastBorrower()).toBe(LAST_BORROWER);
    await nextProbe(okFor('actor_a'));
    expect(survivingState()).toEqual(everything());
    expect(lastBorrower()).toBe(LAST_BORROWER);
    expect(window.sessionStorage.getItem(ACTOR_CACHE_KEY_STORAGE_KEY)).toBe('actor_a');
  });

  it('(ii) a, then a reachable anonymous {status, mode} body: clears', async () => {
    await midSession();
    await nextProbe(anonymous);
    expect(survivingState()).toEqual([]);
    expect(lastBorrower()).toBe('');
  });

  it('(iii) a, then b: clears', async () => {
    await midSession();
    await nextProbe(okFor('actor_b'));
    expect(survivingState()).toEqual([]);
    expect(lastBorrower()).toBe('');
    expect(window.sessionStorage.getItem(ACTOR_CACHE_KEY_STORAGE_KEY)).toBe('actor_b');
  });

  it('(iv) a reload with stored a whose first probes fail, then a: keeps, and nothing clears in between', async () => {
    seedPreviousActor('actor_a');
    healthAnswer = badGateway;
    await mountShell();
    expect(healthCalls).toBe(1);
    expect(survivingState(), 'first probe 502').toEqual(everything());
    await nextProbe(dropped);
    expect(survivingState(), 'second probe dropped').toEqual(everything());
    await nextProbe(badGateway);
    expect(survivingState(), 'third probe 502').toEqual(everything());
    expect(window.sessionStorage.getItem(ACTOR_CACHE_KEY_STORAGE_KEY)).toBe('actor_a');
    await nextProbe(okFor('actor_a'));
    expect(survivingState()).toEqual(everything());
    expect(lastBorrower()).toBe(LAST_BORROWER);
  });

  it('(v) a reload with stored a, then b: clears', async () => {
    seedPreviousActor('actor_a');
    healthAnswer = badGateway;
    await mountShell();
    expect(survivingState()).toEqual(everything());
    await nextProbe(okFor('actor_b'));
    expect(survivingState()).toEqual([]);
    expect(window.sessionStorage.getItem(ACTOR_CACHE_KEY_STORAGE_KEY)).toBe('actor_b');
  });

  it('(vi) a, then a 401: clears', async () => {
    await midSession();
    await nextProbe(unauthorized);
    expect(survivingState()).toEqual([]);
    expect(lastBorrower()).toBe('');
    expect(window.sessionStorage.getItem(ACTOR_CACHE_KEY_STORAGE_KEY)).toBeNull();
  });

  it('(vii) a, then a 403: clears', async () => {
    await midSession();
    await nextProbe(forbidden);
    expect(survivingState()).toEqual([]);
    expect(lastBorrower()).toBe('');
  });

  it('(viii) a custom fetcher that throws after a: keeps', async () => {
    const health = vi.spyOn(api, 'health');
    health.mockImplementationOnce(async () => ({ status: 'ok', mode: 'live', actor_cache_key: 'actor_a' }));
    health.mockImplementation(async () => {
      throw new Error('custom fetcher failed');
    });
    await midSession();
    expect(health).toHaveBeenCalledTimes(1);
    for (let step = 0; step < 20 && health.mock.calls.length < 3; step += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
    }
    expect(health.mock.calls.length, 'two thrown probes ran').toBeGreaterThanOrEqual(3);
    expect(survivingState()).toEqual(everything());
    expect(lastBorrower()).toBe(LAST_BORROWER);
  });
});
