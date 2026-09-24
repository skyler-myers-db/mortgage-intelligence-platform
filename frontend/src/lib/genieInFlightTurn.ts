import { useSyncExternalStore } from 'react';
import type { GenieAnswer as GenieAnswerShape } from '../types';
import { ApiError, api, isAbortError, isWarmingUpError, type GenieLiveProgress } from './api';
import { GenieLiveError, MAX_LIVE_WAIT_MS, pollGenieTurn, submitGenieTurn, type GenieTurnIds } from './genieAsk';
import {
  GENIE_CONVERSATION_RESET_EVENT,
  GENIE_IN_FLIGHT_TURN_KEY,
  clearGenieConversationState,
  writeGenieConversationId,
} from './genieConversation';
import { appendGenieTurn, getGenieTurns } from './genieConversationStore';
import {
  GENIE_RESUME_FAILED_REASON,
  GENIE_STOPPED_REASON,
  genieTurnOutcome,
  genieOutcomeAnnouncement,
  interruptedReason,
  shouldPersistConversation,
  type GenieTurnOutcome,
} from './genieTurnOutcome';
import {
  browserGenieTurnLock,
  genieTurnLockName,
  type GenieTurnLockOutcome,
  type GenieTurnLockRequester,
} from './genieTurnLock';

export {
  GENIE_ANSWER_READY,
  GENIE_BUSY_REASON,
  GENIE_FAILED_ANNOUNCEMENT,
  GENIE_WITHHELD_ANNOUNCEMENT,
  GOVERNED_ACTION_SOURCE,
  genieTurnOutcome,
  genieOutcomeAnnouncement,
  shouldPersistConversation,
  type GenieTurnOutcome,
} from './genieTurnOutcome';
export type { GenieTurnLockOutcome, GenieTurnLockRequester } from './genieTurnLock';

/**
 * The ONE in-flight Genie turn of this tab (audit 2026-09-21 `runtime-01`,
 * `genie-03`, `genie-v2`, `a11y-06`).
 *
 * Both Genie surfaces (the floating panel and `/ask-genie`) used to run their
 * own lifecycle: the panel aborted on unmount, the route ran the ask as a
 * query that was cancelled when the user left the page, and the two could
 * each hold a turn at once. The turn now lives here, at module level, read
 * with `useSyncExternalStore` (the genieConversationStore pattern), so it
 * survives leaving the route, the panel remounting and, through the
 * sessionStorage record below, a reload.
 *
 * One slot per tab: `startGenieTurn` is the synchronous latch and returns
 * false while a turn runs. When a turn settles the STORE lands it (appends to
 * the shared transcript, persists the conversation id, sets the announcement),
 * so an answer lands even when no surface is mounted.
 *
 * Never re-POSTed: the submit POST has no Idempotency-Key, so a second POST
 * would create a second Genie message. A failure lands as a degraded turn,
 * and Retry / Ask again / Regenerate start a NEW turn only on the user's click.
 *
 * Reload resume (sessionStorage only, best effort, never logged): the record
 * carries a phase. Only a 'polling' record with its ids, younger than
 * MAX_LIVE_WAIT_MS, whose Web Lock this tab can take, resumes; its question
 * stays hidden until the first progress poll returns 200. 'submitting' and
 * 'completing' records never resume (a second complete would double-audit):
 * they, and every other case, become an 'interrupted' note with no request.
 *
 * Fail-closed identity boundary: a GENIE_CONVERSATION_RESET_EVENT listener,
 * added when this module loads, aborts the turn, removes the record, releases
 * the lock and clears everything, whether or not a surface is mounted.
 */

export type GenieTurnSurface = 'panel' | 'route';
export type GenieTurnPhase = 'submitting' | 'polling' | 'completing';

export interface GenieInFlightTurn {
  readonly generation: number;
  /** The surface the turn was started from. Both surfaces render it. */
  readonly surface: GenieTurnSurface;
  /** The question; '' while a resumed turn is unconfirmed (see `revealed`). */
  readonly question: string;
  /** The conversation the question was submitted into. */
  readonly conversationId: string | null;
  readonly phase: GenieTurnPhase;
  readonly startedAt: number;
  readonly deep: boolean;
  readonly progress: GenieLiveProgress | null;
  /** Resumed from the sessionStorage record after a reload. */
  readonly resumed: boolean;
  /** False while a resumed turn waits for its first progress poll to return
   *  200: until then the persisted question is not rendered anywhere. */
  readonly revealed: boolean;
}

export interface GenieTurnNote {
  readonly kind: 'stopped' | 'interrupted';
  /** The note's visible explanation. */
  readonly reason: string;
  /** The question the note offers to Ask again or Edit; '' when it has none. */
  readonly question: string;
  /** Settled turns that existed when the note was made (transcript order). */
  readonly atTurnIndex: number;
}

export interface GenieTurnSnapshot {
  readonly inFlight: GenieInFlightTurn | null;
  readonly notes: readonly GenieTurnNote[];
  /** What a surface announcer says while no turn is in flight. */
  readonly announcement: string;
}

export interface GenieTurnSettledEvent {
  readonly surface: GenieTurnSurface;
  readonly question: string;
  readonly response: GenieAnswerShape;
  readonly outcome: GenieTurnOutcome;
  /** The conversation id the store just persisted, or null. */
  readonly persistedConversationId: string | null;
}

export interface StartGenieTurnInput {
  question: string;
  conversationId: string | null;
  surface: GenieTurnSurface;
  /** Epoch ms of the user's action, read at the event site. */
  startedAt: number;
}

interface PersistedTurnRecord {
  v: 1;
  question: string;
  conversationId: string | null;
  surface: GenieTurnSurface;
  startedAt: number;
  deep: boolean;
  phase: GenieTurnPhase;
  ids?: GenieTurnIds;
}

interface ActiveTurn {
  generation: number;
  question: string;
  record: PersistedTurnRecord;
}

/** Stopped / interrupted notes kept in memory (they are not transcript turns). */
const MAX_NOTES = 20;

const EMPTY_SNAPSHOT: GenieTurnSnapshot = { inFlight: null, notes: [], announcement: '' };

let snapshot: GenieTurnSnapshot = EMPTY_SNAPSHOT;
let generation = 0;
let active: ActiveTurn | null = null;
let controller: AbortController | null = null;
let completeRequestedFor = -1;
let resumeChecked = false;
let requestLock: GenieTurnLockRequester = browserGenieTurnLock;
let lockOwner: number | null = null;
let lockRelease: (() => void) | null = null;
let pageHidden = false;
let deferredFailure: (() => void) | null = null;
const listeners = new Set<() => void>();
const settledListeners = new Set<(event: GenieTurnSettledEvent) => void>();

// ---------------------------------------------------------------- snapshot

function update(next: Partial<GenieTurnSnapshot>): void {
  snapshot = { ...snapshot, ...next };
  for (const listener of listeners) listener();
}

function patchTurn(gen: number, patch: Partial<GenieInFlightTurn>): void {
  const current = snapshot.inFlight;
  if (!current || current.generation !== gen) return;
  update({ inFlight: { ...current, ...patch } });
}

function withNote(kind: GenieTurnNote['kind'], reason: string, question: string): readonly GenieTurnNote[] {
  const note: GenieTurnNote = { kind, reason, question, atTurnIndex: getGenieTurns().length };
  return [...snapshot.notes, note].slice(-MAX_NOTES);
}

function isCurrent(gen: number): boolean {
  return generation === gen && active?.generation === gen;
}

// ------------------------------------------------------ sessionStorage record

function isSurface(value: unknown): value is GenieTurnSurface {
  return value === 'panel' || value === 'route';
}

function isPhase(value: unknown): value is GenieTurnPhase {
  return value === 'submitting' || value === 'polling' || value === 'completing';
}

function parseIds(value: unknown): GenieTurnIds | undefined {
  if (!value || typeof value !== 'object') return undefined;
  const ids = value as Partial<GenieTurnIds>;
  if (typeof ids.conversationId !== 'string' || !ids.conversationId) return undefined;
  if (typeof ids.messageId !== 'string' || !ids.messageId) return undefined;
  if (typeof ids.progressToken !== 'string' || !ids.progressToken) return undefined;
  return { conversationId: ids.conversationId, messageId: ids.messageId, progressToken: ids.progressToken };
}

function parseRecord(raw: string): PersistedTurnRecord | null {
  const value: unknown = JSON.parse(raw);
  if (!value || typeof value !== 'object') return null;
  const record = value as Partial<PersistedTurnRecord>;
  if (record.v !== 1 || typeof record.question !== 'string' || !record.question.trim()) return null;
  if (!isSurface(record.surface) || !isPhase(record.phase)) return null;
  if (typeof record.startedAt !== 'number' || !Number.isFinite(record.startedAt)) return null;
  const conversationId = typeof record.conversationId === 'string' ? record.conversationId : null;
  return {
    v: 1,
    question: record.question,
    conversationId,
    surface: record.surface,
    startedAt: record.startedAt,
    deep: record.deep === true,
    phase: record.phase,
    ids: parseIds(record.ids),
  };
}

/** The persisted record, or null. A malformed record is removed. */
function readRecord(): PersistedTurnRecord | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.sessionStorage.getItem(GENIE_IN_FLIGHT_TURN_KEY);
    if (!raw) return null;
    const record = parseRecord(raw);
    if (!record) window.sessionStorage.removeItem(GENIE_IN_FLIGHT_TURN_KEY);
    return record;
  } catch {
    return null;
  }
}

/** Best effort: storage full or blocked means the turn runs in memory only. */
function writeRecord(record: PersistedTurnRecord): void {
  if (typeof window === 'undefined') return;
  try {
    window.sessionStorage.setItem(GENIE_IN_FLIGHT_TURN_KEY, JSON.stringify(record));
  } catch {
    // Quota exceeded / privacy mode: only the reload resume is lost.
  }
}

function removeRecord(): void {
  if (typeof window === 'undefined') return;
  try {
    window.sessionStorage.removeItem(GENIE_IN_FLIGHT_TURN_KEY);
  } catch {
    // Storage unavailable: there is nothing to remove.
  }
}

function recordPhase(gen: number, patch: Partial<PersistedTurnRecord>): void {
  if (!isCurrent(gen) || !active) return;
  active.record = { ...active.record, ...patch };
  writeRecord(active.record);
}

// ------------------------------------------------------------------ Web Lock

/** Hold `mip-genie-turn:<messageId>` for generation `gen` until it settles.
 *  Never rejects. */
function holdLock(gen: number, messageId: string): Promise<GenieTurnLockOutcome> {
  lockOwner = gen;
  let pending: Promise<GenieTurnLockOutcome>;
  try {
    pending = requestLock(genieTurnLockName(messageId));
  } catch {
    pending = Promise.resolve({ kind: 'unsupported' });
  }
  return pending
    .then((outcome) => {
      if (outcome.kind === 'held') {
        if (lockOwner === gen && lockRelease === null) lockRelease = outcome.release;
        else outcome.release();
      }
      return outcome;
    })
    .catch((): GenieTurnLockOutcome => ({ kind: 'unsupported' }));
}

function releaseLock(): void {
  lockOwner = null;
  const release = lockRelease;
  lockRelease = null;
  release?.();
}

/** The turn is over (settled, stopped, reset): forget it everywhere. */
function finishActive(): void {
  active = null;
  controller = null;
  removeRecord();
  releaseLock();
}

// ------------------------------------------------------------------ settling

function notifySettled(event: GenieTurnSettledEvent): void {
  for (const listener of settledListeners) {
    try {
      listener(event);
    } catch (err) {
      // A surface bug must never cost the answer; surface it asynchronously.
      queueMicrotask(() => {
        throw err;
      });
    }
  }
}

/** Land an exchange: listeners first, then the transcript, then clear the
 *  in-flight turn in the same task so no frame renders without the answer. */
function landExchange(
  surface: GenieTurnSurface,
  question: string,
  response: GenieAnswerShape,
  persistedConversationId: string | null,
): void {
  const outcome = genieTurnOutcome(response);
  notifySettled({ surface, question, response, outcome, persistedConversationId });
  appendGenieTurn(question, response);
  update({ inFlight: null, announcement: genieOutcomeAnnouncement(outcome) });
}

function settleTurn(gen: number, response: GenieAnswerShape): void {
  const turn = active;
  const inFlight = snapshot.inFlight;
  if (!isCurrent(gen) || !turn || !inFlight) return;
  finishActive();
  const persisted = shouldPersistConversation(response) ? (response.conversation_id ?? null) : null;
  if (persisted) writeGenieConversationId(persisted);
  landExchange(inFlight.surface, turn.question, response, persisted);
}

function failureAnswer(err: unknown): string {
  if (err instanceof GenieLiveError) return err.message;
  if (isWarmingUpError(err)) return 'Genie is waking the SQL warehouse. Ask again in a moment.';
  if (err instanceof Error) return `Genie session reset: ${err.message}`;
  return 'Genie session reset.';
}

function failTurn(gen: number, err: unknown): void {
  // A reload or navigation cancels the page's requests, and they fail as
  // network errors while the page is going away. That is not the turn
  // failing: keep the record so the next page resumes the turn or notes the
  // interruption. Should the page come back from the back/forward cache
  // instead, the failure is handled then.
  if (pageHidden) {
    deferredFailure = () => failTurn(gen, err);
    return;
  }
  const turn = active;
  const inFlight = snapshot.inFlight;
  if (!isCurrent(gen) || !turn || !inFlight || isAbortError(err)) return;
  const status = err instanceof ApiError ? err.status : null;
  finishActive();
  if (inFlight.resumed && (status === 400 || status === 403)) {
    // The token is bound to the actor: after a reload a 403 can mean a
    // different actor. Fail closed, silently: no question, no transcript.
    update({ inFlight: null });
    clearGenieConversationState({ notify: true });
    return;
  }
  if (inFlight.resumed && !inFlight.revealed) {
    update({
      inFlight: null,
      notes: withNote('interrupted', GENIE_RESUME_FAILED_REASON, ''),
      announcement: genieOutcomeAnnouncement('failed'),
    });
    return;
  }
  // A 403 on a fresh turn: clear the conversation (the reset listeners run
  // now), then land the failure where the question was asked.
  if (status === 403) clearGenieConversationState({ notify: true });
  landExchange(
    inFlight.surface,
    turn.question,
    { answer: failureAnswer(err), source: 'degraded', trusted_assets: [] },
    null,
  );
}

// ------------------------------------------------------------------- the turn

async function pollAndComplete(
  gen: number,
  ids: GenieTurnIds,
  question: string,
  deadline: number,
  deep: boolean,
  signal: AbortSignal,
): Promise<void> {
  await pollGenieTurn(ids, {
    signal,
    deadline,
    deep,
    onProgress: (progress) => {
      if (!isCurrent(gen)) return;
      patchTurn(gen, { progress, question, revealed: true });
    },
  });
  if (!isCurrent(gen) || completeRequestedFor === gen) return;
  completeRequestedFor = gen;
  // Written synchronously BEFORE the single complete call: a reload from here
  // on must never complete this turn a second time.
  recordPhase(gen, { phase: 'completing' });
  patchTurn(gen, { phase: 'completing' });
  const response = await api.genieComplete(ids.conversationId, ids.messageId, ids.progressToken, question, signal);
  settleTurn(gen, response as GenieAnswerShape);
}

async function runFreshTurn(
  gen: number,
  question: string,
  conversationId: string | null,
  signal: AbortSignal,
): Promise<void> {
  const submitted = await submitGenieTurn(question, conversationId, signal);
  if (!isCurrent(gen)) return;
  if (submitted.kind === 'completed') {
    settleTurn(gen, submitted.response as GenieAnswerShape);
    return;
  }
  const ids: GenieTurnIds = {
    conversationId: submitted.conversationId,
    messageId: submitted.messageId,
    progressToken: submitted.progressToken,
  };
  recordPhase(gen, { phase: 'polling', deep: submitted.deep, ids });
  patchTurn(gen, { phase: 'polling', deep: submitted.deep });
  // Held from the moment the ids exist, so a duplicated tab (which copies
  // sessionStorage) cannot resume this turn while this tab runs it.
  void holdLock(gen, ids.messageId);
  await pollAndComplete(gen, ids, question, Date.now() + MAX_LIVE_WAIT_MS, submitted.deep, signal);
}

/** Start a turn. False, and nothing is sent, while a turn is in flight. */
export function startGenieTurn({ question, conversationId, surface, startedAt }: StartGenieTurnInput): boolean {
  const trimmed = question.trim();
  if (!trimmed || snapshot.inFlight) return false;
  // Any record in storage from here on is this page's own turn.
  resumeChecked = true;
  const gen = ++generation;
  const ctrl = new AbortController();
  controller = ctrl;
  const record: PersistedTurnRecord = {
    v: 1,
    question: trimmed,
    conversationId,
    surface,
    startedAt,
    deep: false,
    phase: 'submitting',
  };
  active = { generation: gen, question: trimmed, record };
  writeRecord(record);
  update({
    inFlight: {
      generation: gen,
      surface,
      question: trimmed,
      conversationId,
      phase: 'submitting',
      startedAt,
      deep: false,
      progress: null,
      resumed: false,
      revealed: true,
    },
    announcement: '',
  });
  runFreshTurn(gen, trimmed, conversationId, ctrl.signal).catch((err: unknown) => failTurn(gen, err));
  return true;
}

/**
 * Stop waiting for the in-flight turn (client-only: there is no server
 * cancel). The generation bump makes a reply that still arrives land nowhere.
 * Returns the stopped question, or null when nothing (visible) was stopped.
 */
export function stopGenieTurn(): string | null {
  const inFlight = snapshot.inFlight;
  if (!inFlight) return null;
  const question = inFlight.revealed ? (active?.question ?? inFlight.question) : '';
  generation += 1;
  controller?.abort();
  finishActive();
  update({
    inFlight: null,
    notes: question ? withNote('stopped', GENIE_STOPPED_REASON, question) : snapshot.notes,
  });
  return question || null;
}

/**
 * Resume a turn a reload interrupted. Runs once per page, from the first
 * Genie surface to mount (never at module evaluation: the chunk is
 * idle-preloaded without a surface).
 */
export function resumeGenieTurnFromSession(): void {
  if (resumeChecked) return;
  resumeChecked = true;
  if (snapshot.inFlight) return;
  const record = readRecord();
  if (!record) return;
  const ids = record.ids;
  const fresh = Date.now() - record.startedAt < MAX_LIVE_WAIT_MS;
  if (record.phase !== 'polling' || !ids || !fresh) {
    removeRecord();
    update({ notes: withNote('interrupted', interruptedReason(record.phase === 'completing' ? 'completing' : 'reload'), record.question) });
    return;
  }
  const gen = ++generation;
  const ctrl = new AbortController();
  controller = ctrl;
  active = { generation: gen, question: record.question, record };
  update({
    inFlight: {
      generation: gen,
      surface: record.surface,
      question: '',
      conversationId: record.conversationId,
      phase: 'polling',
      startedAt: record.startedAt,
      deep: record.deep,
      progress: null,
      resumed: true,
      revealed: false,
    },
    announcement: '',
  });
  holdLock(gen, ids.messageId)
    .then((outcome) => {
      if (!isCurrent(gen)) return undefined;
      if (outcome.kind !== 'held') {
        finishActive();
        update({
          inFlight: null,
          notes: withNote('interrupted', interruptedReason(outcome.kind === 'busy' ? 'other-tab' : 'reload'), record.question),
        });
        return undefined;
      }
      return pollAndComplete(gen, ids, record.question, record.startedAt + MAX_LIVE_WAIT_MS, record.deep, ctrl.signal);
    })
    .catch((err: unknown) => failTurn(gen, err));
}

export function clearGenieTurnNotes(): void {
  if (snapshot.notes.length === 0) return;
  update({ notes: [] });
}

/** Say `text` through the surface announcer (Stop, prefill, action, copy). */
export function announceGenie(text: string): void {
  if (snapshot.announcement === text) return;
  update({ announcement: text });
}

export function getGenieTurnSnapshot(): GenieTurnSnapshot {
  return snapshot;
}

function getServerSnapshot(): GenieTurnSnapshot {
  return EMPTY_SNAPSHOT;
}

export function subscribeGenieTurn(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Called synchronously when a turn settles, before the transcript emit. */
export function subscribeGenieTurnSettled(listener: (event: GenieTurnSettledEvent) => void): () => void {
  settledListeners.add(listener);
  return () => {
    settledListeners.delete(listener);
  };
}

export function useGenieTurn(): GenieTurnSnapshot {
  return useSyncExternalStore(subscribeGenieTurn, getGenieTurnSnapshot, getServerSnapshot);
}

// ------------------------------------------------------ identity boundary

function onConversationReset(): void {
  generation += 1;
  controller?.abort();
  finishActive();
  if (snapshot === EMPTY_SNAPSHOT) return;
  snapshot = EMPTY_SNAPSHOT;
  for (const listener of listeners) listener();
}

function onPageHide(): void {
  pageHidden = true;
}

function onPageShow(): void {
  pageHidden = false;
  const failure = deferredFailure;
  deferredFailure = null;
  failure?.();
}

if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
  window.addEventListener(GENIE_CONVERSATION_RESET_EVENT, onConversationReset);
  window.addEventListener('pagehide', onPageHide);
  window.addEventListener('pageshow', onPageShow);
}

// ------------------------------------------------------------------- tests

/** Test seam: happy-dom has no `navigator.locks`. Null restores the browser's. */
export function __setGenieTurnLockForTests(requester: GenieTurnLockRequester | null): void {
  requestLock = requester ?? browserGenieTurnLock;
}

export function __resetGenieTurnStoreForTests(): void {
  generation += 1;
  controller?.abort();
  finishActive();
  lockOwner = null;
  completeRequestedFor = -1;
  resumeChecked = false;
  pageHidden = false;
  deferredFailure = null;
  requestLock = browserGenieTurnLock;
  snapshot = EMPTY_SNAPSHOT;
  for (const listener of listeners) listener();
}
