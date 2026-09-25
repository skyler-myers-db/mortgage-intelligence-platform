/**
 * Live Genie ask lifecycle: submit → poll progress → complete.
 *
 * Replaces the single blocking `/api/genie/message` call on the interactive
 * surfaces so the UI can show what Genie is actually doing (stage, public
 * process steps, generated SQL) instead of a static "pending" card. The
 * governed answer still comes from the server's complete endpoint — the
 * browser never assembles an answer from progress crumbs.
 *
 * The lifecycle is split in two (audit 2026-09-21 `runtime-01`) so the
 * in-flight turn store (lib/genieInFlightTurn.ts) can persist the progress
 * identifiers between the steps and resume polling after a reload:
 *
 *   submitGenieTurn  the ONE submit POST. It carries no Idempotency-Key, so a
 *                    second POST would create a second Genie message: nothing
 *                    here, or in the store, ever re-sends it.
 *   pollGenieTurn    the progress loop, with the deadline passed in (a
 *                    resumed turn keeps its original start + MAX_LIVE_WAIT_MS).
 *
 * Completion (audit 2026-09-21 `genie-01`) runs as a server-side JOB when
 * the submit says `completion_jobs`: `requestGenieCompletion` asks for it
 * with `respond_async` (202 + the job's status) and `pollGenieJob` polls
 * `/message/status` until the job is terminal, showing the server's own
 * stages. The complete call is idempotent there (one job per turn), so a
 * timed-out request is re-sent once. Without jobs (an older server, or one
 * whose job table is not provisioned) it is today's single blocking call.
 * `askGenieLive` is the composition of all of it; it has no production
 * caller (the in-flight store composes the same steps with persistence).
 *
 * Failure contract: progress polling tolerates transient poll errors (the
 * turn keeps running server-side), except a 400 or 403, which fails at once:
 * the progress token is bound to the actor and the message, so neither
 * recovers by asking again. A terminal FAILED/CANCELLED/EXPIRED turn throws
 * `GenieLiveError` with the server's canned hint so callers render an honest
 * failure bubble without a wasted complete round-trip. Job polling adds 404
 * (not this actor's job) to the fatal set and ends on ANY terminal job other
 * than a succeeded one (failed, expired, or cancelled by the owner's Stop,
 * audit `genie-03`) with the server's canned hint.
 */

import type { GenieCompletionJobStatus, GenieSubmitResultWithJobs } from '../types/genieJobs';
import {
  ApiError,
  api,
  isAbortError,
  type GenieLiveProgress,
  type GenieResult,
} from './api';
import { genieJobsApi } from './apiClients/genieJobs';

/** Poll cadence for in-flight turns. Fast enough to feel live, slow enough
 * (~40/min) to sit well inside the dedicated `genie-progress` server
 * budget without touching the shared 30/min answer-path bucket. */
const PROGRESS_POLL_MS = 1_500;

/** Consecutive progress-poll failures tolerated before giving up. */
const MAX_CONSECUTIVE_POLL_FAILURES = 4;

/** Hard client-side ceiling; the server token expires at 15m regardless. */
export const MAX_LIVE_WAIT_MS = 5 * 60_000;

/** Job status poll cadence: the progress poll's, on the server's Lakebase
 *  budget (never the 30/min Genie budget). */
const JOB_POLL_MS = 1_500;

/** A completion job can be resumed after a reload for this long: just under
 *  the 15-minute progress token that authorizes every job call. */
export const JOB_RESUME_WINDOW_MS = 14 * 60_000;

/** The async complete answers at once with 202; a request that has not been
 *  answered in this long is re-sent ONCE (safe: one job per turn). */
const COMPLETE_ASYNC_TIMEOUT_MS = 30_000;

export class GenieLiveError extends Error {
  readonly hint: string | null;

  constructor(message: string, hint?: string | null) {
    super(message);
    this.name = 'GenieLiveError';
    this.hint = hint ?? null;
  }
}

/** The identifiers submit mints for a live turn; progress and complete need all three. */
export interface GenieTurnIds {
  conversationId: string;
  messageId: string;
  progressToken: string;
}

/** What the one submit POST returned: an inline answer, or a live turn to poll.
 *  `completionJobs`: the server will run the completion as a job.
 *  `questionHash`: the submit's 16-hex question label (what a cancel sends
 *  instead of the question), or null when the server sent none. */
export type GenieSubmitOutcome =
  | { kind: 'completed'; response: GenieResult }
  | ({ kind: 'live'; deep: boolean; completionJobs: boolean; questionHash: string | null } & GenieTurnIds);

/** The server's 16-hex question label (the audit ledger's `question_hash`). */
export const GENIE_QUESTION_LABEL_RE = /^[0-9a-f]{16}$/;

/** What the complete call returned: a job to poll, or (legacy / an older
 *  server) the governed answer itself. */
export type GenieCompletionOutcome =
  | { kind: 'job'; job: GenieCompletionJobStatus }
  | { kind: 'answer'; response: GenieResult };

type Sleep = (ms: number, signal?: AbortSignal) => Promise<void>;

export interface PollGenieTurnOptions {
  signal?: AbortSignal;
  onProgress?: (progress: GenieLiveProgress) => void;
  /** Epoch ms after which a non-terminal turn gives up. */
  deadline: number;
  /** The submit response's deep flag, stamped onto every progress update. */
  deep?: boolean;
  /** Test seam; production callers keep the default cadence. */
  pollMs?: number;
  sleep?: Sleep;
}

export interface AskGenieLiveOptions {
  signal?: AbortSignal;
  onProgress?: (progress: GenieLiveProgress) => void;
  /** Test seam; production callers keep the default cadence. */
  pollMs?: number;
  sleep?: Sleep;
}

function defaultSleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    // An already-aborted signal never fires its listener — reject now
    // instead of silently waiting out the full poll interval (QA M2).
    if (signal?.aborted) {
      reject(new DOMException('Aborted', 'AbortError'));
      return;
    }
    const timer = window.setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      window.clearTimeout(timer);
      reject(new DOMException('Aborted', 'AbortError'));
    };
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

/** A progress failure that asking again cannot fix: a bad request, or a
 *  token that is not this actor's (or not this message's). */
function isFatalProgressError(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 400 || err.status === 403);
}

/** A job poll failure that asking again cannot fix; 404 is a job that is not
 *  this actor's turn (or no longer exists). */
function isFatalJobError(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 400 || err.status === 403 || err.status === 404);
}

export function isGenieJobStatus(body: unknown): body is GenieCompletionJobStatus {
  return typeof body === 'object' && body !== null && (body as { kind?: unknown }).kind === 'genie_completion_job';
}

/** Submit one question. Never retried here: the POST is not idempotent. */
export async function submitGenieTurn(
  question: string,
  conversationId: string | null | undefined,
  signal?: AbortSignal,
): Promise<GenieSubmitOutcome> {
  const submitted = await api.genieSubmit(question, conversationId ?? null, signal);
  if (submitted.completed) {
    if (!submitted.response) {
      throw new GenieLiveError('Genie returned an empty completed submission.');
    }
    return { kind: 'completed', response: submitted.response };
  }
  const completionJobs = (submitted as GenieSubmitResultWithJobs).completion_jobs === true;
  const convId = submitted.conversation_id ?? '';
  const messageId = submitted.message_id ?? '';
  const token = submitted.progress_token ?? '';
  if (!convId || !messageId || !token) {
    throw new GenieLiveError('Genie submission is missing its progress identifiers.');
  }
  // Known at submit time (audit 2026-09-21 `genie-01` phase 0): a deep turn
  // spends 90-200 s inside the completion call, after Genie's own turn is
  // already terminal. The flag is stamped onto every progress update so the
  // rail can name that wait instead of claiming the answer is ready.
  return {
    kind: 'live',
    conversationId: convId,
    messageId,
    progressToken: token,
    deep: submitted.deep === true,
    completionJobs,
    questionHash:
      typeof submitted.question_hash === 'string' && GENIE_QUESTION_LABEL_RE.test(submitted.question_hash)
        ? submitted.question_hash
        : null,
  };
}

/** Poll a live turn until Genie's own turn is terminal. Resolves then; the
 *  caller makes the single complete call. */
export async function pollGenieTurn(ids: GenieTurnIds, options: PollGenieTurnOptions): Promise<void> {
  const { signal, onProgress, deadline, deep = false, pollMs = PROGRESS_POLL_MS, sleep = defaultSleep } = options;
  let consecutiveFailures = 0;
  for (;;) {
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
    let progress: GenieLiveProgress | null = null;
    try {
      progress = await api.genieProgress(ids.conversationId, ids.messageId, ids.progressToken, signal);
      consecutiveFailures = 0;
    } catch (err) {
      // The fetch wrapper converts aborts into ApiError{aborted:true}, so
      // check through the shared helper — a raw instanceof DOMException
      // test never fires here (QA M2) and would miscount an abort as a
      // transient poll failure.
      if (isAbortError(err)) throw err;
      if (isFatalProgressError(err)) throw err;
      consecutiveFailures += 1;
      if (consecutiveFailures >= MAX_CONSECUTIVE_POLL_FAILURES) throw err;
    }
    if (progress) {
      onProgress?.({ ...progress, deep });
      if (progress.failed) {
        throw new GenieLiveError(
          progress.error_hint ?? 'Genie could not complete this question.',
          progress.error_hint,
        );
      }
      if (progress.terminal) return;
    }
    if (Date.now() >= deadline) {
      throw new GenieLiveError(
        'Genie is taking longer than expected. The turn may still finish — ask again in a moment.',
      );
    }
    await sleep(pollMs, signal);
  }
}

export interface RequestGenieCompletionOptions {
  /** Ask for a job (`respond_async`); false is the legacy blocking call. */
  asyncComplete: boolean;
  signal?: AbortSignal;
  /** Test seam; production keeps COMPLETE_ASYNC_TIMEOUT_MS. */
  timeoutMs?: number;
}

type AsyncCompleteAttempt = { body: GenieCompletionJobStatus | GenieResult } | { timedOut: true };

/** One async complete attempt under its own timeout, linked to `signal`
 *  through a local controller (not AbortSignal.any). */
async function completeAsyncOnce(
  ids: GenieTurnIds,
  question: string,
  signal: AbortSignal | undefined,
  timeoutMs: number,
): Promise<AsyncCompleteAttempt> {
  if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
  const local = new AbortController();
  let timedOut = false;
  const onAbort = () => local.abort();
  signal?.addEventListener('abort', onAbort, { once: true });
  const timer = setTimeout(() => {
    timedOut = true;
    local.abort();
  }, timeoutMs);
  try {
    return { body: await genieJobsApi.genieCompleteAsync({ ...ids, question }, local.signal) };
  } catch (err) {
    if (timedOut && !signal?.aborted) return { timedOut: true };
    throw err;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', onAbort);
  }
}

/**
 * The complete call. With `asyncComplete` it asks for a job and discriminates
 * the body (a 202 job, or a 200 answer from an older server); a timeout, never
 * a user abort, re-sends it exactly once. Without it, today's blocking call.
 */
export async function requestGenieCompletion(
  ids: GenieTurnIds,
  question: string,
  options: RequestGenieCompletionOptions,
): Promise<GenieCompletionOutcome> {
  const { asyncComplete, signal, timeoutMs = COMPLETE_ASYNC_TIMEOUT_MS } = options;
  if (!asyncComplete) {
    const response = await api.genieComplete(ids.conversationId, ids.messageId, ids.progressToken, question, signal);
    return { kind: 'answer', response };
  }
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const outcome = await completeAsyncOnce(ids, question, signal, timeoutMs);
    if ('timedOut' in outcome) continue;
    return isGenieJobStatus(outcome.body)
      ? { kind: 'job', job: outcome.body }
      : { kind: 'answer', response: outcome.body };
  }
  throw new GenieLiveError('Genie did not accept the answer request in time. Ask the question again.');
}

export interface PollGenieJobOptions {
  signal?: AbortSignal;
  /** Epoch ms after which a still-running job gives up. */
  deadline: number;
  /** Every status the poll returns, the terminal one included. */
  onJob?: (job: GenieCompletionJobStatus) => void;
  pollMs?: number;
  sleep?: Sleep;
}

/** A terminal job's answer, a failure, or null while it still runs. Any
 *  terminal status but `succeeded` (failed, expired, cancelled) throws. */
export function settledGenieJob(job: GenieCompletionJobStatus): GenieResult | null {
  if (job.status === 'succeeded') {
    if (!job.response) throw new GenieLiveError('Genie returned an empty answer.');
    return job.response;
  }
  if (job.status !== 'queued' && job.status !== 'running') {
    throw new GenieLiveError(job.error_hint ?? 'Genie could not complete this question.', job.error_hint);
  }
  return null;
}

/** Poll a completion job until it is terminal; 400/403/404 are fatal at once. */
export async function pollGenieJob(
  ids: GenieTurnIds,
  question: string,
  jobId: string,
  options: PollGenieJobOptions,
): Promise<GenieResult> {
  const { signal, deadline, onJob, pollMs = JOB_POLL_MS, sleep = defaultSleep } = options;
  let consecutiveFailures = 0;
  for (;;) {
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
    let job: GenieCompletionJobStatus | null = null;
    try {
      job = await genieJobsApi.genieJobStatus({ ...ids, question }, jobId, signal);
      consecutiveFailures = 0;
    } catch (err) {
      if (isAbortError(err)) throw err;
      if (isFatalJobError(err)) throw err;
      consecutiveFailures += 1;
      if (consecutiveFailures >= MAX_CONSECUTIVE_POLL_FAILURES) throw err;
    }
    if (job) {
      onJob?.(job);
      const answer = settledGenieJob(job);
      if (answer) return answer;
    }
    if (Date.now() >= deadline) {
      throw new GenieLiveError(
        'Genie is taking longer than expected. The turn may still finish — ask again in a moment.',
      );
    }
    await sleep(pollMs, signal);
  }
}

export async function askGenieLive(
  question: string,
  conversationId: string | null | undefined,
  options: AskGenieLiveOptions = {},
): Promise<GenieResult> {
  const { signal, onProgress, pollMs, sleep } = options;
  const submitted = await submitGenieTurn(question, conversationId, signal);
  if (submitted.kind === 'completed') return submitted.response;
  const ids: GenieTurnIds = {
    conversationId: submitted.conversationId,
    messageId: submitted.messageId,
    progressToken: submitted.progressToken,
  };
  await pollGenieTurn(ids, {
    signal,
    onProgress,
    deadline: Date.now() + MAX_LIVE_WAIT_MS,
    deep: submitted.deep,
    pollMs,
    sleep,
  });
  const completion = await requestGenieCompletion(ids, question, {
    asyncComplete: submitted.completionJobs,
    signal,
  });
  if (completion.kind === 'answer') return completion.response;
  return (
    settledGenieJob(completion.job) ??
    pollGenieJob(ids, question, completion.job.job_id, {
      signal,
      deadline: Date.now() + JOB_RESUME_WINDOW_MS,
      pollMs,
      sleep,
    })
  );
}
