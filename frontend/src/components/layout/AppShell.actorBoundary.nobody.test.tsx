// @vitest-environment happy-dom

import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  ACTOR_SCOPE_REGISTRY,
  NOBODY,
  _resetActorScopeForTests,
  _setResetDocumentForTests,
  actorScopeStatus,
} from '../../lib/actorScope';
import { api } from '../../lib/api';
import type { GenieLiveProgress } from '../../lib/api';
import { GENIE_IN_FLIGHT_TURN_KEY } from '../../lib/genieConversation';
import { GENIE_CONVERSATION_TURNS_KEY, getGenieTurns } from '../../lib/genieConversationStore';
import {
  __resetGenieTurnStoreForTests,
  __setGenieTurnLockForTests,
  getGenieTurnSnapshot,
  resumeGenieTurnFromSession,
} from '../../lib/genieInFlightTurn';
import { PINNED_INSIGHTS_KEY } from '../../lib/pinnedInsights';
import { QUEUE_CONTEXT_STORAGE_KEY } from '../../lib/queueContext';
import { _resetSessionStatusForTests } from '../../lib/sessionStatus';
import { ACTOR_A, ACTOR_B } from '../../test/actorKeys';
import { installLocalStorage } from '../../test/installLocalStorage';
import { useApp } from '../AppContext';
import { PinnedInsights } from '../mortgage/PinnedInsights';
import { AppShell } from './AppShell';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/** Out of this suite for the reason AppShell.actorBoundary.test.tsx gives;
 *  the shell's resume is modelled by calling resumeGenieTurnFromSession(). */
vi.mock('./GenieDock', () => ({ GenieDock: () => null }));

/**
 * D-identity-review-a2 on the REAL AppShell (genie-02 / bundle-04 item 4,
 * 12.4 #8): a trusted NOBODY after a real owner (a 401, a 403, the reachable
 * anonymous {status, mode} body) closes the gate. Nothing is removed and
 * nothing is shown: the pin, the transcript, the live Genie turn and the last
 * borrower leave the screen, storage stays byte for byte. A same-page return
 * of the owner shows it all again and RESUMES the kept in-flight record (the
 * 'closed' event re-armed it); another actor is a proven change (xv) and the
 * document is reset. A 401 ends the session: no probe runs until a reload,
 * and the reload is a new document, whose first observation decides.
 *
 * The trusted-probe harness is copied from AppShell.actorBoundary.test.tsx
 * (test files are never imported).
 */

const { STAMPS } = ACTOR_SCOPE_REGISTRY;
const LAST_BORROWER = 'B-0OXOBYLW8MNCK';
const PIN_QUESTION = 'Which states lead the refi screen?';

/** A's private data in every area, stamped A; the record is polling and young. */
function seedActorA(): void {
  const startedAt = Date.now() - 1_000;
  const local: Record<string, string> = {
    [STAMPS.local]: ACTOR_A,
    [PINNED_INSIGHTS_KEY]: JSON.stringify([{ id: 'pin-a', question: PIN_QUESTION, summary: 'Illinois.', source: null, pinnedAt: '2026-10-01T09:00:00Z' }]),
    'mip.genie.conversationId': 'conv-of-a',
  };
  const session: Record<string, string> = {
    [STAMPS.session]: ACTOR_A,
    [GENIE_CONVERSATION_TURNS_KEY]: JSON.stringify([{ question: 'previous question of a', response: { answer: 'previous answer', source: 'genie' } }]),
    [GENIE_IN_FLIGHT_TURN_KEY]: JSON.stringify({
      v: 2,
      question: 'A live question',
      conversationId: 'conv-of-a',
      surface: 'panel',
      startedAt,
      deep: false,
      phase: 'polling',
      ids: { conversationId: 'conv-of-a', messageId: 'msg-of-a', progressToken: 'tok-of-a' },
      asyncComplete: false,
    }),
    [QUEUE_CONTEXT_STORAGE_KEY]: JSON.stringify({ epoch: 'epoch-a', search: '?state=IL', label: 'IL', ids: [LAST_BORROWER] }),
  };
  for (const [key, value] of Object.entries(local)) window.localStorage.setItem(key, value);
  for (const [key, value] of Object.entries(session)) window.sessionStorage.setItem(key, value);
}

const privateKeysLeft = () => [
  ...ACTOR_SCOPE_REGISTRY.PRIVATE_LOCAL.filter((key) => window.localStorage.getItem(key) !== null),
  ...ACTOR_SCOPE_REGISTRY.PRIVATE_SESSION.filter((key) => window.sessionStorage.getItem(key) !== null),
];
const SEEDED_PRIVATE = [
  PINNED_INSIGHTS_KEY,
  'mip.genie.conversationId',
  GENIE_CONVERSATION_TURNS_KEY,
  GENIE_IN_FLIGHT_TURN_KEY,
  QUEUE_CONTEXT_STORAGE_KEY,
];
const stamps = () => [window.localStorage.getItem(STAMPS.local), window.sessionStorage.getItem(STAMPS.session)];
const storageBytes = () =>
  JSON.stringify([
    [...ACTOR_SCOPE_REGISTRY.PRIVATE_LOCAL, STAMPS.local].map((key) => window.localStorage.getItem(key)),
    [...ACTOR_SCOPE_REGISTRY.PRIVATE_SESSION, STAMPS.session].map((key) => window.sessionStorage.getItem(key)),
  ]);

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
const unauthorized: HealthAnswer = () => jsonResponse(401, {});
const forbidden: HealthAnswer = () => jsonResponse(403, { detail: 'forbidden' });

/** Genie is still working: every progress poll answers "not done yet". */
const STILL_WORKING: GenieLiveProgress = {
  status: 'EXECUTING_QUERY',
  stage: 'query',
  stage_label: 'Running the governed query',
  terminal: false,
  failed: false,
  reasoning_trace: [],
  sql_preview: null,
  error_hint: null,
};

/** Sets the in-memory last borrower once, and shows what the shell holds. */
function LastBorrowerProbe() {
  const { lastBorrowerId, setLastBorrowerId } = useApp();
  useEffect(() => {
    setLastBorrowerId(LAST_BORROWER);
  }, [setLastBorrowerId]);
  return <output data-last-borrower="">{lastBorrowerId ?? ''}</output>;
}

describe('AppShell: a trusted nobody closes the gate on everything (D-identity-review-a2)', () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;
  let healthAnswer: HealthAnswer;
  let healthCalls: number;
  let progressPolls: ReturnType<typeof vi.spyOn>;

  /** A new document: the turn store first (its removals would otherwise
   *  queue on the pending gate and replay when it opens), then the gate. */
  function newDocument(owner: string): void {
    __resetGenieTurnStoreForTests();
    _resetSessionStatusForTests();
    _resetActorScopeForTests({ status: 'pending', owner });
    __setGenieTurnLockForTests(() => Promise.resolve({ kind: 'held', release: () => undefined }));
  }

  beforeEach(() => {
    vi.useFakeTimers();
    newDocument(NOBODY);
    installLocalStorage();
    window.sessionStorage.clear();
    progressPolls = vi.spyOn(api, 'genieProgress').mockResolvedValue(STILL_WORKING);
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
    __resetGenieTurnStoreForTests();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    _resetActorScopeForTests({ status: 'open', owner: NOBODY });
  });

  const lastBorrower = () => container.querySelector('[data-last-borrower]')?.textContent ?? null;
  const pinShown = () => container.querySelector('.pinned-insights')?.textContent?.includes(PIN_QUESTION) ?? false;

  async function advance(ms: number): Promise<void> {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  }

  /** Mount the shell (a load of the page); the shell's resume (GenieDock's
   *  job) is asked for at once, so it runs when the gate first opens. */
  async function mountShell(): Promise<void> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/']}>
            <AppShell>
              <PinnedInsights />
              <LastBorrowerProbe />
            </AppShell>
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    act(() => resumeGenieTurnFromSession());
    await advance(0);
  }

  /** Answer the NEXT poll with `answer`, advancing the clock until it runs. */
  async function nextProbe(answer: HealthAnswer): Promise<void> {
    healthAnswer = answer;
    const before = healthCalls;
    for (let step = 0; step < 20 && healthCalls === before; step += 1) await advance(1000);
    expect(healthCalls, 'a probe ran').toBeGreaterThan(before);
    await advance(0);
  }

  /** A's tab, open: everything shown, the kept turn resumed and polling. */
  async function openForA(): Promise<void> {
    seedActorA();
    await mountShell();
    expect(actorScopeStatus()).toBe('open');
    expect(stamps()).toEqual([ACTOR_A, ACTOR_A]);
    expect(pinShown(), "A's pin").toBe(true);
    expect(getGenieTurns().map((turn) => turn.question)).toEqual(['previous question of a']);
    expect(lastBorrower()).toBe(LAST_BORROWER);
    expect(getGenieTurnSnapshot().inFlight?.resumed, "A's turn resumed on the first open").toBe(true);
    expect(progressPolls, 'and polls').toHaveBeenCalled();
  }

  /** The nobody matrix's common assertions (the record's list). */
  async function closesOn(answer: HealthAnswer): Promise<void> {
    await openForA();
    const before = storageBytes();
    await nextProbe(answer);
    expect(storageBytes(), 'every stored key byte-identical').toBe(before);
    expect(stamps(), 'both stamps still name A').toEqual([ACTOR_A, ACTOR_A]);
    expect(actorScopeStatus()).toBe('closed');
    expect(pinShown(), 'no pin while closed').toBe(false);
    expect(getGenieTurns(), 'no transcript while closed').toEqual([]);
    expect(getGenieTurnSnapshot().inFlight, 'the live turn ended').toBeNull();
    expect(lastBorrower(), 'the shell forgot the last borrower').toBe('');
    expect(privateKeysLeft(), 'the record and the rest are kept').toEqual(SEEDED_PRIVATE);
  }

  for (const [label, answer] of [['a 403', forbidden], ['the reachable anonymous body', anonymous]] as const) {
    it(`${label}: closes; then A on the same page shows it all again, removes nothing, and the kept record resumes`, async () => {
      await closesOn(answer);
      const pollsWhileClosed = progressPolls.mock.calls.length;
      await advance(10_000);
      expect(progressPolls.mock.calls.length, 'nothing polls while closed').toBe(pollsWhileClosed);

      await nextProbe(okFor(ACTOR_A));
      expect(actorScopeStatus()).toBe('open');
      expect(pinShown()).toBe(true);
      expect(getGenieTurns().map((turn) => turn.question)).toEqual(['previous question of a']);
      expect(privateKeysLeft(), 'nothing removed').toEqual(SEEDED_PRIVATE);
      await advance(3_000);
      expect(progressPolls.mock.calls.length, 'the kept record resumed: a progress poll').toBeGreaterThan(pollsWhileClosed);
      expect(getGenieTurnSnapshot().inFlight?.resumed).toBe(true);
    });

    it(`${label}: closes; then B is a proven change (xv): one reset, every private key removed, both stamps B`, async () => {
      await closesOn(answer);
      const resetDocument = vi.fn();
      _setResetDocumentForTests(resetDocument);
      await nextProbe(okFor(ACTOR_B));
      expect(resetDocument).toHaveBeenCalledOnce();
      expect(privateKeysLeft()).toEqual([]);
      expect(stamps()).toEqual([ACTOR_B, ACTOR_B]);
      expect(actorScopeStatus()).toBe('closed');
    });
  }

  // D-identity-review-a3's suspend meets a2: another tab restamped the shared
  // local stamp (B signed in there), which suspends this tab's gate with
  // 'restamped' only; B's session is then gone, so the recheck answers
  // nobody. That answer must contain this tab exactly as a nobody from open
  // does, or A's Genie turn and the shell's actor state stay on screen.
  for (const [label, answer] of [['a 403', forbidden], ['the reachable anonymous body', anonymous], ['a 401', unauthorized]] as const) {
    it(`a cross-tab suspend whose recheck answers ${label}: the same containment as a nobody from open`, async () => {
      await openForA();
      await advance(2_000);
      queryClient.setQueryData(['routed', 'of-a'], { rows: ['a'] });
      window.localStorage.setItem(STAMPS.local, ACTOR_B);
      const before = storageBytes();
      const probes = healthCalls;
      healthAnswer = answer;
      act(() => {
        window.dispatchEvent(new StorageEvent('storage', { key: STAMPS.local, oldValue: ACTOR_A, newValue: ACTOR_B, storageArea: window.localStorage }));
      });
      await advance(0);
      expect(healthCalls, 'the recheck probe ran').toBe(probes + 1);
      expect(actorScopeStatus()).toBe('closed');
      expect(lastBorrower(), 'the shell forgot the last borrower').toBe('');
      expect(getGenieTurnSnapshot().inFlight, 'the live turn ended').toBeNull();
      expect(queryClient.getQueryData(['routed', 'of-a']), "the query cache dropped A's data").toBeUndefined();
      expect(pinShown(), 'no pin while closed').toBe(false);
      expect(storageBytes(), 'every stored key byte-identical').toBe(before);
    });
  }

  it('a 401: closes; no probe for 60 s; a reload as A keeps everything and resumes; a new document as B clears as a first observation', async () => {
    await closesOn(unauthorized);
    const probes = healthCalls;
    await advance(60_000);
    expect(healthCalls, 'the session ended: no probe until a reload').toBe(probes);

    // A reload: a new document over the same storage (both stamps still A).
    await act(async () => root.unmount());
    newDocument(ACTOR_A);
    progressPolls.mockClear();
    healthAnswer = okFor(ACTOR_A);
    root = createRoot(container);
    await mountShell();
    expect(actorScopeStatus()).toBe('open');
    expect(privateKeysLeft(), 'the same actor keeps everything').toEqual(SEEDED_PRIVATE);
    expect(pinShown()).toBe(true);
    expect(getGenieTurns().map((turn) => turn.question)).toEqual(['previous question of a']);
    await advance(3_000);
    expect(progressPolls, 'the kept record resumes').toHaveBeenCalled();

    // Another new document, whose first trusted observation is B.
    await act(async () => root.unmount());
    newDocument(ACTOR_A);
    const resetDocument = vi.fn();
    _setResetDocumentForTests(resetDocument);
    healthAnswer = okFor(ACTOR_B);
    root = createRoot(container);
    await mountShell();
    expect(actorScopeStatus()).toBe('open');
    expect(privateKeysLeft(), "a first observation clears A's data").toEqual([]);
    expect(stamps()).toEqual([ACTOR_B, ACTOR_B]);
    expect(resetDocument, 'a first observation never resets').not.toHaveBeenCalled();
    expect(window.sessionStorage.getItem('mip.actorResetNotice'), 'and leaves no notice').toBeNull();
  });
});
