// @vitest-environment happy-dom

import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ACTOR_SCOPE_REGISTRY, NOBODY, _resetActorScopeForTests, actorScopeStatus } from '../../lib/actorScope';
import { api } from '../../lib/api';
import { GENIE_IN_FLIGHT_TURN_KEY } from '../../lib/genieConversation';
import { GENIE_CONVERSATION_TURNS_KEY, getGenieTurns } from '../../lib/genieConversationStore';
import { getGenieTurnSnapshot } from '../../lib/genieInFlightTurn';
import { PINNED_INSIGHTS_KEY } from '../../lib/pinnedInsights';
import { _resetSessionStatusForTests } from '../../lib/sessionStatus';
import { ACTOR_A, ACTOR_B } from '../../test/actorKeys';
import { installLocalStorage } from '../../test/installLocalStorage';
import { useApp } from '../AppContext';
import { AppShell } from './AppShell';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * The only removals of the seeded Genie in-flight record this suite may see
 * are the identity boundary's. Since wave 4b, GenieDock resumes a
 * reload-interrupted turn from the shell through a lazy import of the chat
 * chunk, and that resume discards an unresumable record such as the legacy
 * v:1 one seeded here. Mocking the chat module's two resume entries did not
 * keep it out. Every mount issues that import. While the first one's async
 * factory is still awaiting importOriginal(), Vitest routes each later import
 * from the same module to the ORIGINAL chat module (its self-import bypass
 * checks the importer's shared callstack). Drained, 10 of this file's 11
 * imports settled with the real resume. Whichever test is running when the
 * chat graph finishes loading loses its record: case viii on CI (runs
 * 36646256904 and 36664857512). So the dock is out of this suite. It is the
 * shell's only resume caller and has its own suite (GenieDock.resume.test.tsx).
 * The "clears" cases still need the boundary itself to remove the record.
 */
vi.mock('./GenieDock', () => ({ GenieDock: () => null }));

/**
 * Genie-turn residual #3 (the actor boundary across a reload), proven on the
 * REAL AppShell over the actor gate (lib/actorScope, D-identity-review-b).
 * Each storage area carries an owner stamp ('mip.actorOwner' in
 * localStorage, 'mip.actorCacheKey' in this tab's sessionStorage), and the
 * gate compares the first trusted key after a reload with them: another
 * actor's pins, transcript and in-flight record are removed; the same actor
 * keeps them; unowned data is never adopted. The legacy 'mip.lastBorrowerId'
 * (in-memory AppContext state since 8a30eafa) is removed at the first
 * resolution either way.
 *
 * The health probe is the only answered request; everything else 404s, so
 * the /api/session seed never applies here (the new-tab suite covers it).
 */

const { STAMPS } = ACTOR_SCOPE_REGISTRY;
const LEGACY_LAST_BORROWER = 'mip.lastBorrowerId';

const PREVIOUS_ACTOR_STATE = {
  local: {
    [LEGACY_LAST_BORROWER]: 'B-0OXOBYLW8MNCK',
    [PINNED_INSIGHTS_KEY]: JSON.stringify([{ id: 'pin-1', question: 'Previous actor pin', summary: 's' }]),
  },
  session: {
    [GENIE_CONVERSATION_TURNS_KEY]: JSON.stringify([
      { question: 'previous actor question', response: { answer: 'previous actor answer', source: 'genie' } },
    ]),
    [GENIE_IN_FLIGHT_TURN_KEY]: JSON.stringify({ v: 1, phase: 'polling' }),
  },
};

/** Seed the previous actor's data, stamped `owner` in BOTH areas (null: unstamped). */
function seedPreviousActor(owner: string | null): void {
  if (owner !== null) {
    window.localStorage.setItem(STAMPS.local, owner);
    window.sessionStorage.setItem(STAMPS.session, owner);
  }
  for (const [key, value] of Object.entries(PREVIOUS_ACTOR_STATE.local)) window.localStorage.setItem(key, value);
  for (const [key, value] of Object.entries(PREVIOUS_ACTOR_STATE.session)) window.sessionStorage.setItem(key, value);
}

/** The previous actor's keys still holding something. */
function survivingState(): string[] {
  return [
    ...Object.keys(PREVIOUS_ACTOR_STATE.local).filter((key) => window.localStorage.getItem(key) !== null),
    ...Object.keys(PREVIOUS_ACTOR_STATE.session).filter((key) => window.sessionStorage.getItem(key) !== null),
  ];
}

/** Everything the gate keeps for the same actor: all but the legacy key. */
const KEPT = [PINNED_INSIGHTS_KEY, GENIE_CONVERSATION_TURNS_KEY, GENIE_IN_FLIGHT_TURN_KEY];
const stamps = () => [window.localStorage.getItem(STAMPS.local), window.sessionStorage.getItem(STAMPS.session)];

/** A fresh document: the gate pending, both storages empty. */
function freshDocument(): void {
  _resetActorScopeForTests({ status: 'pending', owner: NOBODY });
  installLocalStorage();
  window.sessionStorage.clear();
}

describe('AppShell actor boundary across a reload (Genie residual #3)', () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;
  let actorKey: string | null;

  beforeEach(() => {
    freshDocument();
    actorKey = ACTOR_B;
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
    _resetActorScopeForTests({ status: 'open', owner: NOBODY });
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
      if (actorScopeStatus() !== 'pending' && window.sessionStorage.getItem(STAMPS.session) === actorKey) break;
      await act(async () => {
        await new Promise((resolve) => window.setTimeout(resolve, 10));
      });
    }
  }

  it('a reload that brings a DIFFERENT actor key clears the transcript, pins, in-flight record and last borrower', async () => {
    seedPreviousActor(ACTOR_A);
    await reload();
    expect(stamps(), 'the new actor owns both areas').toEqual([ACTOR_B, ACTOR_B]);
    expect(survivingState()).toEqual([]);
  });

  it('a reload by the SAME actor keeps every piece of that state (the legacy key is retired)', async () => {
    actorKey = ACTOR_A;
    seedPreviousActor(ACTOR_A);
    await reload();
    expect(stamps()).toEqual([ACTOR_A, ACTOR_A]);
    expect(survivingState()).toEqual(KEPT);
    expect(getGenieTurns().map((turn) => turn.question)).toEqual(['previous actor question']);
  });

  it('an unstamped tab (a first visit, or the upgrade) never adopts the data it finds', async () => {
    seedPreviousActor(null);
    await reload();
    expect(stamps()).toEqual([ACTOR_B, ACTOR_B]);
    expect(survivingState()).toEqual([]);
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
 *
 * A trusted nobody after a real owner (an anonymous body, a 401, a 403) is row
 * 5 of the gate: storage is kept byte for byte and the gate CLOSES, so the
 * stores read nothing; the W5a bridge clears the shell's in-memory state
 * (query cache, last borrower, the live Genie turn) on 'closed'.
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
    freshDocument();
    _resetSessionStatusForTests();
    healthCalls = 0;
    healthAnswer = okFor(ACTOR_A);
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
    _resetActorScopeForTests({ status: 'open', owner: NOBODY });
  });

  const lastBorrower = () => container.querySelector('[data-last-borrower]')?.textContent ?? null;
  const storageBytes = () =>
    JSON.stringify([
      [...ACTOR_SCOPE_REGISTRY.PRIVATE_LOCAL, STAMPS.local].map((key) => window.localStorage.getItem(key)),
      [...ACTOR_SCOPE_REGISTRY.PRIVATE_SESSION, STAMPS.session].map((key) => window.sessionStorage.getItem(key)),
    ]);

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
    seedPreviousActor(ACTOR_A);
    await mountShell();
    expect(stamps()).toEqual([ACTOR_A, ACTOR_A]);
    expect(actorScopeStatus()).toBe('open');
    expect(survivingState()).toEqual(KEPT);
    expect(getGenieTurns()).toHaveLength(1);
    expect(lastBorrower()).toBe(LAST_BORROWER);
  }

  /** A trusted nobody after A: storage kept byte for byte, the gate closed,
   *  the shell's in-memory actor state cleared by the W5a bridge. */
  async function closesOn(answer: HealthAnswer): Promise<void> {
    await midSession();
    const before = storageBytes();
    await nextProbe(answer);
    expect(storageBytes(), 'storage byte-identical').toBe(before);
    expect(stamps(), 'both stamps still name A').toEqual([ACTOR_A, ACTOR_A]);
    expect(actorScopeStatus()).toBe('closed');
    expect(lastBorrower(), 'the bridge cleared the last borrower').toBe('');
    expect(getGenieTurns(), 'the stores read nothing while closed').toEqual([]);
    expect(getGenieTurnSnapshot().inFlight).toBeNull();
  }

  it('(i) trusted a, then a 502 and a dropped connection, then a: keeps everything', async () => {
    await midSession();
    await nextProbe(badGateway);
    expect(survivingState(), 'a 502 is not an actor change').toEqual(KEPT);
    expect(lastBorrower()).toBe(LAST_BORROWER);
    await nextProbe(dropped);
    expect(survivingState(), 'a dropped connection is not an actor change').toEqual(KEPT);
    expect(lastBorrower()).toBe(LAST_BORROWER);
    await nextProbe(okFor(ACTOR_A));
    expect(survivingState()).toEqual(KEPT);
    expect(lastBorrower()).toBe(LAST_BORROWER);
    expect(stamps()).toEqual([ACTOR_A, ACTOR_A]);
    expect(actorScopeStatus()).toBe('open');
  });

  it('(ii) a, then a reachable anonymous {status, mode} body: closes, keeps storage, clears memory', async () => {
    await closesOn(anonymous);
  });

  it('(ii-b) a, then nobody, then a again: the same actor reopens and sees its transcript', async () => {
    await closesOn(anonymous);
    await nextProbe(okFor(ACTOR_A));
    expect(actorScopeStatus()).toBe('open');
    expect(getGenieTurns().map((turn) => turn.question)).toEqual(['previous actor question']);
  });

  it('(iii) a, then b: clears', async () => {
    await midSession();
    await nextProbe(okFor(ACTOR_B));
    expect(survivingState()).toEqual([]);
    expect(lastBorrower()).toBe('');
    expect(stamps()).toEqual([ACTOR_B, ACTOR_B]);
  });

  it('(iv) a reload with stored a whose first probes fail, then a: keeps, and nothing clears in between', async () => {
    seedPreviousActor(ACTOR_A);
    healthAnswer = badGateway;
    await mountShell();
    expect(healthCalls).toBe(1);
    expect(actorScopeStatus(), 'no trusted observation yet').toBe('pending');
    expect(getGenieTurns(), 'nothing renders from storage while pending').toEqual([]);
    expect(survivingState(), 'first probe 502').toHaveLength(4);
    await nextProbe(dropped);
    expect(survivingState(), 'second probe dropped').toHaveLength(4);
    await nextProbe(badGateway);
    expect(survivingState(), 'third probe 502').toHaveLength(4);
    expect(stamps()).toEqual([ACTOR_A, ACTOR_A]);
    await nextProbe(okFor(ACTOR_A));
    expect(survivingState()).toEqual(KEPT);
    expect(getGenieTurns()).toHaveLength(1);
    expect(lastBorrower()).toBe(LAST_BORROWER);
  });

  it('(v) a reload with stored a, then b: clears', async () => {
    seedPreviousActor(ACTOR_A);
    healthAnswer = badGateway;
    await mountShell();
    expect(survivingState()).toHaveLength(4);
    await nextProbe(okFor(ACTOR_B));
    expect(survivingState()).toEqual([]);
    expect(stamps()).toEqual([ACTOR_B, ACTOR_B]);
  });

  it('(vi) a, then a 401: closes, keeps storage, clears memory', async () => {
    await closesOn(unauthorized);
  });

  it('(vii) a, then a 403: closes, keeps storage, clears memory', async () => {
    await closesOn(forbidden);
  });

  it('(ix) a, then a reachable 200 {} that is not a real health body: keeps every key and the stamp', async () => {
    await midSession();
    await nextProbe(() => jsonResponse(200, {}));
    expect(survivingState()).toEqual(KEPT);
    expect(lastBorrower()).toBe(LAST_BORROWER);
    expect(stamps()).toEqual([ACTOR_A, ACTOR_A]);
    expect(actorScopeStatus()).toBe('open');
  });

  it('(viii) a custom fetcher that throws after a: keeps', async () => {
    const health = vi.spyOn(api, 'health');
    health.mockImplementationOnce(async () => ({ status: 'ok', mode: 'live', actor_cache_key: ACTOR_A }));
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
    expect(survivingState()).toEqual(KEPT);
    expect(lastBorrower()).toBe(LAST_BORROWER);
  });
});
