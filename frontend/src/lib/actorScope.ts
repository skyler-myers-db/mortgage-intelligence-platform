/**
 * The actor gate over browser storage (D-identity-review-b; audit genie-02
 * item 1, bundle-04 item 4). docs/security/identity-boundary.md is the prose
 * version of this file.
 *
 * Every `mip` browser-storage key is classified in ONE registry below, and
 * lib/actorScope.registry.test.ts fails on a key or a storage call site the
 * registry does not know. PRIVATE keys (pins, the Genie conversation id,
 * transcript and in-flight turn, the queue context, the bulk-approve stash,
 * the persisted aggregate cache) belong to one actor. They are never
 * namespaced: each storage area carries an owner STAMP, and the gate decides
 * per area, on every trusted actor observation, whether the stored private
 * data belongs to the observed actor. Only this module reads or writes a
 * PRIVATE or PREFERENCE key; stores go through the guarded accessors.
 *
 * Gate: 'pending' at document start (reads return null, writes queue as
 * updaters), then 'open' or 'closed'. It opens only when both areas resolve
 * open. Resolution rows per area, for observation k (null = nobody) against
 * the area's stamp s, first match wins:
 *   1. s absent: remove the area's PRIVATE keys, stamp k (or '~nobody'). Open.
 *   2. k real and s is k or one of k's aliases: keep, re-stamp to k. Open.
 *   3. s real, k real, different: remove, stamp k. Open.
 *   4. s '~nobody', k real: remove, stamp k. Open.
 *   5. s real, k null: keep everything, rewrite nothing. CLOSED.
 *   6. s '~nobody', k null: keep. Open for nobody.
 * Before the rows, a document-level check: once this document has opened for
 * a real actor A (documentOwnerKey), a different real k that is not an alias
 * of A is a PROVEN change and goes to the replaceable proven-change handler
 * (the interim default runs the rows and makes k the document owner), so a
 * cross-tab restamp of the shared local stamp cannot hide it.
 *
 * Events, in order, after the status is set: 'cleared' (a PRESENT private key
 * was removed, or the proven-change default ran), 'restamped' (a stamp now
 * names another owner: row 2 with s !== k, or rows 1/3/4), then 'opened' /
 * 'closed' on a status change. The shell clears in-memory state on 'cleared'
 * and 'closed' only, so a first visit never clears the boot prefetches;
 * stores drop their caches on every event. Storage that throws holds values
 * (stamps included) in memory for the document, so row 1 applies at every
 * document start.
 *
 * Preference map ('mip.shortcuts.singleKey'): JSON {actorKey | '~nobody':
 * value}, at most 8 entries, most recently written first; never cleared.
 */

export type ActorScopeArea = 'local' | 'session';
export type ActorScopeStatus = 'pending' | 'open' | 'closed';
export type ActorScopeReason = 'opened' | 'cleared' | 'closed' | 'restamped';
export interface ActorScopeEvent {
  status: ActorScopeStatus;
  reason: ActorScopeReason;
}

/** The owner stamp of an area whose private data belongs to no actor. */
export const NOBODY = '~nobody';

const PRIVATE_LOCAL = Object.freeze(['mip.pinnedInsights', 'mip.genie.conversationId'] as const);
const PRIVATE_SESSION = Object.freeze([
  'mip-genie-conversation-v1',
  'mip.genie.inFlightTurn',
  'mip.queueContext',
  'mip.bulkApprove.lastCancelled',
  'mip.queryCache.v1',
  'mip.portfolio.campaignDraft.v1',
] as const);
const ACTOR_PREFERENCE_LOCAL = Object.freeze(['mip.shortcuts.singleKey'] as const);
const STAMPS = Object.freeze({ local: 'mip.actorOwner', session: 'mip.actorCacheKey' } as const);
const LEGACY = Object.freeze(['mip.lastBorrowerId'] as const);

/** Every browser-storage key the app may touch, by class. */
export const ACTOR_SCOPE_REGISTRY = Object.freeze({
  PRIVATE_LOCAL,
  PRIVATE_SESSION,
  ACTOR_PREFERENCE_LOCAL,
  DEVICE_LOCAL: Object.freeze([
    'mip.theme',
    'mip.accent',
    'mip.density',
    'mip.consoleOpen',
    'mip-genie-chat-size-v1',
    'mip-genie-chat-pos-v1',
    'mip.themeChosen',
    'mip.accentChosen',
  ] as const),
  DEVICE_SESSION: Object.freeze([
    'mip.mainScroll.v1',
    'mip.leadTableScroll.v1',
    'mip.rumApiSample',
    'mip.staleChunkReloadAt',
    'mip.actorResetNotice',
    'mip.actorResetAt',
  ] as const),
  STAMPS,
  LEGACY,
});

/** Registered ahead of the lane that writes them; absent from source until then. */
export const PREREGISTERED: Readonly<Record<string, string>> = Object.freeze({
  'mip.actorResetNotice': 'w5-identity-reset-portfolio',
  'mip.actorResetAt': 'w5-identity-reset-portfolio',
});

export type ActorScopedKey<A extends ActorScopeArea> = A extends 'local'
  ? (typeof PRIVATE_LOCAL)[number]
  : (typeof PRIVATE_SESSION)[number];
export type ActorPreferenceKey = (typeof ACTOR_PREFERENCE_LOCAL)[number];
type Updater = (prev: string | null) => string | null;
type ProvenChangeHandler = (key: string, aliases: readonly string[]) => void;

const AREAS: readonly ActorScopeArea[] = ['local', 'session'];
const PRIVATE: Readonly<Record<ActorScopeArea, readonly string[]>> = { local: PRIVATE_LOCAL, session: PRIVATE_SESSION };
const PREFERENCE_CAP = 8;

let status: ActorScopeStatus = 'pending';
/** The last resolution's owner (k or NOBODY); null before the first. */
let owner: string | null = null;
let docOwner: string | null = null;
let resolvedOnce = false;
let queue: Array<() => void> = [];
const listeners = new Set<(event: ActorScopeEvent) => void>();
const memory = new Map<string, string>();

// ------------------------------------------------------------ raw storage

function storageOf(area: ActorScopeArea): Storage {
  return area === 'local' ? window.localStorage : window.sessionStorage;
}

/** Storage, or this document's memory when storage is missing or throws. */
function rawRead(area: ActorScopeArea, key: string): string | null {
  try {
    return storageOf(area).getItem(key) ?? null;
  } catch {
    return memory.get(area + key) ?? null;
  }
}

function rawWrite(area: ActorScopeArea, key: string, value: string | null): void {
  try {
    const store = storageOf(area);
    if (value === null) store.removeItem(key);
    else store.setItem(key, value);
    memory.delete(area + key);
  } catch {
    if (value === null) memory.delete(area + key);
    else memory.set(area + key, value);
  }
}

// ------------------------------------------------------------ preference map

function parseMap(raw: string | null): Record<string, string> {
  const map: Record<string, string> = {};
  try {
    const value: unknown = raw ? JSON.parse(raw) : null;
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      for (const [who, entry] of Object.entries(value)) if (typeof entry === 'string') map[who] = entry;
    }
  } catch {
    // A legacy plain value or corrupt JSON holds no entries.
  }
  return map;
}

/** `who`'s entry first, then the rest, capped. */
function withEntry(map: Record<string, string>, who: string, value: string): string {
  const rest = Object.entries(map).filter(([entry]) => entry !== who);
  return JSON.stringify(Object.fromEntries([[who, value], ...rest].slice(0, PREFERENCE_CAP)));
}

function rekeyPreferences(from: string, to: string): void {
  for (const key of ACTOR_PREFERENCE_LOCAL) {
    const map = parseMap(rawRead('local', key));
    if (!Object.prototype.hasOwnProperty.call(map, from)) continue;
    const value = map[from];
    delete map[from];
    rawWrite('local', key, withEntry(map, to, value));
  }
}

/** Upgrade: a pre-map plain 'on' / 'off' becomes the first owner's entry. */
function adoptLegacyPreferences(who: string): void {
  for (const key of ACTOR_PREFERENCE_LOCAL) {
    const raw = rawRead('local', key);
    if (raw === 'on' || raw === 'off') rawWrite('local', key, JSON.stringify({ [who]: raw }));
  }
}

// ------------------------------------------------------------ resolution

function emit(reason: ActorScopeReason): void {
  const event = { status, reason };
  for (const listener of [...listeners]) {
    try {
      listener(event);
    } catch (err) {
      queueMicrotask(() => {
        throw err;
      });
    }
  }
}

function removePrivate(area: ActorScopeArea): boolean {
  let removed = false;
  for (const key of PRIVATE[area]) {
    if (rawRead(area, key) === null) continue;
    rawWrite(area, key, null);
    removed = true;
  }
  return removed;
}

/** `forced`: the proven-change default clears in-memory state even when no
 *  private key was stored (the document's caches hold the old actor's data). */
function runRows(key: string | null, aliases: readonly string[], forced = false): void {
  const next = key ?? NOBODY;
  let cleared = forced;
  let restamped = false;
  let closed = false;
  for (const area of AREAS) {
    const stamp = rawRead(area, STAMPS[area]);
    if (key !== null && stamp !== null && stamp !== NOBODY && (stamp === key || aliases.includes(stamp))) {
      // Row 2: the same actor (or its alias).
      if (stamp === key) continue;
      rawWrite(area, STAMPS[area], key);
      if (area === 'local') rekeyPreferences(stamp, key);
      restamped = true;
    } else if (stamp === null || key !== null) {
      // Rows 1, 3 and 4: unowned or another actor's data is never adopted.
      cleared = removePrivate(area) || cleared;
      rawWrite(area, STAMPS[area], next);
      // A new owner: whose preference entry applies changed.
      restamped = restamped || stamp !== next;
      if (stamp === null && area === 'local') adoptLegacyPreferences(next);
    } else if (stamp !== NOBODY) {
      closed = true; // Row 5: a real owner, observed nobody.
    } // Row 6: nobody's data, observed nobody.
  }
  const before = status;
  status = closed ? 'closed' : 'open';
  owner = next;
  if (key !== null && (docOwner === null || aliases.includes(docOwner))) docOwner = key;
  const runs = queue;
  queue = [];
  if (status === 'open') {
    for (const run of runs) {
      try {
        run();
      } catch {
        // One bad updater never blocks the rest of the replay.
      }
    }
  }
  if (cleared) emit('cleared');
  if (restamped) emit('restamped');
  if (before !== status) emit(status === 'open' ? 'opened' : 'closed');
}

/** Interim default until D-identity-review-a3 replaces it (W5b): the rows in
 *  place, always reported as 'cleared' (the shell then drops the old actor's
 *  query cache and in-memory state, as it did before the gate), and k
 *  becomes the document owner. */
function defaultProvenChange(key: string, aliases: readonly string[]): void {
  runRows(key, aliases, true);
  docOwner = key;
}

let provenChange: ProvenChangeHandler = defaultProvenChange;

// ------------------------------------------------------------ public API

export function actorScopeStatus(): ActorScopeStatus {
  return status;
}

export function subscribeActorScope(listener: (event: ActorScopeEvent) => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * The actor this document first opened for (a4 binds requests to it). Held
 * in memory only; never cleared by a close; null before the first real open
 * and while only nobody was observed.
 */
export function documentOwnerKey(): string | null {
  return docOwner;
}

/** A trusted actor observation (key null = nobody). */
export function observeActor({ key, aliases = [] }: { key: string | null; aliases?: readonly string[] }): void {
  if (!resolvedOnce) {
    resolvedOnce = true;
    for (const legacy of LEGACY) rawWrite('local', legacy, null);
  }
  if (docOwner !== null && key !== null && key !== docOwner && !aliases.includes(docOwner)) {
    provenChange(key, aliases);
    return;
  }
  runRows(key, aliases);
}

/** A private value, or null unless the gate is open. */
export function readActorScoped<A extends ActorScopeArea>(area: A, key: ActorScopedKey<A>): string | null {
  return status === 'open' ? rawRead(area, key) : null;
}

/**
 * Write through `updater` over the raw stored string (null removes): applied
 * now while open, the UPDATER queued while pending (replayed against the
 * post-resolution value), dropped while closed.
 */
export function updateActorScoped<A extends ActorScopeArea>(area: A, key: ActorScopedKey<A>, updater: Updater): void {
  const run = () => rawWrite(area, key, updater(rawRead(area, key)));
  if (status === 'open') run();
  else if (status === 'pending') queue.push(run);
}

export function writeActorScoped<A extends ActorScopeArea>(area: A, key: ActorScopedKey<A>, value: string): void {
  updateActorScoped(area, key, () => value);
}

export function removeActorScoped<A extends ActorScopeArea>(area: A, key: ActorScopedKey<A>): void {
  updateActorScoped(area, key, () => null);
}

/** The resolved owner's entry (null = unset); the local stamp's while not open. */
export function readActorPreference(key: ActorPreferenceKey): string | null {
  const who = status === 'open' ? owner : rawRead('local', STAMPS.local);
  return who === null ? null : (parseMap(rawRead('local', key))[who] ?? null);
}

/** Set the resolved owner's entry: now while open, queued while pending, dropped while closed. */
export function writeActorPreference(key: ActorPreferenceKey, value: string): void {
  const run = () => {
    if (owner !== null) rawWrite('local', key, withEntry(parseMap(rawRead('local', key)), owner, value));
  };
  if (status === 'open') run();
  else if (status === 'pending') queue.push(run);
}

// ------------------------------------------------------------ tests

export function _setProvenChangeHandlerForTests(handler: ProvenChangeHandler | null): void {
  provenChange = handler ?? defaultProvenChange;
}

/**
 * Test seam (src/test/setup.ts runs it before every test as open for nobody).
 * Sets the status, both stamps and the document owner (null for nobody, and
 * for 'pending': a fresh document has none yet), clears the queue and the
 * memory, KEEPS every subscriber (stores subscribe once at module
 * evaluation) and emits one 'restamped' so store caches drop. Never 'cleared'.
 */
export function _resetActorScopeForTests({ status: next, owner: who }: { status: ActorScopeStatus; owner: string }): void {
  status = next;
  owner = next === 'pending' ? null : who;
  docOwner = next === 'pending' || who === NOBODY ? null : who;
  resolvedOnce = next !== 'pending';
  queue = [];
  memory.clear();
  provenChange = defaultProvenChange;
  for (const area of AREAS) rawWrite(area, STAMPS[area], who);
  emit('restamped');
}
