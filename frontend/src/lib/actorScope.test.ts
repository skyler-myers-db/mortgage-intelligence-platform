/**
 * The actor gate over browser storage (D-identity-review-b), at the module
 * layer: the six resolution rows per area, the document-level proven-change
 * check, the pending queue, the preference map, storage that throws, the
 * legacy key and the event order. Node environment with a stubbed `window`
 * (two Map-backed storages and an event target): the gate needs no DOM.
 *
 * The rendered consequences (pins, the Genie transcript, the new-tab and
 * nobody cases) are proven on the real AppShell in
 * components/layout/AppShell.actorBoundary*.test.tsx.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  ACTOR_SCOPE_REGISTRY,
  NOBODY,
  _resetActorScopeForTests,
  _setProvenChangeHandlerForTests,
  _setResetDocumentForTests,
  actorScopeStatus,
  documentOwnerKey,
  observeActor,
  readActorPreference,
  readActorScoped,
  removeActorScoped,
  subscribeActorScope,
  updateActorScoped,
  writeActorPreference,
  writeActorScoped,
  type ActorScopeReason,
} from './actorScope';
import { GENIE_CONVERSATION_RESET_EVENT, GENIE_CONVERSATION_STORAGE_KEY, GENIE_IN_FLIGHT_TURN_KEY } from './genieConversation';
import { GENIE_CONVERSATION_TURNS_KEY } from './genieConversationStore';
import { SINGLE_KEY_SHORTCUTS_STORAGE_KEY } from './keymapPreference';
import { PINNED_INSIGHTS_KEY } from './pinnedInsights';
import { QUEUE_CONTEXT_STORAGE_KEY } from './queueContext';
import { ACTOR_A, ACTOR_B, ACTOR_C } from '../test/actorKeys';

const OWNER = 'mip.actorOwner';
const TAB_OWNER = 'mip.actorCacheKey';
const PREF = SINGLE_KEY_SHORTCUTS_STORAGE_KEY;

let local: Map<string, string>;
let session: Map<string, string>;
let events: ActorScopeReason[];
let unsubscribe: (() => void) | undefined;

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

function installWindow(): void {
  local = new Map();
  session = new Map();
  const target = new EventTarget();
  vi.stubGlobal('window', {
    localStorage: storage(local),
    sessionStorage: storage(session),
    addEventListener: target.addEventListener.bind(target),
    removeEventListener: target.removeEventListener.bind(target),
    dispatchEvent: target.dispatchEvent.bind(target),
  });
}

/** A new document: gate pending, storage holding exactly `stored`. */
function freshDocument(stored: { local?: Record<string, string>; session?: Record<string, string> } = {}): void {
  unsubscribe?.();
  _resetActorScopeForTests({ status: 'pending', owner: NOBODY });
  local.clear();
  session.clear();
  for (const [key, value] of Object.entries(stored.local ?? {})) local.set(key, value);
  for (const [key, value] of Object.entries(stored.session ?? {})) session.set(key, value);
  events = [];
  unsubscribe = subscribeActorScope(({ reason }) => events.push(reason));
}

const PINS = JSON.stringify([{ id: 'p1', question: 'q1', summary: 's1', source: null, pinnedAt: '2026-09-30' }]);
/** One actor's private data in both areas, stamped `owner`. */
function ownedBy(owner: string | null): { local: Record<string, string>; session: Record<string, string> } {
  return {
    local: {
      ...(owner === null ? {} : { [OWNER]: owner }),
      [PINNED_INSIGHTS_KEY]: PINS,
      [GENIE_CONVERSATION_STORAGE_KEY]: 'conv-1',
      'mip.theme': 'light',
    },
    session: {
      ...(owner === null ? {} : { [TAB_OWNER]: owner }),
      [GENIE_CONVERSATION_TURNS_KEY]: '[]',
      [GENIE_IN_FLIGHT_TURN_KEY]: '{"v":2}',
      'mip.mainScroll.v1': '{}',
    },
  };
}

const privateLeft = () => [
  ...ACTOR_SCOPE_REGISTRY.PRIVATE_LOCAL.filter((key) => local.has(key)),
  ...ACTOR_SCOPE_REGISTRY.PRIVATE_SESSION.filter((key) => session.has(key)),
];
const stamps = () => [local.get(OWNER) ?? null, session.get(TAB_OWNER) ?? null];
const snapshot = () => JSON.stringify([[...local], [...session]]);

beforeEach(() => {
  installWindow();
  freshDocument();
});

afterEach(() => {
  unsubscribe?.();
  _resetActorScopeForTests({ status: 'open', owner: NOBODY });
  vi.unstubAllGlobals();
});

describe('registry', () => {
  it('classifies every store key, and the in-flight key the shell reads', () => {
    expect(ACTOR_SCOPE_REGISTRY.PRIVATE_LOCAL).toEqual([PINNED_INSIGHTS_KEY, GENIE_CONVERSATION_STORAGE_KEY]);
    expect(ACTOR_SCOPE_REGISTRY.PRIVATE_SESSION).toEqual([
      GENIE_CONVERSATION_TURNS_KEY,
      GENIE_IN_FLIGHT_TURN_KEY,
      QUEUE_CONTEXT_STORAGE_KEY,
      'mip.bulkApprove.lastCancelled',
      'mip.queryCache.v1',
    ]);
    expect(GENIE_IN_FLIGHT_TURN_KEY).toBe('mip.genie.inFlightTurn');
    expect(ACTOR_SCOPE_REGISTRY.ACTOR_PREFERENCE_LOCAL).toEqual([PREF]);
    expect(Object.isFrozen(ACTOR_SCOPE_REGISTRY)).toBe(true);
    expect(Object.isFrozen(ACTOR_SCOPE_REGISTRY.PRIVATE_SESSION)).toBe(true);
  });
});

describe('resolution rows, per area', () => {
  it('row 1: an absent stamp removes the private keys, stamps k and opens; device keys stay', () => {
    freshDocument(ownedBy(null));
    observeActor({ key: ACTOR_A });
    expect(privateLeft()).toEqual([]);
    expect(stamps()).toEqual([ACTOR_A, ACTOR_A]);
    expect(local.get('mip.theme')).toBe('light');
    expect(session.get('mip.mainScroll.v1')).toBe('{}');
    expect(actorScopeStatus()).toBe('open');
    expect(events).toEqual(['cleared', 'restamped', 'opened']);
  });

  it('row 1 for nobody: unowned data is never adopted, not even by nobody', () => {
    freshDocument(ownedBy(null));
    observeActor({ key: null });
    expect(privateLeft()).toEqual([]);
    expect(stamps()).toEqual([NOBODY, NOBODY]);
    expect(actorScopeStatus()).toBe('open');
  });

  it('row 2: the same actor keeps everything and emits only opened', () => {
    freshDocument(ownedBy(ACTOR_A));
    const before = snapshot();
    observeActor({ key: ACTOR_A });
    expect(snapshot()).toBe(before);
    expect(readActorScoped('local', PINNED_INSIGHTS_KEY)).toBe(PINS);
    expect(events).toEqual(['opened']);
  });

  it('row 2 by alias: keeps the data, re-stamps to k and re-keys the preference entry', () => {
    freshDocument({
      local: { ...ownedBy(ACTOR_A).local, [PREF]: JSON.stringify({ [ACTOR_C]: 'on', [ACTOR_A]: 'off' }) },
      session: ownedBy(ACTOR_A).session,
    });
    observeActor({ key: ACTOR_B, aliases: [ACTOR_A] });
    expect(privateLeft()).toHaveLength(4);
    expect(stamps()).toEqual([ACTOR_B, ACTOR_B]);
    expect(JSON.parse(local.get(PREF) ?? '{}')).toEqual({ [ACTOR_B]: 'off', [ACTOR_C]: 'on' });
    expect(readActorPreference(PREF)).toBe('off');
    expect(events).toEqual(['restamped', 'opened']);
  });

  it('row 3: another real owner is removed and restamped', () => {
    freshDocument(ownedBy(ACTOR_A));
    observeActor({ key: ACTOR_B });
    expect(privateLeft()).toEqual([]);
    expect(stamps()).toEqual([ACTOR_B, ACTOR_B]);
    expect(events).toEqual(['cleared', 'restamped', 'opened']);
  });

  it("row 4: nobody's data is removed when a real actor arrives", () => {
    freshDocument(ownedBy(NOBODY));
    observeActor({ key: ACTOR_A });
    expect(privateLeft()).toEqual([]);
    expect(stamps()).toEqual([ACTOR_A, ACTOR_A]);
  });

  it('row 5: a real owner observed as nobody keeps everything, rewrites nothing and CLOSES', () => {
    freshDocument(ownedBy(ACTOR_A));
    const before = snapshot();
    observeActor({ key: null });
    expect(snapshot()).toBe(before);
    expect(actorScopeStatus()).toBe('closed');
    expect(readActorScoped('local', PINNED_INSIGHTS_KEY)).toBeNull();
    expect(events).toEqual(['closed']);
  });

  it("row 6: nobody's data observed as nobody is kept and opens for nobody", () => {
    freshDocument(ownedBy(NOBODY));
    const before = snapshot();
    observeActor({ key: null });
    expect(snapshot()).toBe(before);
    expect(actorScopeStatus()).toBe('open');
    expect(readActorScoped('session', GENIE_IN_FLIGHT_TURN_KEY)).toBe('{"v":2}');
  });

  it('a mixed gate: local closed (row 5) and session absent (row 1) closes the gate', () => {
    freshDocument({ local: ownedBy(ACTOR_A).local, session: ownedBy(null).session });
    observeActor({ key: null });
    expect(actorScopeStatus()).toBe('closed');
    expect(local.get(PINNED_INSIGHTS_KEY)).toBe(PINS);
    expect(session.has(GENIE_IN_FLIGHT_TURN_KEY)).toBe(false);
    expect(stamps()).toEqual([ACTOR_A, NOBODY]);
    expect(events).toEqual(['cleared', 'restamped', 'closed']);
  });
});

describe('document-level proven change', () => {
  it('a different real actor after the document opened goes to the handler, not the rows', () => {
    freshDocument(ownedBy(ACTOR_A));
    observeActor({ key: ACTOR_A });
    const handler = vi.fn();
    _setProvenChangeHandlerForTests(handler);
    const before = snapshot();
    observeActor({ key: ACTOR_B });
    expect(handler).toHaveBeenCalledOnce();
    expect(handler).toHaveBeenCalledWith(ACTOR_B, []);
    expect(snapshot()).toBe(before);
  });

  it('the default resets the document (D-identity-review-a3): restamped only, both stamps k, the owner kept, one reset', () => {
    freshDocument(ownedBy(ACTOR_A));
    observeActor({ key: ACTOR_A });
    events.length = 0;
    const reset = vi.fn();
    _setResetDocumentForTests(reset);
    observeActor({ key: ACTOR_B });
    expect(events, 'never cleared or closed: no query-cache clear before the page goes').toEqual(['restamped']);
    expect(privateLeft()).toEqual([]);
    expect(stamps()).toEqual([ACTOR_B, ACTOR_B]);
    expect(actorScopeStatus()).toBe('closed');
    expect(documentOwnerKey(), 'the document stays A until it is replaced').toBe(ACTOR_A);
    expect(reset).toHaveBeenCalledOnce();
  });

  it('a cross-tab restamp of the shared local stamp cannot hide the change: the handler fires once', () => {
    freshDocument(ownedBy(ACTOR_A));
    observeActor({ key: ACTOR_A });
    const handler = vi.fn();
    _setProvenChangeHandlerForTests(handler);
    local.set(OWNER, ACTOR_B); // another tab resolved B
    observeActor({ key: ACTOR_B });
    expect(handler).toHaveBeenCalledOnce();
  });

  it('an alias of the document owner is not a change', () => {
    freshDocument(ownedBy(ACTOR_A));
    observeActor({ key: ACTOR_A });
    const handler = vi.fn();
    _setProvenChangeHandlerForTests(handler);
    observeActor({ key: ACTOR_B, aliases: [ACTOR_A] });
    expect(handler).not.toHaveBeenCalled();
    expect(documentOwnerKey()).toBe(ACTOR_B);
  });
});

describe('documentOwnerKey', () => {
  it('is null before the first real open and for nobody, set on the first real open, kept by a close', () => {
    freshDocument(ownedBy(NOBODY));
    expect(documentOwnerKey()).toBeNull();
    observeActor({ key: null });
    expect(documentOwnerKey(), 'open for nobody').toBeNull();
    observeActor({ key: ACTOR_A });
    expect(documentOwnerKey()).toBe(ACTOR_A);
    observeActor({ key: null });
    expect(actorScopeStatus()).toBe('closed');
    expect(documentOwnerKey(), 'never cleared by a close').toBe(ACTOR_A);
    observeActor({ key: ACTOR_A });
    expect(actorScopeStatus()).toBe('open');
    expect(documentOwnerKey()).toBe(ACTOR_A);
  });
});

describe('the pending queue', () => {
  const pin = (id: string) => (raw: string | null) => JSON.stringify([id, ...(raw ? (JSON.parse(raw) as string[]) : [])]);

  it('replays UPDATERS against the post-resolution value: [p2, p1] for the same actor, [p2] for a new one', () => {
    freshDocument({ local: { [OWNER]: ACTOR_A, [PINNED_INSIGHTS_KEY]: '["p1"]' } });
    updateActorScoped('local', PINNED_INSIGHTS_KEY, pin('p2'));
    expect(local.get(PINNED_INSIGHTS_KEY), 'queued, not applied').toBe('["p1"]');
    observeActor({ key: ACTOR_A });
    expect(local.get(PINNED_INSIGHTS_KEY)).toBe('["p2","p1"]');

    freshDocument({ local: { [OWNER]: ACTOR_A, [PINNED_INSIGHTS_KEY]: '["p1"]' } });
    updateActorScoped('local', PINNED_INSIGHTS_KEY, pin('p2'));
    observeActor({ key: ACTOR_B });
    expect(local.get(PINNED_INSIGHTS_KEY)).toBe('["p2"]');
  });

  it('a resolution that closes the gate drops the queue', () => {
    freshDocument(ownedBy(ACTOR_A));
    writeActorScoped('local', GENIE_CONVERSATION_STORAGE_KEY, 'conv-queued');
    observeActor({ key: null });
    observeActor({ key: ACTOR_A });
    expect(local.get(GENIE_CONVERSATION_STORAGE_KEY)).toBe('conv-1');
  });

  it('writes while closed are dropped; reads return null; removals too', () => {
    freshDocument(ownedBy(ACTOR_A));
    observeActor({ key: null });
    writeActorScoped('local', GENIE_CONVERSATION_STORAGE_KEY, 'conv-closed');
    removeActorScoped('session', GENIE_IN_FLIGHT_TURN_KEY);
    expect(readActorScoped('local', GENIE_CONVERSATION_STORAGE_KEY)).toBeNull();
    expect(local.get(GENIE_CONVERSATION_STORAGE_KEY)).toBe('conv-1');
    expect(session.get(GENIE_IN_FLIGHT_TURN_KEY)).toBe('{"v":2}');
  });

  it('reads nothing while pending', () => {
    freshDocument(ownedBy(ACTOR_A));
    expect(actorScopeStatus()).toBe('pending');
    expect(readActorScoped('local', PINNED_INSIGHTS_KEY)).toBeNull();
    expect(readActorScoped('session', GENIE_CONVERSATION_TURNS_KEY)).toBeNull();
  });
});

describe('storage that throws', () => {
  const blocked = () => {
    throw new DOMException('blocked', 'SecurityError');
  };
  const throwing = { getItem: blocked, setItem: blocked, removeItem: blocked };

  it('holds values, preferences and stamps in memory for the document', () => {
    vi.stubGlobal('window', { localStorage: throwing, sessionStorage: throwing });
    freshDocument();
    observeActor({ key: ACTOR_A });
    expect(actorScopeStatus()).toBe('open');
    writeActorScoped('local', GENIE_CONVERSATION_STORAGE_KEY, 'conv-memory');
    expect(readActorScoped('local', GENIE_CONVERSATION_STORAGE_KEY)).toBe('conv-memory');
    writeActorPreference(PREF, 'off');
    expect(readActorPreference(PREF)).toBe('off');
    observeActor({ key: null });
    expect(actorScopeStatus(), 'the in-memory stamp is A: row 5').toBe('closed');
  });

  it('a new document starts with no stamp (row 1): a trusted nobody opens instead of closing', async () => {
    vi.stubGlobal('window', { localStorage: throwing, sessionStorage: throwing });
    vi.resetModules();
    const fresh = await import('./actorScope');
    const seen: ActorScopeReason[] = [];
    fresh.subscribeActorScope(({ reason }) => seen.push(reason));
    fresh.observeActor({ key: null });
    expect(fresh.actorScopeStatus()).toBe('open');
    expect(seen).toEqual(['restamped', 'opened']);
  });
});

describe('legacy key and upgrade', () => {
  it("removes 'mip.lastBorrowerId' at the first resolution, without a cleared event", () => {
    freshDocument({ local: { [OWNER]: ACTOR_A, 'mip.lastBorrowerId': 'B-0OXOBYLW8MNCK' }, session: { [TAB_OWNER]: ACTOR_A } });
    observeActor({ key: ACTOR_A });
    expect(local.has('mip.lastBorrowerId')).toBe(false);
    expect(events).toEqual(['opened']);
  });

  it('upgrade: no owner stamp yet removes the pins and adopts only the legacy plain shortcut value', () => {
    freshDocument({ local: { [PINNED_INSIGHTS_KEY]: PINS, [PREF]: 'off' }, session: { [TAB_OWNER]: ACTOR_A } });
    observeActor({ key: ACTOR_A });
    expect(local.has(PINNED_INSIGHTS_KEY)).toBe(false);
    expect(JSON.parse(local.get(PREF) ?? '{}')).toEqual({ [ACTOR_A]: 'off' });
    expect(readActorPreference(PREF)).toBe('off');
  });

  it('a first visit with nothing stored emits no cleared (the shell keeps its boot prefetches)', () => {
    observeActor({ key: ACTOR_A });
    expect(events).toEqual(['restamped', 'opened']);
    expect(events).not.toContain('cleared');
  });
});

describe('the preference map', () => {
  it("reads the local stamp's entry while pending and closed, the resolved owner's while open", () => {
    freshDocument({ local: { [OWNER]: ACTOR_A, [PREF]: JSON.stringify({ [ACTOR_A]: 'off' }) }, session: { [TAB_OWNER]: ACTOR_A } });
    expect(readActorPreference(PREF), 'pending').toBe('off');
    observeActor({ key: null });
    expect(actorScopeStatus()).toBe('closed');
    expect(readActorPreference(PREF), 'closed').toBe('off');
    writeActorPreference(PREF, 'on');
    expect(JSON.parse(local.get(PREF) ?? '{}'), 'dropped while closed').toEqual({ [ACTOR_A]: 'off' });

    freshDocument({ local: { [OWNER]: ACTOR_A, [PREF]: JSON.stringify({ [ACTOR_A]: 'off' }) }, session: { [TAB_OWNER]: ACTOR_A } });
    observeActor({ key: ACTOR_B });
    expect(readActorPreference(PREF), 'B has no entry: the default').toBeNull();
    expect(JSON.parse(local.get(PREF) ?? '{}'), 'never cleared').toEqual({ [ACTOR_A]: 'off' });
  });

  it('a write while pending is queued and lands under the resolved owner', () => {
    freshDocument({ local: { [OWNER]: ACTOR_A }, session: { [TAB_OWNER]: ACTOR_A } });
    writeActorPreference(PREF, 'off');
    expect(local.has(PREF)).toBe(false);
    observeActor({ key: ACTOR_B });
    expect(JSON.parse(local.get(PREF) ?? '{}')).toEqual({ [ACTOR_B]: 'off' });
  });

  it('keeps at most 8 entries, most recently written first', () => {
    observeActor({ key: null });
    const owners = Array.from({ length: 9 }, (_, index) => `actor_${String(index).repeat(16)}`);
    for (const who of owners) {
      _resetActorScopeForTests({ status: 'open', owner: who });
      writeActorPreference(PREF, 'off');
    }
    expect(Object.keys(JSON.parse(local.get(PREF) ?? '{}'))).toEqual([...owners].reverse().slice(0, 8));
  });
});

describe('ported from the shell reset guard and actorScopedBrowserState', () => {
  it('the first trusted key after a first visit clears nothing; a disappearing key closes; a swapped key resets the document', () => {
    observeActor({ key: ACTOR_A });
    expect(events).not.toContain('cleared');
    events.length = 0;
    observeActor({ key: null });
    expect(events).toEqual(['closed']);
    observeActor({ key: ACTOR_A });
    events.length = 0;
    const resetDocument = vi.fn();
    _setResetDocumentForTests(resetDocument);
    observeActor({ key: ACTOR_B });
    expect(events).toEqual(['restamped']);
    expect(resetDocument).toHaveBeenCalledOnce();
  });

  /** A's private keys in every PRIVATE slot the registry knows. */
  const everyPrivateOfA = () => ({
    local: { ...ownedBy(ACTOR_A).local },
    session: {
      ...ownedBy(ACTOR_A).session,
      [QUEUE_CONTEXT_STORAGE_KEY]: '{"borrower_id":"B-0OXOBYLW8MNCK"}',
      'mip.bulkApprove.lastCancelled': '{"ok":1}',
      'mip.queryCache.v1': '{}',
    },
  });

  it('a first observation of another actor removes every private key and resets the mounted Genie surfaces once', () => {
    freshDocument(everyPrivateOfA());
    const reset = vi.fn();
    window.addEventListener(GENIE_CONVERSATION_RESET_EVENT, reset);
    try {
      observeActor({ key: ACTOR_B });
    } finally {
      window.removeEventListener(GENIE_CONVERSATION_RESET_EVENT, reset);
    }
    expect(privateLeft()).toEqual([]);
    expect(reset).toHaveBeenCalledTimes(1);
  });

  it('a mid-session actor change removes every private key and replaces the document (the reset contract)', () => {
    freshDocument(everyPrivateOfA());
    observeActor({ key: ACTOR_A });
    expect(privateLeft(), 'the same actor kept everything').toHaveLength(7);
    const genieReset = vi.fn();
    const resetDocument = vi.fn();
    _setResetDocumentForTests(resetDocument);
    window.addEventListener(GENIE_CONVERSATION_RESET_EVENT, genieReset);
    try {
      observeActor({ key: ACTOR_B });
    } finally {
      window.removeEventListener(GENIE_CONVERSATION_RESET_EVENT, genieReset);
    }
    expect(privateLeft()).toEqual([]);
    expect(stamps()).toEqual([ACTOR_B, ACTOR_B]);
    expect(resetDocument, 'every mounted Genie surface goes with the document').toHaveBeenCalledOnce();
    expect(genieReset, 'no in-place reset: the document is replaced').not.toHaveBeenCalled();
  });

  it('a trusted nobody after a real owner resets the Genie surfaces too (the W5a bridge)', () => {
    freshDocument(ownedBy(ACTOR_A));
    observeActor({ key: ACTOR_A });
    const reset = vi.fn();
    window.addEventListener(GENIE_CONVERSATION_RESET_EVENT, reset);
    try {
      observeActor({ key: null });
    } finally {
      window.removeEventListener(GENIE_CONVERSATION_RESET_EVENT, reset);
    }
    expect(reset).toHaveBeenCalledTimes(1);
    expect(privateLeft()).toHaveLength(4);
  });
});

describe('the test seam', () => {
  it('keeps subscribers and emits one restamped, never cleared', () => {
    const seen: ActorScopeReason[] = [];
    const off = subscribeActorScope(({ reason }) => seen.push(reason));
    _resetActorScopeForTests({ status: 'open', owner: ACTOR_A });
    _resetActorScopeForTests({ status: 'pending', owner: NOBODY });
    off();
    expect(seen).toEqual(['restamped', 'restamped']);
  });

  it('an open reset makes the owner the document owner; a pending one is a fresh document', () => {
    _resetActorScopeForTests({ status: 'open', owner: ACTOR_A });
    expect(documentOwnerKey()).toBe(ACTOR_A);
    expect(stamps()).toEqual([ACTOR_A, ACTOR_A]);
    _resetActorScopeForTests({ status: 'pending', owner: ACTOR_A });
    expect(documentOwnerKey()).toBeNull();
    expect(actorScopeStatus()).toBe('pending');
  });
});
