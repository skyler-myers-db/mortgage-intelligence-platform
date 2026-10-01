/**
 * The cancelled-bulk stash (R5-21) through the actor gate (the W5b critique
 * correction): a PRIVATE_SESSION key of lib/actorScope, so a write is queued
 * while the gate is pending and dropped while it is closed, a read returns
 * null unless the gate is open, and a clear removes the key only while the
 * gate is open. Node environment with a stubbed `window` (two Map-backed
 * storages): the gate needs no DOM.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NOBODY, _resetActorScopeForTests, observeActor } from '../../lib/actorScope';
import { ACTOR_A } from '../../test/actorKeys';
import { clearCancelledBulk, readCancelledBulk, stashCancelledBulk } from './bulkApproveStash';

const STASH_KEY = 'mip.bulkApprove.lastCancelled';
const T0 = Date.parse('2026-10-01T09:00:00Z');

let session: Map<string, string>;

function storage(values: Map<string, string>) {
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => {
      values.set(key, value);
    },
    removeItem: (key: string) => {
      values.delete(key);
    },
  };
}

/** A new document over storage stamped A: the gate pending until observed. */
function pendingDocumentOfA(stash?: string): void {
  _resetActorScopeForTests({ status: 'pending', owner: ACTOR_A });
  if (stash !== undefined) session.set(STASH_KEY, stash);
}

const stashOf = (ok: number, aborted: number, kind: 'approve' | 'reject' = 'approve', ts = Date.now()) =>
  JSON.stringify({ ok, aborted, kind, ts });

beforeEach(() => {
  session = new Map();
  vi.stubGlobal('window', { localStorage: storage(new Map()), sessionStorage: storage(session) });
  vi.useFakeTimers();
  vi.setSystemTime(T0);
  _resetActorScopeForTests({ status: 'open', owner: ACTOR_A });
});

afterEach(() => {
  vi.useRealTimers();
  _resetActorScopeForTests({ status: 'open', owner: NOBODY });
  vi.unstubAllGlobals();
});

describe('the cancelled-bulk stash while the gate is open', () => {
  it('stashes, reads back with its kind, and clears', () => {
    stashCancelledBulk(2, 4, 'reject');
    expect(JSON.parse(session.get(STASH_KEY) ?? '{}')).toEqual({ ok: 2, aborted: 4, kind: 'reject', ts: T0 });
    expect(readCancelledBulk()).toEqual({ ok: 2, aborted: 4, kind: 'reject' });
    clearCancelledBulk();
    expect(session.has(STASH_KEY)).toBe(false);
    expect(readCancelledBulk()).toBeNull();
  });

  it('reads an older value without a kind as an approve run, and drops a stale or empty one', () => {
    session.set(STASH_KEY, JSON.stringify({ ok: 1, aborted: 2, ts: T0 }));
    expect(readCancelledBulk()).toEqual({ ok: 1, aborted: 2, kind: 'approve' });
    session.set(STASH_KEY, stashOf(1, 2, 'approve', T0 - 11 * 60 * 1000));
    expect(readCancelledBulk(), 'older than ten minutes').toBeNull();
    session.set(STASH_KEY, stashOf(0, 0));
    expect(readCancelledBulk(), 'says nothing').toBeNull();
    session.set(STASH_KEY, '{not json');
    expect(readCancelledBulk(), 'never throws').toBeNull();
  });
});

describe('the cancelled-bulk stash through the actor gate', () => {
  it('queues a write while pending and applies it when the gate opens for the owner', () => {
    pendingDocumentOfA();
    stashCancelledBulk(3, 1);
    expect(session.has(STASH_KEY), 'queued, not applied').toBe(false);
    observeActor({ key: ACTOR_A });
    expect(readCancelledBulk()).toEqual({ ok: 3, aborted: 1, kind: 'approve' });
  });

  it('reads null while pending and while closed, with the stash still stored', () => {
    pendingDocumentOfA(stashOf(1, 1));
    expect(readCancelledBulk(), 'pending').toBeNull();
    observeActor({ key: ACTOR_A });
    expect(readCancelledBulk(), 'open').toEqual({ ok: 1, aborted: 1, kind: 'approve' });
    observeActor({ key: null });
    expect(readCancelledBulk(), 'closed').toBeNull();
    expect(session.get(STASH_KEY)).toBe(stashOf(1, 1));
  });

  it('drops a write while closed', () => {
    observeActor({ key: null });
    stashCancelledBulk(5, 5);
    expect(session.has(STASH_KEY)).toBe(false);
    observeActor({ key: ACTOR_A });
    expect(session.has(STASH_KEY), 'nothing was queued either').toBe(false);
  });

  it('a clear while pending removes nothing: the next mount after the gate opens still flashes it', () => {
    pendingDocumentOfA(stashOf(2, 3));
    expect(readCancelledBulk(), 'the mount before the gate opened read nothing').toBeNull();
    clearCancelledBulk();
    observeActor({ key: ACTOR_A });
    expect(session.get(STASH_KEY), 'no queued removal deleted the unread flash').toBe(stashOf(2, 3));
    expect(readCancelledBulk()).toEqual({ ok: 2, aborted: 3, kind: 'approve' });
  });

  it('a clear while closed removes nothing', () => {
    session.set(STASH_KEY, stashOf(1, 2));
    observeActor({ key: null });
    clearCancelledBulk();
    expect(session.get(STASH_KEY)).toBe(stashOf(1, 2));
  });
});
