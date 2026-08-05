/**
 * Live Genie ask lifecycle: submit → poll progress → complete.
 *
 * Replaces the single blocking `/api/genie/message` call on the interactive
 * surfaces so the UI can show what Genie is actually doing (stage, public
 * process steps, generated SQL) instead of a static "pending" card. The
 * governed answer still comes from the server's complete endpoint — the
 * browser never assembles an answer from progress crumbs.
 *
 * Failure contract: progress polling tolerates transient poll errors (the
 * turn keeps running server-side); a terminal FAILED/CANCELLED/EXPIRED turn
 * throws `GenieLiveError` with the server's canned hint so callers render
 * an honest failure bubble without a wasted complete round-trip.
 */

import { api, isAbortError, type GenieLiveProgress, type GenieResult } from './api';

/** Poll cadence for in-flight turns. Fast enough to feel live, slow enough
 * (~40/min) to sit well inside the dedicated `genie-progress` server
 * budget without touching the shared 30/min answer-path bucket. */
const PROGRESS_POLL_MS = 1_500;

/** Consecutive progress-poll failures tolerated before giving up. */
const MAX_CONSECUTIVE_POLL_FAILURES = 4;

/** Hard client-side ceiling; the server token expires at 15m regardless. */
const MAX_LIVE_WAIT_MS = 5 * 60_000;

export class GenieLiveError extends Error {
  readonly hint: string | null;

  constructor(message: string, hint?: string | null) {
    super(message);
    this.name = 'GenieLiveError';
    this.hint = hint ?? null;
  }
}

export interface AskGenieLiveOptions {
  signal?: AbortSignal;
  onProgress?: (progress: GenieLiveProgress) => void;
  /** Test seam; production callers keep the default cadence. */
  pollMs?: number;
  sleep?: (ms: number, signal?: AbortSignal) => Promise<void>;
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

export async function askGenieLive(
  question: string,
  conversationId: string | null | undefined,
  options: AskGenieLiveOptions = {},
): Promise<GenieResult> {
  const { signal, onProgress, pollMs = PROGRESS_POLL_MS, sleep = defaultSleep } = options;

  const submitted = await api.genieSubmit(question, conversationId ?? null, signal);
  if (submitted.completed) {
    if (!submitted.response) {
      throw new GenieLiveError('Genie returned an empty completed submission.');
    }
    return submitted.response;
  }
  const convId = submitted.conversation_id ?? '';
  const messageId = submitted.message_id ?? '';
  const token = submitted.progress_token ?? '';
  if (!convId || !messageId || !token) {
    throw new GenieLiveError('Genie submission is missing its progress identifiers.');
  }

  const deadline = Date.now() + MAX_LIVE_WAIT_MS;
  let consecutiveFailures = 0;
  for (;;) {
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
    let progress: GenieLiveProgress | null = null;
    try {
      progress = await api.genieProgress(convId, messageId, token, signal);
      consecutiveFailures = 0;
    } catch (err) {
      // The fetch wrapper converts aborts into ApiError{aborted:true}, so
      // check through the shared helper — a raw instanceof DOMException
      // test never fires here (QA M2) and would miscount an abort as a
      // transient poll failure.
      if (isAbortError(err)) throw err;
      consecutiveFailures += 1;
      if (consecutiveFailures >= MAX_CONSECUTIVE_POLL_FAILURES) throw err;
    }
    if (progress) {
      onProgress?.(progress);
      if (progress.failed) {
        throw new GenieLiveError(
          progress.error_hint ?? 'Genie could not complete this question.',
          progress.error_hint,
        );
      }
      if (progress.terminal) break;
    }
    if (Date.now() >= deadline) {
      throw new GenieLiveError(
        'Genie is taking longer than expected. The turn may still finish — ask again in a moment.',
      );
    }
    await sleep(pollMs, signal);
  }

  return api.genieComplete(convId, messageId, token, question, signal);
}
