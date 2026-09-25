import { useSyncExternalStore } from 'react';
import type { GenieAnswer as GenieAnswerShape } from '../types';
import type { GenieCompletionJobStatus, GenieTurnProgress } from '../types/genieJobs';
import { ApiError, isAbortError, isWarmingUpError, type GenieLiveProgress } from './api';
import {
  GenieLiveError,
  JOB_RESUME_WINDOW_MS,
  MAX_LIVE_WAIT_MS,
  pollGenieJob,
  pollGenieTurn,
  requestGenieCompletion,
  settledGenieJob,
  submitGenieTurn,
  type GenieTurnIds,
} from './genieAsk';
import {
  GENIE_CONVERSATION_RESET_EVENT,
  clearGenieConversationState,
  writeGenieConversationId,
} from './genieConversation';
import { appendGenieTurn, clearGenieTurns, getGenieTurns } from './genieConversationStore';
import {
  LEGACY_RECORD,
  readRecord,
  removeRecord,
  writeRecord,
  type PersistedTurnRecord,
} from './genieInFlightRecord';
import { requestGenieTurnCancel, type GenieTurnCancelTarget } from './genieTurnCancel';
import {
  GENIE_RESUME_FAILED_REASON,
  GENIE_STOP_CONFIRMED_REASON,
  GENIE_STOP_RECORDED_REASON,
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
 * Completion (audit 2026-09-21 `genie-01`): when submit says the server
 * runs completion jobs, the one complete call asks for a job (202) and the
 * store polls the job's status, showing the server's own stages; the job id
 * is written to the record the moment the 202 arrives.
 *
 * Reload resume (sessionStorage only, best effort, never logged; whose Web
 * Lock this tab can take):
 *   - 'polling', with ids, younger than MAX_LIVE_WAIT_MS: polls on, then
 *     completes (as a job when the record says so);
 *   - 'completing' of a JOB turn, younger than JOB_RESUME_WINDOW_MS: with its
 *     job id it only polls the job's status; without one (reloaded while the
 *     complete was held) it sends exactly one more complete, which joins the
 *     server's one job for the turn, then polls. Never a second audit;
 *   - a resumed question stays hidden until the first 200;
 *   - 'submitting', a legacy (non-job) 'completing', a stale record and a v:1
 *     record from before jobs become an 'interrupted' note with no request.
 * The submit is never re-POSTed.
 *
 * Fail-closed identity boundary: a GENIE_CONVERSATION_RESET_EVENT listener,
 * added when this module loads, aborts the turn, removes the record, releases
 * the lock and clears everything, the shared transcript included, whether or
 * not a surface is mounted.
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
  /** Genie's own progress, plus the completion job's stage once there is one. */
  readonly progress: GenieTurnProgress | null;
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
  /** A Stopped note's turn generation: the server's confirmation replaces it. */
  readonly stopId?: number;
}

/** The in-flight turn when an announcement was made during it (a copy
 *  result, say): the announcer says it until that turn's stage moves on. */
export interface GenieAnnouncedDuringTurn {
  readonly generation: number;
  readonly progress: GenieTurnProgress | null;
}

export interface GenieTurnSnapshot {
  readonly inFlight: GenieInFlightTurn | null;
  readonly notes: readonly GenieTurnNote[];
  /** What a surface announcer says (see `announcedDuringTurn` for a turn). */
  readonly announcement: string;
  /** Bumped every time an announcement is made, a repeat of the same text
   *  included, and never reset: each announcement is said once, and a surface
   *  that takes the floor never replays one said before (useGenieAnnouncer). */
  readonly announcementSeq: number;
  /** Set when the announcement was made while a turn was in flight. */
  readonly announcedDuringTurn: GenieAnnouncedDuringTurn | null;
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

interface ActiveTurn {
  generation: number;
  question: string;
  record: PersistedTurnRecord;
}

/** Stopped / interrupted notes kept in memory (they are not transcript turns). */
const MAX_NOTES = 20;

const EMPTY_SNAPSHOT: GenieTurnSnapshot = {
  inFlight: null,
  notes: [],
  announcement: '',
  announcementSeq: 0,
  announcedDuringTurn: null,
};

let snapshot: GenieTurnSnapshot = EMPTY_SNAPSHOT;
let generation = 0;
/** Monotonic for the page's life (test resets included), so an announcer's
 *  "already said" mark can never exceed the store's newest announcement. */
let announcementSeq = 0;
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

/** A new announcement: always a new sequence number, even for the same text,
 *  so a second "SQL copied" is said again. `during` is the turn still in
 *  flight after it, if any. */
function announcing(
  text: string,
  during: GenieInFlightTurn | null = null,
): Pick<GenieTurnSnapshot, 'announcement' | 'announcementSeq' | 'announcedDuringTurn'> {
  announcementSeq += 1;
  return {
    announcement: text,
    announcementSeq,
    announcedDuringTurn: during ? { generation: during.generation, progress: during.progress } : null,
  };
}

/** Nothing in flight, no notes, nothing to say; the sequence carries on. */
function clearedSnapshot(): GenieTurnSnapshot {
  return { ...EMPTY_SNAPSHOT, announcementSeq };
}

function patchTurn(gen: number, patch: Partial<GenieInFlightTurn>): void {
  const current = snapshot.inFlight;
  if (!current || current.generation !== gen) return;
  update({ inFlight: { ...current, ...patch } });
}

function withNote(kind: GenieTurnNote['kind'], reason: string, question: string, stopId?: number): readonly GenieTurnNote[] {
  const note: GenieTurnNote = { kind, reason, question, atTurnIndex: getGenieTurns().length, ...(stopId === undefined ? {} : { stopId }) };
  return [...snapshot.notes, note].slice(-MAX_NOTES);
}

/** An interrupted turn: no in-flight turn, a note in the transcript, and the
 *  note's reason said by the surface announcer. */
function interrupt(reason: string, question: string): void {
  const notes = withNote('interrupted', reason, question);
  update({ inFlight: null, notes, ...announcing(reason) });
}

function isCurrent(gen: number): boolean {
  return generation === gen && active?.generation === gen;
}

// ------------------------------------------------------ sessionStorage record

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
  update({ inFlight: null, ...announcing(genieOutcomeAnnouncement(outcome)) });
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
  if (inFlight.resumed && (status === 400 || status === 403 || status === 404)) {
    // The token (and the job) are bound to the actor: after a reload a 403 or
    // a 404 can mean a different actor. Fail closed, silently: no question, no
    // transcript.
    update({ inFlight: null });
    clearGenieConversationState({ notify: true });
    return;
  }
  if (inFlight.resumed && !inFlight.revealed) {
    interrupt(GENIE_RESUME_FAILED_REASON, '');
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

/** Genie's own turn as it last looked; a resumed job synthesizes it. */
const COMPLETED_GENIE_PROGRESS: GenieLiveProgress = {
  status: 'COMPLETED',
  stage: 'complete',
  stage_label: 'Verifying the answer against its rows',
  terminal: true,
  failed: false,
  reasoning_trace: [],
  sql_preview: null,
  error_hint: null,
};

/** Show the job's stage on the rail, keeping Genie's own last progress. A
 *  status poll's 200 (never the 202) reveals a resumed turn's question. */
function showJob(gen: number, job: GenieCompletionJobStatus, reveal: boolean): void {
  const inFlight = snapshot.inFlight;
  if (!isCurrent(gen) || !inFlight) return;
  if (job.terminal) {
    // The answer (or the failure) lands next: never flash a terminal stage.
    if (reveal && active) patchTurn(gen, { question: active.question, revealed: true });
    return;
  }
  const base = inFlight.progress ?? COMPLETED_GENIE_PROGRESS;
  const progress: GenieTurnProgress = {
    ...base,
    deep: inFlight.deep,
    job: {
      stage: job.stage,
      stage_label: job.stage_label,
      parts_done: job.parts_done,
      parts_planned: job.parts_planned,
      typical_seconds: job.typical_seconds,
    },
  };
  patchTurn(gen, reveal && active ? { progress, question: active.question, revealed: true } : { progress });
}

/** Poll the named job to its answer and land it. */
async function followJob(
  gen: number,
  ids: GenieTurnIds,
  question: string,
  jobId: string,
  startedAt: number,
  signal: AbortSignal,
): Promise<void> {
  const response = await pollGenieJob(ids, question, jobId, {
    signal,
    deadline: startedAt + JOB_RESUME_WINDOW_MS,
    onJob: (job) => showJob(gen, job, true),
  });
  settleTurn(gen, response as GenieAnswerShape);
}

/** The ONE complete request of a generation (plus its single timeout
 *  re-send inside requestGenieCompletion), then the job it names. */
async function completeTurn(
  gen: number,
  ids: GenieTurnIds,
  question: string,
  asyncComplete: boolean,
  startedAt: number,
  signal: AbortSignal,
): Promise<void> {
  const completion = await requestGenieCompletion(ids, question, { asyncComplete, signal });
  if (!isCurrent(gen)) return;
  if (completion.kind === 'answer') {
    settleTurn(gen, completion.response as GenieAnswerShape);
    return;
  }
  const { job } = completion;
  // Written synchronously when the 202 arrives, before the first status
  // poll: a reload from here on only polls this job.
  recordPhase(gen, { jobId: job.job_id });
  showJob(gen, job, false);
  const answer = settledGenieJob(job);
  if (answer) {
    settleTurn(gen, answer as GenieAnswerShape);
    return;
  }
  await followJob(gen, ids, question, job.job_id, startedAt, signal);
}

async function pollAndComplete(
  gen: number,
  ids: GenieTurnIds,
  question: string,
  record: Pick<PersistedTurnRecord, 'startedAt' | 'deep' | 'asyncComplete'>,
  deadline: number,
  signal: AbortSignal,
): Promise<void> {
  await pollGenieTurn(ids, {
    signal,
    deadline,
    deep: record.deep,
    onProgress: (progress) => {
      if (!isCurrent(gen)) return;
      patchTurn(gen, { progress, question, revealed: true });
    },
  });
  if (!isCurrent(gen) || completeRequestedFor === gen) return;
  completeRequestedFor = gen;
  // Written synchronously BEFORE the single complete call: a reload from here
  // on never completes a legacy turn again, and only rejoins a job turn.
  recordPhase(gen, { phase: 'completing' });
  patchTurn(gen, { phase: 'completing' });
  await completeTurn(gen, ids, question, record.asyncComplete, record.startedAt, signal);
}

/** A reloaded job turn: status polls only, or one complete that rejoins. */
async function resumeJob(gen: number, ids: GenieTurnIds, record: PersistedTurnRecord, signal: AbortSignal): Promise<void> {
  completeRequestedFor = gen;
  if (record.jobId) {
    await followJob(gen, ids, record.question, record.jobId, record.startedAt, signal);
    return;
  }
  await completeTurn(gen, ids, record.question, true, record.startedAt, signal);
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
  const { deep, completionJobs: asyncComplete, questionHash } = submitted;
  recordPhase(gen, { phase: 'polling', deep, ids, asyncComplete, questionHash: questionHash ?? undefined });
  patchTurn(gen, { phase: 'polling', deep: submitted.deep });
  // Held from the moment the ids exist, so a duplicated tab (which copies
  // sessionStorage) cannot resume this turn while this tab runs it.
  void holdLock(gen, ids.messageId);
  const record = active?.record;
  if (!record) return;
  await pollAndComplete(gen, ids, question, record, Date.now() + MAX_LIVE_WAIT_MS, signal);
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
    v: 2,
    question: trimmed,
    conversationId,
    surface,
    startedAt,
    deep: false,
    phase: 'submitting',
    asyncComplete: false,
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
    announcedDuringTurn: null,
  });
  runFreshTurn(gen, trimmed, conversationId, ctrl.signal).catch((err: unknown) => failTurn(gen, err));
  return true;
}

/** Replace Stopped note `stopId` with the server's confirmed copy; nothing
 *  when the note is gone (a reset or an actor change) or the call failed. */
function confirmStop(stopId: number, target: GenieTurnCancelTarget): void {
  void requestGenieTurnCancel(target).then((outcome) => {
    const index = snapshot.notes.findIndex((note) => note.kind === 'stopped' && note.stopId === stopId);
    if (outcome === null || index < 0) return;
    const reason = outcome === 'recorded' ? GENIE_STOP_RECORDED_REASON : GENIE_STOP_CONFIRMED_REASON;
    const notes = snapshot.notes.map((note, at) => (at === index ? { ...note, reason } : note));
    update(outcome === 'recorded' ? { notes, ...announcing(reason, snapshot.inFlight) } : { notes });
  });
}

/**
 * Stop waiting for the in-flight turn. The generation bump makes a reply
 * that still arrives land nowhere. Once a 202 has named the turn's job, the
 * server is also asked not to record the answer (audit `genie-03`); its
 * reply only rewrites the note. Returns the stopped question, or null when
 * nothing (visible) was stopped.
 */
export function stopGenieTurn(): string | null {
  const inFlight = snapshot.inFlight;
  if (!inFlight) return null;
  const question = inFlight.revealed ? (active?.question ?? inFlight.question) : '';
  const record = active?.record;
  const target: GenieTurnCancelTarget | null =
    record?.asyncComplete && record.jobId && record.ids && record.questionHash
      ? { ids: record.ids, jobId: record.jobId, questionHash: record.questionHash }
      : null;
  const stopId = inFlight.generation;
  generation += 1;
  controller?.abort();
  finishActive();
  update({
    inFlight: null,
    notes: question ? withNote('stopped', GENIE_STOPPED_REASON, question, stopId) : snapshot.notes,
  });
  if (target) confirmStop(stopId, target);
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
  if (record === LEGACY_RECORD) {
    // Written before completion jobs existed: never resumed, and its question
    // is not shown (no server check backs it).
    removeRecord();
    interrupt(interruptedReason('reload'), '');
    return;
  }
  const ids = record.ids;
  const age = Date.now() - record.startedAt;
  const polling = record.phase === 'polling' && age < MAX_LIVE_WAIT_MS;
  const jobCompleting = record.phase === 'completing' && record.asyncComplete && age < JOB_RESUME_WINDOW_MS;
  if (!ids || !(polling || jobCompleting)) {
    removeRecord();
    // Known residual (#3, wave 4): this note, like the other-tab note below
    // and the transcript key, shows the persisted question with no server
    // check, so it survives a reload across an actor change.
    interrupt(interruptedReason(record.phase === 'completing' ? 'completing' : 'reload'), record.question);
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
      phase: record.phase,
      startedAt: record.startedAt,
      deep: record.deep,
      progress: null,
      resumed: true,
      revealed: false,
    },
    announcement: '',
    announcedDuringTurn: null,
  });
  holdLock(gen, ids.messageId)
    .then((outcome) => {
      if (!isCurrent(gen)) return undefined;
      if (outcome.kind !== 'held') {
        finishActive();
        interrupt(interruptedReason(outcome.kind === 'busy' ? 'other-tab' : 'reload'), record.question);
        return undefined;
      }
      if (jobCompleting) return resumeJob(gen, ids, record, ctrl.signal);
      return pollAndComplete(gen, ids, record.question, record, record.startedAt + MAX_LIVE_WAIT_MS, ctrl.signal);
    })
    .catch((err: unknown) => failTurn(gen, err));
}

export function clearGenieTurnNotes(): void {
  if (snapshot.notes.length === 0) return;
  update({ notes: [] });
}

/** Say `text` through the surface announcer (Stop, prefill, action, copy).
 *  Said again when it repeats; said mid-turn until the stage moves on. */
export function announceGenie(text: string): void {
  if (!text) return;
  update(announcing(text, snapshot.inFlight));
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
  // The transcript too, with no surface mounted to do it (w2-genie-turn
  // review #0): a reset must never leave the previous actor's answers.
  clearGenieTurns();
  if (!snapshot.inFlight && snapshot.notes.length === 0 && snapshot.announcement === '') return;
  snapshot = clearedSnapshot();
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
  snapshot = clearedSnapshot();
  for (const listener of listeners) listener();
}
