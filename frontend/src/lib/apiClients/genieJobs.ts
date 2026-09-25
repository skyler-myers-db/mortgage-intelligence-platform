/**
 * Genie completion-job clients (audit 2026-09-21 `genie-01`).
 *
 * NOT spread into `api` (api.ts spreads genieApi into the initial closure):
 * only the lazy Genie turn code imports this module, so the job clients cost
 * the initial bundle nothing. Both calls are idempotent server-side (one job
 * per turn), so they keep the transport's default retry.
 */
import type { GenieCompletionJobStatus } from '../../types/genieJobs';
import type { GenieResult } from '../apiTypes';
import { postJson } from '../apiTransport';

/** The identifiers and prompt of record every job call carries. */
export interface GenieJobTurn {
  conversationId: string;
  messageId: string;
  progressToken: string;
  question: string;
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
};
