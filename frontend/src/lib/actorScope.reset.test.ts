/**
 * @vitest-environment happy-dom
 *
 * D-identity-review-a3 at the module layer (genie-02 / bundle-04 item 4):
 * a PROVEN mid-session actor change resets the document instead of reopening
 * the gate in place, and another tab restamping the shared local stamp
 * suspends this one until a trusted probe says who the actor is (the W5a
 * integrator ruling). happy-dom, because the gate's cross-tab listener is a
 * real `window` 'storage' listener added when the module loads. The rendered
 * consequences (the routed page, the notice, the hold) are proven on the real
 * AppShell in components/layout/AppShell.actorBoundary.midSession.test.tsx.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { registerUnsavedWork, subscribeUnsavedWork, unsavedWorkMessage } from '../hooks/useUnsavedGuard';
import { ACTOR_A, ACTOR_B, ACTOR_C } from '../test/actorKeys';
import { installLocalStorage } from '../test/installLocalStorage';
import {
  ACTOR_SCOPE_REGISTRY,
  NOBODY,
  _resetActorScopeForTests,
  _setResetDocumentForTests,
  actorResetHeld,
  actorScopeStatus,
  documentOwnerKey,
  observeActor,
  readActorScoped,
  subscribeActorScope,
  takeActorResetNotice,
  updateActorScoped,
  writeActorScoped,
  type ActorScopeReason,
} from './actorScope';
import { subscribeHealthRecheck } from './healthRecheck';
import { PINNED_INSIGHTS_KEY } from './pinnedInsights';

const { STAMPS } = ACTOR_SCOPE_REGISTRY;
const NOTICE = 'mip.actorResetNotice';
const RESET_AT = 'mip.actorResetAt';
const T0 = Date.parse('2026-10-01T10:00:00Z');
const PINS = JSON.stringify([{ id: 'p1', question: 'q1', summary: 's1', source: null, pinnedAt: '2026-10-01' }]);

let events: ActorScopeReason[];
let rechecks: number;
let resetDocument: ReturnType<typeof vi.fn<() => void>>;
const cleanups: Array<() => void> = [];

const local = () => window.localStorage;
const session = () => window.sessionStorage;
const stamps = () => [local().getItem(STAMPS.local), session().getItem(STAMPS.session)];
const privateLeft = () => [
  ...ACTOR_SCOPE_REGISTRY.PRIVATE_LOCAL.filter((key) => local().getItem(key) !== null),
  ...ACTOR_SCOPE_REGISTRY.PRIVATE_SESSION.filter((key) => session().getItem(key) !== null),
];
/** Every private key and both stamps, as stored. */
const storageBytes = () =>
  JSON.stringify([
    [...ACTOR_SCOPE_REGISTRY.PRIVATE_LOCAL, STAMPS.local].map((key) => local().getItem(key)),
    [...ACTOR_SCOPE_REGISTRY.PRIVATE_SESSION, STAMPS.session].map((key) => session().getItem(key)),
  ]);

/** A document opened for A over A's private data in both areas. */
function openForA(): void {
  _resetActorScopeForTests({ status: 'pending', owner: ACTOR_A });
  local().setItem(PINNED_INSIGHTS_KEY, PINS);
  local().setItem('mip.genie.conversationId', 'conv-a');
  session().setItem('mip.genie.inFlightTurn', '{"v":2}');
  session().setItem('mip.queueContext', '{}');
  observeActor({ key: ACTOR_A });
  expect(actorScopeStatus()).toBe('open');
  resetDocument = vi.fn<() => void>();
  _setResetDocumentForTests(resetDocument);
  events.length = 0;
}

function storageEvent(init: StorageEventInit): void {
  window.dispatchEvent(new StorageEvent('storage', init));
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(T0);
  installLocalStorage();
  session().clear();
  events = [];
  rechecks = 0;
  cleanups.push(subscribeActorScope(({ reason }) => events.push(reason)));
  cleanups.push(subscribeHealthRecheck(() => {
    rechecks += 1;
  }));
});

afterEach(() => {
  while (cleanups.length > 0) cleanups.pop()?.();
  _resetActorScopeForTests({ status: 'open', owner: NOBODY });
  session().clear();
  vi.useRealTimers();
});

describe('a proven change resets the document (B1)', () => {
  it('runs in order: unsaved work is dropped before any removal, then the stamps, then one reset', () => {
    openForA();
    const unregister = registerUnsavedWork('page', 'Your campaign setup is not saved.');
    let keysWhenUnsavedCleared: string[] | null = null;
    cleanups.push(subscribeUnsavedWork(() => {
      if (unsavedWorkMessage() === null && keysWhenUnsavedCleared === null) keysWhenUnsavedCleared = privateLeft();
    }));
    observeActor({ key: ACTOR_B });
    unregister();
    expect(keysWhenUnsavedCleared, 'clearUnsavedWork ran before the removals').toHaveLength(4);
    expect(privateLeft()).toEqual([]);
    expect(stamps()).toEqual([ACTOR_B, ACTOR_B]);
    expect(events, "the immediate path emits 'restamped' only").toEqual(['restamped']);
    expect(actorResetHeld()).toBe(false);
    expect(resetDocument).toHaveBeenCalledOnce();
    expect(session().getItem(NOTICE)).toBe('1');
    expect(session().getItem(RESET_AT)).toBe(String(T0));
  });

  it('keeps an area already stamped k (or an alias of k), and removes the other', () => {
    openForA();
    local().setItem(STAMPS.local, ACTOR_C); // another tab already resolved C, an alias of B
    observeActor({ key: ACTOR_B, aliases: [ACTOR_C] });
    expect(local().getItem(PINNED_INSIGHTS_KEY), "the alias's local area is kept").toBe(PINS);
    expect(session().getItem('mip.genie.inFlightTurn'), "A's session area is removed").toBeNull();
    expect(stamps()).toEqual([ACTOR_B, ACTOR_B]);
  });

  it('resets even when the path is already "/" and nothing private is stored', () => {
    _resetActorScopeForTests({ status: 'pending', owner: NOBODY });
    observeActor({ key: ACTOR_A });
    resetDocument = vi.fn<() => void>();
    _setResetDocumentForTests(resetDocument);
    observeActor({ key: ACTOR_B });
    expect(resetDocument).toHaveBeenCalledOnce();
  });

  it.each([
    ['absent', null],
    ['not a number', 'soon'],
    ['zero', '0'],
    ['30 s old', String(T0 - 30_000)],
  ])('the loop guard reads %s as no recent reset: reset now', (_label, stored) => {
    openForA();
    if (stored !== null) session().setItem(RESET_AT, stored);
    observeActor({ key: ACTOR_B });
    expect(resetDocument).toHaveBeenCalledOnce();
    expect(actorResetHeld()).toBe(false);
  });

  it('holds a reset under 30 s since the last one: restamped then closed, one timer, one reset at the boundary', () => {
    openForA();
    session().setItem(RESET_AT, String(T0 - 10_000));
    const timers = vi.getTimerCount();
    observeActor({ key: ACTOR_B });
    expect(events).toEqual(['restamped', 'closed']);
    expect(actorResetHeld()).toBe(true);
    expect(resetDocument).not.toHaveBeenCalled();
    expect(vi.getTimerCount(), 'exactly one timer').toBe(timers + 1);
    expect(session().getItem(NOTICE), 'the notice waits for the reset').toBeNull();
    expect(privateLeft(), 'storage is already cleared').toEqual([]);

    observeActor({ key: ACTOR_C });
    observeActor({ key: ACTOR_B });
    expect(vi.getTimerCount(), 'never a second timer').toBe(timers + 1);

    vi.advanceTimersByTime(19_999);
    expect(resetDocument).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(resetDocument).toHaveBeenCalledOnce();
    expect(session().getItem(NOTICE)).toBe('1');
    expect(session().getItem(RESET_AT)).toBe(String(T0 + 20_000));
    vi.advanceTimersByTime(60_000);
    expect(resetDocument).toHaveBeenCalledOnce();
  });
});

describe('the B3 invariant: a reset document never reopens', () => {
  it('keeps the gate closed and the document owner A, whatever is observed next', () => {
    openForA();
    observeActor({ key: ACTOR_B });
    expect(documentOwnerKey()).toBe(ACTOR_A);
    for (const key of [ACTOR_B, ACTOR_A, null, ACTOR_C]) {
      observeActor({ key });
      expect(actorScopeStatus(), String(key)).toBe('closed');
      expect(documentOwnerKey(), String(key)).toBe(ACTOR_A);
    }
    // Nor does a cross-tab event or a read under a foreign stamp do anything.
    events.length = 0;
    storageEvent({ key: STAMPS.local, newValue: ACTOR_C, storageArea: window.localStorage });
    local().setItem(STAMPS.local, ACTOR_C);
    expect(readActorScoped('local', PINNED_INSIGHTS_KEY)).toBeNull();
    expect(events).toEqual([]);
    expect(rechecks).toBe(0);
  });

  it('holds the same way while a reset is held', () => {
    openForA();
    session().setItem(RESET_AT, String(T0 - 5_000));
    observeActor({ key: ACTOR_B });
    observeActor({ key: ACTOR_A });
    expect(actorScopeStatus()).toBe('closed');
    expect(documentOwnerKey()).toBe(ACTOR_A);
    expect(actorResetHeld()).toBe(true);
  });
});

describe('cross-tab: another tab restamps the shared local stamp (B5)', () => {
  it('suspends an open gate: closed at once, restamped only (no query-cache clear), one health recheck', () => {
    openForA();
    storageEvent({ key: STAMPS.local, oldValue: ACTOR_A, newValue: ACTOR_B, storageArea: window.localStorage });
    expect(actorScopeStatus()).toBe('closed');
    expect(events, "never 'closed' or 'cleared' on suspicion").toEqual(['restamped']);
    expect(rechecks).toBe(1);
    expect(privateLeft(), 'nothing is removed on suspicion').toHaveLength(4);
    expect(readActorScoped('local', PINNED_INSIGHTS_KEY), 'stores read nothing while suspended').toBeNull();
  });

  it('a clear() in another tab (key null) suspends too', () => {
    openForA();
    storageEvent({ key: null, storageArea: window.localStorage });
    expect(actorScopeStatus()).toBe('closed');
    expect(rechecks).toBe(1);
  });

  it('ignores other keys, the same owner, sessionStorage and a pending gate', () => {
    openForA();
    storageEvent({ key: PINNED_INSIGHTS_KEY, newValue: '[]', storageArea: window.localStorage });
    storageEvent({ key: STAMPS.local, newValue: ACTOR_A, storageArea: window.localStorage });
    storageEvent({ key: STAMPS.session, newValue: ACTOR_B, storageArea: window.sessionStorage });
    expect(actorScopeStatus()).toBe('open');
    expect(events).toEqual([]);
    _resetActorScopeForTests({ status: 'pending', owner: ACTOR_A });
    storageEvent({ key: STAMPS.local, newValue: ACTOR_B, storageArea: window.localStorage });
    expect(actorScopeStatus()).toBe('pending');
    expect(rechecks).toBe(0);
  });

  it('a trusted probe answering the owner reopens with nothing removed; another actor is a proven change', () => {
    openForA();
    storageEvent({ key: STAMPS.local, newValue: ACTOR_B, storageArea: window.localStorage });
    observeActor({ key: ACTOR_A });
    expect(actorScopeStatus()).toBe('open');
    expect(privateLeft()).toHaveLength(4);
    expect(resetDocument).not.toHaveBeenCalled();

    storageEvent({ key: STAMPS.local, newValue: ACTOR_B, storageArea: window.localStorage });
    observeActor({ key: ACTOR_B });
    expect(resetDocument).toHaveBeenCalledOnce();
  });

  it("a trusted nobody answering the suspend emits 'closed' (row 5's containment) with storage byte-identical", () => {
    openForA();
    local().setItem(STAMPS.local, ACTOR_B);
    storageEvent({ key: STAMPS.local, oldValue: ACTOR_A, newValue: ACTOR_B, storageArea: window.localStorage });
    expect(events).toEqual(['restamped']);
    const bytes = storageBytes();
    events.length = 0;

    observeActor({ key: null });
    expect(events, "the shell's a2 containment runs, as from open").toEqual(['closed']);
    expect(actorScopeStatus()).toBe('closed');
    expect(storageBytes(), 'nothing is removed or rewritten').toBe(bytes);
    observeActor({ key: null });
    expect(events, 'once: a second nobody is closed to closed').toEqual(['closed']);

    // Back as A: the local area names B, so row 3 applies to it; this tab's
    // session area is A's and is kept.
    events.length = 0;
    observeActor({ key: ACTOR_A });
    expect(actorScopeStatus()).toBe('open');
    expect(events).toEqual(['cleared', 'restamped', 'opened']);
    expect(stamps()).toEqual([ACTOR_A, ACTOR_A]);
    expect(privateLeft()).toEqual(['mip.genie.inFlightTurn', 'mip.queueContext']);
    expect(resetDocument).not.toHaveBeenCalled();
  });

  it("a synthetic event, then nobody, then A: 'closed', then 'opened' with nothing removed", () => {
    openForA();
    storageEvent({ key: STAMPS.local, newValue: ACTOR_B, storageArea: window.localStorage });
    events.length = 0;
    observeActor({ key: null });
    expect(events).toEqual(['closed']);
    observeActor({ key: ACTOR_A });
    expect(events).toEqual(['closed', 'opened']);
    expect(privateLeft()).toHaveLength(4);
  });

  it("a suspend of an already-closed gate leaves no 'closed' owed: the containment already ran", () => {
    openForA();
    observeActor({ key: null });
    expect(events).toEqual(['closed']);
    storageEvent({ key: STAMPS.local, newValue: ACTOR_B, storageArea: window.localStorage });
    expect(rechecks).toBe(1);
    observeActor({ key: null });
    expect(events, 'no second containment').toEqual(['closed']);
  });

  it('suspends a document open for nobody when another tab stamps a real actor', () => {
    _resetActorScopeForTests({ status: 'pending', owner: NOBODY });
    observeActor({ key: null });
    expect(actorScopeStatus()).toBe('open');
    storageEvent({ key: STAMPS.local, newValue: ACTOR_A, storageArea: window.localStorage });
    expect(actorScopeStatus()).toBe('closed');
    expect(rechecks).toBe(1);
  });
});

describe('the stamp guard: no PRIVATE_LOCAL access under a foreign stamp (B6)', () => {
  it('a read after a raw foreign restamp returns null and suspends once, in a microtask', async () => {
    openForA();
    local().setItem(STAMPS.local, ACTOR_B);
    expect(readActorScoped('local', PINNED_INSIGHTS_KEY)).toBeNull();
    expect(readActorScoped('local', 'mip.genie.conversationId')).toBeNull();
    expect(actorScopeStatus(), 'never emitted synchronously from a read').toBe('open');
    expect(events).toEqual([]);
    await Promise.resolve();
    expect(actorScopeStatus()).toBe('closed');
    expect(events).toEqual(['restamped']);
    expect(rechecks, 'exactly one suspend for the burst').toBe(1);
  });

  it('a write under a foreign stamp is dropped and suspends', async () => {
    openForA();
    local().setItem(STAMPS.local, ACTOR_B);
    writeActorScoped('local', PINNED_INSIGHTS_KEY, '[]');
    updateActorScoped('local', 'mip.genie.conversationId', () => 'conv-of-someone');
    expect(local().getItem(PINNED_INSIGHTS_KEY)).toBe(PINS);
    expect(local().getItem('mip.genie.conversationId')).toBe('conv-a');
    await Promise.resolve();
    expect(actorScopeStatus()).toBe('closed');
    expect(rechecks).toBe(1);
  });

  it('an absent local stamp is not foreign; the per-tab session area is never guarded', async () => {
    openForA();
    local().removeItem(STAMPS.local);
    expect(readActorScoped('local', PINNED_INSIGHTS_KEY)).toBe(PINS);
    session().setItem(STAMPS.session, ACTOR_B);
    expect(readActorScoped('session', 'mip.queueContext')).toBe('{}');
    await Promise.resolve();
    expect(actorScopeStatus()).toBe('open');
    expect(rechecks).toBe(0);
  });

  it("a suspend from a read under a foreign stamp, answered by nobody, emits 'closed'", async () => {
    openForA();
    local().setItem(STAMPS.local, ACTOR_B);
    expect(readActorScoped('local', PINNED_INSIGHTS_KEY)).toBeNull();
    await Promise.resolve();
    expect(events).toEqual(['restamped']);
    observeActor({ key: null });
    expect(events).toEqual(['restamped', 'closed']);
    expect(actorScopeStatus()).toBe('closed');
  });

  it("an open-for-nobody document's owner is '~nobody'", async () => {
    _resetActorScopeForTests({ status: 'pending', owner: NOBODY });
    observeActor({ key: null });
    local().setItem(STAMPS.local, ACTOR_A);
    expect(readActorScoped('local', PINNED_INSIGHTS_KEY)).toBeNull();
    await Promise.resolve();
    expect(actorScopeStatus()).toBe('closed');
  });
});

describe('takeActorResetNotice', () => {
  it('is true once: it reads and removes the flag', () => {
    session().setItem(NOTICE, '1');
    expect(takeActorResetNotice()).toBe(true);
    expect(session().getItem(NOTICE)).toBeNull();
    expect(takeActorResetNotice()).toBe(false);
    session().setItem(NOTICE, 'yes');
    expect(takeActorResetNotice(), "only '1' is a notice").toBe(false);
  });
});
