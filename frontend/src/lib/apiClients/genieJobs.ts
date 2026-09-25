/**
 * Genie completion-job clients (audit 2026-09-21 `genie-01`).
 *
 * NOT spread into `api` (api.ts spreads genieApi into the initial closure):
 * only the lazy Genie turn code imports this module, so the job clients cost
 * the initial bundle nothing. Every call is idempotent server-side (one job
 * per turn; a repeated cancel is a no-op), so they keep the transport's
 * default retry.
 */
import type { GenieCancelResult, GenieCompletionJobStatus } from '../../types/genieJobs';
import type { GenieResult } from '../apiTypes';
import { postJson } from '../apiTransport';

/** The identifiers and prompt of record every job call carries. */
export interface GenieJobTurn {
  conversationId: string;
  messageId: string;
  progressToken: string;
  question: string;
}

/** The ids a cancel names; never the question. */
export type GenieCancelTurn = Omit<GenieJobTurn, 'question'>;

interface GenieCancelBody {
  conversation_id: string;
  message_id: string;
  progress_token: string;
  job_id: string;
  question_hash: string;
}

interface GenieJobTurnBody {
  conversation_id: string;
  message_id: string;
  progress_token: string;
  question: string;
}

function turnBody(turn: GenieJobTurn): GenieJobTurnBody {
  return {
    conversation_id: turn.conversationId,
    message_id: turn.messageId,
    progress_token: turn.progressToken,
    question: turn.question,
  };
}

export const genieJobsApi = {
  /** 202 + the job's status; an older server ignores `respond_async` and
   *  answers 200 with the governed answer itself. */
  genieCompleteAsync: (turn: GenieJobTurn, signal?: AbortSignal) =>
    postJson<GenieCompletionJobStatus | GenieResult, GenieJobTurnBody & { respond_async: true }>(
      '/api/genie/message/complete',
      { ...turnBody(turn), respond_async: true },
      signal,
    ),

  /** One poll of the caller's own job (a POST: the token never lands in a URL). */
  genieJobStatus: (turn: GenieJobTurn, jobId: string, signal?: AbortSignal) =>
    postJson<GenieCompletionJobStatus, GenieJobTurnBody & { job_id: string }>(
      '/api/genie/message/status',
      { ...turnBody(turn), job_id: jobId },
      signal,
    ),

  /** The owner's Stop on a job turn (audit genie-03). Carries the submit's
   *  16-hex question label, never the question. `retry: 'default'` is
   *  explicit: the call is idempotent server-side, and an unkeyed POST no
   *  longer defaults to it. */
  genieCancel: (ids: GenieCancelTurn, jobId: string, questionHash: string, signal?: AbortSignal) =>
    postJson<GenieCancelResult, GenieCancelBody>(
      '/api/genie/message/cancel',
      {
        conversation_id: ids.conversationId,
        message_id: ids.messageId,
        progress_token: ids.progressToken,
        job_id: jobId,
        question_hash: questionHash,
      },
      signal,
      undefined,
      { retry: 'default' },
    ),
};
