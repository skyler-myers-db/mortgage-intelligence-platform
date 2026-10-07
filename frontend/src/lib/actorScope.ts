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
 * of A is a PROVEN change (D-identity-review-a3), so a cross-tab restamp of
 * the shared local stamp cannot hide it. The document is RESET, never
 * reopened in place: the gate closes for good (no later observation, storage
 * event or stamp check runs again here), unsaved work is dropped, every area
 * not stamped k (or an alias) loses its private keys, both stamps become k,
 * and the tab reloads at '/' with a one-time notice (takeActorResetNotice).
 * Only 'restamped' is emitted: a 'cleared' or 'closed' would clear the query
 * cache, and the re-render would send audited refetches under k's cookie
 * before the page goes. A second reset within 30 s of the last one is HELD
 * until that boundary ('restamped' then 'closed'; actorResetHeld() is true and
 * the shell swaps the routed page for its placeholder before it clears).
 * A FIRST observation (no document owner yet) only runs the rows: a reload as
 * another actor is that actor's own navigation.
 *
 * Cross-tab (the W5a integrator ruling): another tab restamping the shared
 * local stamp SUSPENDS an open gate (closed, 'restamped' only, then a health
 * recheck through lib/healthRecheck): a probe answering the owner reopens it,
 * another actor is a proven change, and nobody emits 'closed' as row 5 does
 * from open (the shell's containment runs then). While open, a local read
 * returns null and a local write is dropped when the stored local stamp is
 * present and is not this document's owner; either queues one suspend. An
 * absent stamp is not foreign.
 *
 * Events, in order, after the status is set: 'cleared' (a PRESENT private key
 * was removed), 'restamped' (a stamp now names another owner: row 2 with
 * s !== k, or rows 1/3/4, a suspend, a reset), then 'opened' / 'closed' on a
 * status change (and 'closed' when the observation answering a suspend
 * leaves the gate closed). The shell clears in-memory state on 'cleared' and
 * 'closed' only, so a first visit never clears the boot prefetches; stores
 * drop their caches on every event. Storage that throws holds values (stamps
 * included) in memory for the document, so row 1 applies at every document
 * start, and the reset notice and its loop guard are lost with the old
 * document.
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

/** Keys registered ahead of the lane that writes them (key: lane). None today. */
export const PREREGISTERED: Readonly<Record<string, string>> = Object.freeze({});

/** DEVICE_SESSION: the one-time notice a reset leaves for the next document. */
const RESET_NOTICE_KEY = 'mip.actorResetNotice';
/** DEVICE_SESSION: when this tab last reset for a proven change (epoch ms). */
const RESET_AT_KEY = 'mip.actorResetAt';
/** A second reset within this long of the last one is held until it passes. */
const RESET_LOOP_MS = 30_000;

export type ActorScopedKey<A extends ActorScopeArea> = A extends 'local'
  ? (typeof PRIVATE_LOCAL)[number]
  : (typeof PRIVATE_SESSION)[number];
export type ActorPreferenceKey = (typeof ACTOR_PREFERENCE_LOCAL)[number];
type Updater = (prev: string | null) => string | null;
type ProvenChangeHandler = (key: string, aliases: readonly string[]) => void;

const AREAS: readonly ActorScopeArea[] = ['local', 'session'];
const PRIVATE: Readonly<Record<ActorScopeArea, readonly string[]>> = { local: PRIVATE_LOCAL, session: PRIVATE_SESSION };
const PREFERENCE_CAP = 8;

/**
 * What a reset and a suspend call outside the gate: hooks/useUnsavedGuard's
 * clearUnsavedWork and lib/healthRecheck's requestHealthRecheck. Each module
 * registers itself here when it loads (both are in the shell's initial
 * closure), so this module imports neither: the bundler keeps this module in
 * a chunk shared with lazy routes, and an import from here pulled both into
 * it (measured +0.48 KiB br of initial JS in chunk overhead).
 */
export interface ActorScopeHooks {
  clearUnsavedWork?: () => void;
  requestHealthRecheck?: () => void;
}
const hooks: Required<ActorScopeHooks> = { clearUnsavedWork: () => undefined, requestHealthRecheck: () => undefined };

export function registerActorScopeHooks(next: ActorScopeHooks): void {
  Object.assign(hooks, next);
}

let status: ActorScopeStatus = 'pending';
/** The last resolution's owner (k or NOBODY); null before the first. */
let owner: string | null = null;
let docOwner: string | null = null;
let resolvedOnce = false;
let queue: Array<() => void> = [];
/** A proven change ran: this document never resolves again (B1 (i)). */
let resetting = false;
/** The reset waits for the loop guard's boundary (actorResetHeld). */
let resetHeld = false;
let heldTimer: ReturnType<typeof setTimeout> | null = null;
/** One suspend is queued from a read or write under a foreign local stamp. */
let suspendQueued = false;
/** A suspend closed an OPEN gate, and no observation has resolved since. */
let suspendedOpen = false;
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

function runRows(key: string | null, aliases: readonly string[]): void {
  // A suspend closed an open gate without 'closed'; the observation that
  // answers it decides as row 5 would have from open (see the emit below).
  const wasSuspended = suspendedOpen;
  suspendedOpen = false;
  const next = key ?? NOBODY;
  let cleared = false;
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
  // After a suspend the status was already 'closed', but nothing has been
  // contained yet: a trusted nobody answering it emits 'closed' as row 5 does
  // from open, so the shell clears its in-memory state and Genie resets.
  if (before !== status || (wasSuspended && status === 'closed')) emit(status === 'open' ? 'opened' : 'closed');
}

/** Replace the document: a full load of '/', so every module, cache and
 *  mounted reader of the old actor goes with it (test seam below). */
function browserResetDocument(): void {
  window.location.replace('/');
}

let resetDocument: () => void = browserResetDocument;

/** Leave the notice for the next document and stamp the loop guard, then go. */
function leaveDocument(): void {
  rawWrite('session', RESET_NOTICE_KEY, '1');
  rawWrite('session', RESET_AT_KEY, String(Date.now()));
  resetDocument();
}

/**
 * Ms left until RESET_LOOP_MS has passed since this tab's last reset, else 0.
 * An absent, unparsable (NaN) or non-positive stamp gives a NaN or huge age,
 * which reads as no recent reset (staleChunkRecovery's claim pattern).
 */
function loopGuardWait(): number {
  const age = Date.now() - Number(rawRead('session', RESET_AT_KEY));
  return age >= 0 && age < RESET_LOOP_MS ? RESET_LOOP_MS - age : 0;
}

/**
 * D-identity-review-a3: a proven actor change resets the document (see the
 * module note). Synchronous and in this order: close for good, drop unsaved
 * work, remove what k does not own and stamp k, then leave (or hold).
 */
function resetForProvenChange(key: string, aliases: readonly string[]): void {
  status = 'closed';
  resetting = true;
  suspendedOpen = false;
  queue = [];
  hooks.clearUnsavedWork();
  for (const area of AREAS) {
    const stamp = rawRead(area, STAMPS[area]);
    if (stamp !== key && !(stamp !== null && aliases.includes(stamp))) removePrivate(area);
  }
  for (const area of AREAS) rawWrite(area, STAMPS[area], key);
  const wait = loopGuardWait();
  // Set before 'restamped', so the shell's swap reads it on that event.
  resetHeld = wait > 0;
  emit('restamped');
  if (!resetHeld) {
    leaveDocument();
    return;
  }
  emit('closed');
  // The only timer: `resetting` makes every later observation a no-op.
  heldTimer = setTimeout(() => {
    heldTimer = null;
    leaveDocument();
  }, wait);
}

let provenChange: ProvenChangeHandler = resetForProvenChange;

/**
 * Another tab may have changed the actor: close an open gate at once, drop
 * nothing, and ask for a trusted probe. 'restamped', never 'cleared' or
 * 'closed': stores drop their caches and read null, the query cache stays.
 * The probe decides: the owner reopens ('opened'), another actor is a proven
 * change, and nobody emits 'closed' (runRows, through `suspendedOpen`).
 */
function suspend(): void {
  if (status === 'pending' || resetting) return;
  if (status === 'open') {
    status = 'closed';
    suspendedOpen = true;
    emit('restamped');
  }
  hooks.requestHealthRecheck();
}

/** Present and not this document's owner: another tab restamped the area. */
function foreignLocalStamp(): boolean {
  const stamp = rawRead('local', STAMPS.local);
  return stamp !== null && stamp !== owner;
}

/** One suspend per microtask; never emitted from a read, which can run in render. */
function queueSuspend(): void {
  if (suspendQueued) return;
  suspendQueued = true;
  queueMicrotask(() => {
    suspendQueued = false;
    suspend();
  });
}

/** Cross-tab: another document wrote (or cleared) the shared local stamp. */
function onStorage(event: StorageEvent): void {
  try {
    if (event.storageArea !== window.localStorage) return;
  } catch {
    return;
  }
  if (event.key !== null && event.key !== STAMPS.local) return;
  if (status === 'pending' || resetting || owner === null || event.newValue === owner) return;
  suspend();
}

if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
  window.addEventListener('storage', onStorage);
}

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

/** True while a proven change waits for the loop guard's 30 s boundary. */
export function actorResetHeld(): boolean {
  return resetHeld;
}

/**
 * The notice a proven-change reset left for this document: read and removed
 * at once, so it is shown once. The shell calls it when the gate first opens.
 */
export function takeActorResetNotice(): boolean {
  const left = rawRead('session', RESET_NOTICE_KEY) === '1';
  if (left) rawWrite('session', RESET_NOTICE_KEY, null);
  return left;
}

/** A trusted actor observation (key null = nobody). A no-op once a reset ran. */
export function observeActor({ key, aliases = [] }: { key: string | null; aliases?: readonly string[] }): void {
  if (resetting) return;
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

/** Open, but the shared local area is stamped for another owner: refuse, and suspend. */
function refusedAsForeign(area: ActorScopeArea): boolean {
  if (area !== 'local' || !foreignLocalStamp()) return false;
  queueSuspend();
  return true;
}

/** A private value, or null unless the gate is open (and the area is this owner's). */
export function readActorScoped<A extends ActorScopeArea>(area: A, key: ActorScopedKey<A>): string | null {
  if (status !== 'open' || refusedAsForeign(area)) return null;
  return rawRead(area, key);
}

/**
 * Write through `updater` over the raw stored string (null removes): applied
 * now while open (dropped under another owner's local stamp), the UPDATER
 * queued while pending (replayed against the post-resolution value), dropped
 * while closed.
 */
export function updateActorScoped<A extends ActorScopeArea>(area: A, key: ActorScopedKey<A>, updater: Updater): void {
  const run = () => rawWrite(area, key, updater(rawRead(area, key)));
  if (status === 'open') {
    if (!refusedAsForeign(area)) run();
  } else if (status === 'pending') queue.push(run);
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
  provenChange = handler ?? resetForProvenChange;
}

/** Test seam: what a reset calls instead of a full load of '/' (null restores it). */
export function _setResetDocumentForTests(fn: (() => void) | null): void {
  resetDocument = fn ?? browserResetDocument;
}

/**
 * Test seam (src/test/setup.ts runs it before every test as open for nobody,
 * then installs a no-op resetDocument). Sets the status, both stamps and the
 * document owner (null for nobody, and for 'pending': a fresh document has
 * none yet), clears the queue, the memory and any reset (its held timer
 * too, and resetDocument is restored), KEEPS every subscriber (stores
 * subscribe once at module evaluation) and emits one 'restamped' so store
 * caches drop. Never 'cleared'.
 */
export function _resetActorScopeForTests({ status: next, owner: who }: { status: ActorScopeStatus; owner: string }): void {
  status = next;
  owner = next === 'pending' ? null : who;
  docOwner = next === 'pending' || who === NOBODY ? null : who;
  resolvedOnce = next !== 'pending';
  queue = [];
  memory.clear();
  resetting = false;
  resetHeld = false;
  suspendQueued = false;
  suspendedOpen = false;
  if (heldTimer !== null) clearTimeout(heldTimer);
  heldTimer = null;
  resetDocument = browserResetDocument;
  provenChange = resetForProvenChange;
  for (const area of AREAS) rawWrite(area, STAMPS[area], who);
  emit('restamped');
}
