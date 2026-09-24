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
 * `askGenieLive` stays as the composition of the two plus the single
 * `api.genieComplete` call.
 *
 * Failure contract: progress polling tolerates transient poll errors (the
 * turn keeps running server-side), except a 400 or 403, which fails at once:
 * the progress token is bound to the actor and the message, so neither
 * recovers by asking again. A terminal FAILED/CANCELLED/EXPIRED turn throws
 * `GenieLiveError` with the server's canned hint so callers render an honest
 * failure bubble without a wasted complete round-trip.
 */

import {
  ApiError,
  api,
  isAbortError,
  type GenieLiveProgress,
  type GenieResult,
} from './api';

/** Poll cadence for in-flight turns. Fast enough to feel live, slow enough
 * (~40/min) to sit well inside the dedicated `genie-progress` server
 * budget without touching the shared 30/min answer-path bucket. */
const PROGRESS_POLL_MS = 1_500;

/** Consecutive progress-poll failures tolerated before giving up. */
const MAX_CONSECUTIVE_POLL_FAILURES = 4;

/** Hard client-side ceiling; the server token expires at 15m regardless. */
export const MAX_LIVE_WAIT_MS = 5 * 60_000;

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

/** What the one submit POST returned: an inline answer, or a live turn to poll. */
export type GenieSubmitOutcome =
  | { kind: 'completed'; response: GenieResult }
  | ({ kind: 'live'; deep: boolean } & GenieTurnIds);

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
  return api.genieComplete(ids.conversationId, ids.messageId, ids.progressToken, question, signal);
}
