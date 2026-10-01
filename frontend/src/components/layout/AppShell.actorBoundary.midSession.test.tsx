// @vitest-environment happy-dom

import { act, StrictMode, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { createMemoryRouter, Outlet, RouterProvider } from 'react-router';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { UNSAVED_DIALOG_TITLE } from '../feedback/UnsavedChangesDialog';
import { unsavedWorkMessage, useUnsavedGuard } from '../../hooks/useUnsavedGuard';
import {
  ACTOR_SCOPE_REGISTRY,
  NOBODY,
  _resetActorScopeForTests,
  _setResetDocumentForTests,
  actorResetHeld,
  actorScopeStatus,
} from '../../lib/actorScope';
import { PINNED_INSIGHTS_KEY } from '../../lib/pinnedInsights';
import { sessionQueryOptions } from '../../lib/sessionQuery';
import { _resetSessionStatusForTests } from '../../lib/sessionStatus';
import { clearToasts, getToasts } from '../../lib/toast';
import { ACTOR_A, ACTOR_B } from '../../test/actorKeys';
import { installLocalStorage } from '../../test/installLocalStorage';
import { useApp } from '../AppContext';
import { PinnedInsights } from '../mortgage/PinnedInsights';
import { AppShell } from './AppShell';
import { ACTOR_RESET_NOTICE } from './useActorResetNotice';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/** Out of this suite for the reason AppShell.actorBoundary.test.tsx gives. */
vi.mock('./GenieDock', () => ({ GenieDock: () => null }));

/**
 * D-identity-review-a3 on the REAL AppShell, under a data router
 * (createMemoryRouter + RouterProvider, never MemoryRouter: the unsaved
 * guard's blocker only exists under a data router), with fake timers and a
 * fetch stub. The record's cases (ix) to (xiv); (xv) is in the nobody suite.
 *
 * A proven mid-session actor change resets the document: the stub stands in
 * for window.location.replace('/'). Every request the page sends is
 * recorded, so "nothing but health or session" is checked at the fetch layer,
 * where an audited read (VIEW_LEADS) would leave.
 */

const { STAMPS } = ACTOR_SCOPE_REGISTRY;
const NOTICE = 'mip.actorResetNotice';
const RESET_AT = 'mip.actorResetAt';
const PROBE_KEY = ['probe', 'lead-queue'] as const;
const A_PIN = JSON.stringify([{ id: 'pin-a', question: 'Which states lead the refi screen?', summary: 'Illinois.', source: null, pinnedAt: '2026-10-01T09:00:00Z' }]);

const A_STATE = {
  local: { [PINNED_INSIGHTS_KEY]: A_PIN, 'mip.genie.conversationId': 'conv-of-a' },
  session: { 'mip-genie-conversation-v1': '[]', 'mip.queueContext': '{"epoch":"e"}' },
};

function seedA(): void {
  window.localStorage.setItem(STAMPS.local, ACTOR_A);
  window.sessionStorage.setItem(STAMPS.session, ACTOR_A);
  for (const [key, value] of Object.entries(A_STATE.local)) window.localStorage.setItem(key, value);
  for (const [key, value] of Object.entries(A_STATE.session)) window.sessionStorage.setItem(key, value);
}

const localLeft = () => Object.keys(A_STATE.local).filter((key) => window.localStorage.getItem(key) !== null);
const sessionLeft = () => Object.keys(A_STATE.session).filter((key) => window.sessionStorage.getItem(key) !== null);
const stamps = () => [window.localStorage.getItem(STAMPS.local), window.sessionStorage.getItem(STAMPS.session)];

type Answer = () => Response;
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const okFor = (key: string): Answer => () =>
  json(200, { status: 'ok', mode: 'live', dependencies: { warehouse: 'up', lakebase: 'up', genie: 'up' }, circuit_breakers: {}, actor_cache_key: key });
const badGateway: Answer = () => json(502, { detail: 'Bad gateway' });

const HEALTH_OR_SESSION = /^\/api(\/v1)?\/(health|session)$/;
/** The open Console's own read (its Recent activity feed passes `enabled`). */
const MY_EVENTS = '/api/v1/audit/my-events';

/** A routed page with a live read, as the Lead Queue has: it re-renders with
 *  the shell's actor state, and its query passes `enabled` (as real routes do). */
function ProbeRoute() {
  useApp();
  const read = useQuery({
    queryKey: PROBE_KEY,
    queryFn: async ({ signal }) => (await fetch('/api/v1/leads?probe=1', { signal })).json() as Promise<unknown>,
    enabled: true,
    retry: false,
  });
  return <output data-probe="">{read.status}</output>;
}

/** A page with unsaved work (Portfolio Builder's typed budget). */
function DirtyRoute() {
  useUnsavedGuard(true, 'Your campaign setup is not saved.');
  return <h1>Portfolio Builder</h1>;
}

function setHidden(hidden: boolean): void {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => (hidden ? 'hidden' : 'visible') });
}

describe('AppShell: a proven mid-session actor change (D-identity-review-a3)', () => {
  // The notice is raised by the shell's lazy Toaster: load its chunk once,
  // so the shell's import of it resolves in microtasks under fake timers.
  beforeAll(async () => {
    await import('../feedback/Toaster');
    // The lazy Console, for the Console-open hold case (xiii-b).
    await import('./Console');
  });

  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;
  let router: ReturnType<typeof createMemoryRouter>;
  let healthAnswer: Answer;
  let healthCalls: number;
  let requests: string[];

  beforeEach(() => {
    vi.useFakeTimers();
    _resetActorScopeForTests({ status: 'pending', owner: NOBODY });
    installLocalStorage();
    window.sessionStorage.clear();
    _resetSessionStatusForTests();
    setHidden(false);
    healthCalls = 0;
    requests = [];
    healthAnswer = okFor(ACTOR_A);
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const raw = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      const url = new URL(raw, 'http://localhost');
      requests.push(url.pathname);
      if (url.pathname === '/api/v1/health') {
        healthCalls += 1;
        return healthAnswer();
      }
      if (url.pathname === '/api/v1/leads') return json(200, { items: [] });
      if (url.pathname === MY_EVENTS) return json(200, { items: [], next_cursor: null });
      return json(404, { detail: 'not found' });
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
    act(() => clearToasts());
    _resetSessionStatusForTests();
    setHidden(false);
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    _resetActorScopeForTests({ status: 'open', owner: NOBODY });
  });

  /** Requests other than health and session sent so far. */
  const otherRequests = () => requests.filter((path) => !HEALTH_OR_SESSION.test(path));

  async function advance(ms: number): Promise<void> {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  }

  async function mountAt(path: string, { strict = false }: { strict?: boolean } = {}): Promise<void> {
    router = createMemoryRouter(
      [
        {
          path: '/',
          element: (
            <AppShell>
              <Outlet />
            </AppShell>
          ),
          children: [
            { index: true, element: <><h1>Home</h1><PinnedInsights /></> },
            { path: 'lead-queue', element: <ProbeRoute /> },
            { path: 'portfolio-builder', element: <DirtyRoute /> },
          ],
        },
      ],
      { initialEntries: [path] },
    );
    const tree: ReactNode = (
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    );
    await act(async () => {
      root.render(strict ? <StrictMode>{tree}</StrictMode> : tree);
    });
    await advance(0);
  }

  /** Answer the NEXT poll with `answer`, advancing the clock until it runs. */
  async function nextProbe(answer: Answer): Promise<void> {
    healthAnswer = answer;
    const before = healthCalls;
    for (let step = 0; step < 20 && healthCalls === before; step += 1) await advance(1000);
    expect(healthCalls, 'a probe ran').toBeGreaterThan(before);
    await advance(0);
  }

  /** A's tab, open, at `path`, with A's private data on both areas. */
  async function midSessionAt(path: string): Promise<void> {
    seedA();
    await mountAt(path);
    expect(actorScopeStatus()).toBe('open');
    expect(stamps()).toEqual([ACTOR_A, ACTOR_A]);
    if (path === '/lead-queue') {
      for (let step = 0; step < 20 && container.querySelector('[data-probe]')?.textContent !== 'success'; step += 1) await advance(10);
      expect(container.querySelector('[data-probe]')?.textContent, 'the routed read loaded').toBe('success');
    }
  }

  /** The other-request count at the moment the next probe is answered with `key`. */
  function answerWith(key: string, onAnswer: () => void = () => undefined): { atObservation: () => number } {
    let seen = -1;
    healthAnswer = () => {
      seen = otherRequests().length;
      onAnswer();
      return okFor(key)();
    };
    return { atObservation: () => seen };
  }

  it('(ix) at /lead-queue: one reset, nothing but health or session sent between the observation and it', async () => {
    await midSessionAt('/lead-queue');
    let atReset = -1;
    const resetDocument = vi.fn(() => {
      atReset = otherRequests().length;
    });
    _setResetDocumentForTests(resetDocument);
    const observation = answerWith(ACTOR_B);
    await nextProbe(healthAnswer);
    expect(resetDocument).toHaveBeenCalledOnce();
    expect(observation.atObservation()).toBeGreaterThanOrEqual(0);
    expect(atReset, 'no other request between the observation and the reset').toBe(observation.atObservation());
    expect([...localLeft(), ...sessionLeft()], "A's private keys are removed").toEqual([]);
    expect(stamps()).toEqual([ACTOR_B, ACTOR_B]);
    expect(window.sessionStorage.getItem(NOTICE)).toBe('1');
    expect(actorScopeStatus()).toBe('closed');
  });

  it('(x) a FIRST observation of B over stamps of A clears, with no reset, notice flag or toast', async () => {
    seedA();
    const resetDocument = vi.fn();
    _setResetDocumentForTests(resetDocument);
    healthAnswer = okFor(ACTOR_B);
    await mountAt('/');
    expect(actorScopeStatus()).toBe('open');
    expect([...localLeft(), ...sessionLeft()]).toEqual([]);
    expect(stamps()).toEqual([ACTOR_B, ACTOR_B]);
    expect(resetDocument).not.toHaveBeenCalled();
    expect(window.sessionStorage.getItem(NOTICE)).toBeNull();
    expect(getToasts()).toEqual([]);
  });

  it('(xi) a cross-tab StorageEvent closes the gate at once and one health probe follows: A reopens, B resets', async () => {
    await midSessionAt('/');
    expect(container.querySelector('.pinned-insights'), "A's pin shows").not.toBeNull();
    await advance(2_000);
    const before = healthCalls;
    act(() => {
      window.dispatchEvent(new StorageEvent('storage', { key: STAMPS.local, oldValue: ACTOR_A, newValue: ACTOR_B, storageArea: window.localStorage }));
    });
    expect(actorScopeStatus(), 'closed synchronously').toBe('closed');
    expect(container.querySelector('.pinned-insights'), 'the pin hides at once').toBeNull();
    await advance(0);
    expect(healthCalls, 'one recheck probe within one timer tick').toBe(before + 1);
    expect(actorScopeStatus(), 'A answered: reopened').toBe('open');
    expect([...localLeft(), ...sessionLeft()], 'nothing removed: the event was synthetic').toEqual([
      ...Object.keys(A_STATE.local),
      ...Object.keys(A_STATE.session),
    ]);
    expect(container.querySelector('.pinned-insights')).not.toBeNull();

    // Another tab really did resolve B: the local stamp is B, then the event.
    const resetDocument = vi.fn();
    _setResetDocumentForTests(resetDocument);
    window.localStorage.setItem(STAMPS.local, ACTOR_B);
    healthAnswer = okFor(ACTOR_B);
    await advance(2_000);
    act(() => {
      window.dispatchEvent(new StorageEvent('storage', { key: STAMPS.local, oldValue: ACTOR_A, newValue: ACTOR_B, storageArea: window.localStorage }));
    });
    await advance(0);
    expect(resetDocument).toHaveBeenCalledOnce();
    expect(localLeft(), 'the local area is untouched: already stamped B').toEqual(Object.keys(A_STATE.local));
    expect(sessionLeft(), "this tab's session area is cleared").toEqual([]);
    expect(stamps()).toEqual([ACTOR_B, ACTOR_B]);
  });

  it('(xii) a dirty page: the reset drops the unsaved work, detaches beforeunload and no "Leave without saving?" holds it', async () => {
    await midSessionAt('/portfolio-builder');
    expect(unsavedWorkMessage()).toBe('Your campaign setup is not saved.');
    const removed = vi.spyOn(window, 'removeEventListener');
    // The stand-in for the reload also navigates in-app, so a guard that
    // still held the page would show its dialog.
    const resetDocument = vi.fn(() => {
      void router.navigate('/');
    });
    _setResetDocumentForTests(resetDocument);
    await nextProbe(okFor(ACTOR_B));
    await advance(0);
    expect(resetDocument).toHaveBeenCalledOnce();
    expect(unsavedWorkMessage()).toBeNull();
    expect(removed).toHaveBeenCalledWith('beforeunload', expect.any(Function));
    const unload = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented, 'no beforeunload listener stays attached').toBe(false);
    expect(document.body.textContent).not.toContain(UNSAVED_DIALOG_TITLE);
    expect(router.state.location.pathname).toBe('/');
  });

  it('(xiii) a reset 10 s after the last one is held: the routed page is swapped out before the clear and nothing else is sent until the 30 s boundary', async () => {
    await midSessionAt('/lead-queue');
    const resetDocument = vi.fn();
    _setResetDocumentForTests(resetDocument);
    // The last reset was 10 s before the observation: the boundary is 20 s on.
    const observation = answerWith(ACTOR_B, () => window.sessionStorage.setItem(RESET_AT, String(Date.now() - 10_000)));
    await nextProbe(healthAnswer);
    const atObservation = observation.atObservation();
    expect(atObservation).toBeGreaterThanOrEqual(0);

    expect(resetDocument, 'not now').not.toHaveBeenCalled();
    expect(actorResetHeld()).toBe(true);
    expect(actorScopeStatus()).toBe('closed');
    expect(container.querySelector('[data-route-fallback]'), 'the route placeholder holds the outlet').not.toBeNull();
    expect(container.querySelector('[data-probe]'), 'the routed reader is unmounted').toBeNull();
    expect(queryClient.getQueryCache().find({ queryKey: PROBE_KEY }), 'its query is gone and never re-created').toBeUndefined();
    expect(queryClient.getQueryCache().getAll().filter((query) => query.state.data !== undefined), 'the cache holds nothing').toEqual([]);

    for (let second = 0; second < 19; second += 1) {
      await advance(1_000);
      expect(otherRequests().slice(atObservation), `nothing but health or session by ${second + 1} s`).toEqual([]);
      expect(queryClient.getQueryCache().find({ queryKey: PROBE_KEY })).toBeUndefined();
    }
    expect(resetDocument).not.toHaveBeenCalled();
    await advance(1_000);
    expect(resetDocument, 'exactly one reset at the boundary').toHaveBeenCalledOnce();
    expect(otherRequests().slice(atObservation)).toEqual([]);
    await advance(60_000);
    expect(resetDocument).toHaveBeenCalledOnce();
  });

  it('(xiii-b) with the Console open, a held reset swaps the Console for its placeholder too: its feed is never re-read before the 30 s boundary', async () => {
    // A device preference (DEVICE_LOCAL), so it survives the reset.
    window.localStorage.setItem('mip.consoleOpen', 'true');
    await midSessionAt('/lead-queue');
    for (let step = 0; step < 20 && !requests.includes(MY_EVENTS); step += 1) await advance(10);
    expect(requests, 'the open Console read its feed').toContain(MY_EVENTS);
    expect(container.querySelector('#workspace-console[aria-hidden="false"] .tweaks__title'), 'the live Console is mounted').not.toBeNull();
    const resetDocument = vi.fn();
    _setResetDocumentForTests(resetDocument);
    // The last reset was 10 s before the observation: the boundary is 20 s on.
    const observation = answerWith(ACTOR_B, () => window.sessionStorage.setItem(RESET_AT, String(Date.now() - 10_000)));
    await nextProbe(healthAnswer);
    const atObservation = observation.atObservation();
    expect(atObservation).toBeGreaterThanOrEqual(0);
    expect(resetDocument, 'not now').not.toHaveBeenCalled();
    expect(actorResetHeld()).toBe(true);
    expect(actorScopeStatus()).toBe('closed');

    for (let second = 0; second < 19; second += 1) {
      await advance(1_000);
      expect(otherRequests().slice(atObservation), `nothing but health or session by ${second + 1} s`).toEqual([]);
      expect(container.querySelector('#workspace-console[aria-hidden="true"]'), `the Console placeholder holds its slot at ${second + 1} s`).not.toBeNull();
    }
    expect(container.querySelector('.tweaks__title'), 'the live Console is unmounted').toBeNull();
    expect(queryClient.getQueryCache().find({ queryKey: ['audit', 'my-events'] }), 'its feed query is gone and never re-created').toBeUndefined();
    expect(resetDocument).not.toHaveBeenCalled();
    await advance(1_000);
    expect(resetDocument, 'exactly one reset at the boundary').toHaveBeenCalledOnce();
    expect(otherRequests().slice(atObservation)).toEqual([]);
  });

  it('(xiv) a boot with the notice flag removes it and shows the info toast once, only after the gate opens (StrictMode)', async () => {
    seedA();
    window.sessionStorage.setItem(NOTICE, '1');
    healthAnswer = badGateway;
    await mountAt('/', { strict: true });
    expect(actorScopeStatus(), 'no trusted observation yet').toBe('pending');
    expect(getToasts()).toEqual([]);
    expect(window.sessionStorage.getItem(NOTICE), 'kept until the gate opens').toBe('1');
    await nextProbe(okFor(ACTOR_A));
    expect(actorScopeStatus()).toBe('open');
    expect(getToasts()).toEqual([expect.objectContaining({ tone: 'info', title: ACTOR_RESET_NOTICE, action: null })]);
    expect(window.sessionStorage.getItem(NOTICE)).toBeNull();
    await advance(30_000);
    expect(getToasts(), 'once, and an info toast is never auto-dismissed').toHaveLength(1);
  });

  it('(xiv) in a hidden document the notice waits for the first change to visible', async () => {
    seedA();
    window.sessionStorage.setItem(NOTICE, '1');
    setHidden(true);
    // The primed /api/session body opens the gate; a hidden tab does not probe.
    queryClient.setQueryData(sessionQueryOptions().queryKey, {
      can_access_admin: false,
      can_approve: false,
      actor_email: null,
      lender_name: 'Summit Mortgage',
      rum_enabled: false,
      actor_cache_key: ACTOR_A,
    });
    await mountAt('/');
    expect(actorScopeStatus()).toBe('open');
    expect(getToasts(), 'hidden: not yet').toEqual([]);
    expect(window.sessionStorage.getItem(NOTICE)).toBe('1');
    setHidden(false);
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await advance(0);
    expect(getToasts()).toEqual([expect.objectContaining({ tone: 'info', title: ACTOR_RESET_NOTICE })]);
    expect(window.sessionStorage.getItem(NOTICE)).toBeNull();
  });
});
