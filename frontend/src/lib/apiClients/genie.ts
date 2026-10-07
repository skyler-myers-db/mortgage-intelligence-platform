/**
 * Genie endpoint clients: the synchronous ask, the async submit/progress/
 * complete lifecycle, session history, governed actions, and feedback.
 *
 * Members moved verbatim out of `api.ts`, which spreads them back into the
 * exported `api` object in the original order. Consumers import `api` from
 * `../api` — never from this module.
 */
import type {
  GenieActionResult,
  GenieActionSuggestion,
  GenieRefusalReason,
  GenieSessionDetail,
  GenieSessionSummary,
  GenieStartResult,
} from '../../types';
import type {
  GenieResult,
  GenieFeedbackResult,
  GenieRefusalReportResult,
  GenieSubmitResult,
  GenieLiveProgress,
} from '../apiTypes';
import { _newRequestId, getJson, postJson } from '../apiTransport';
import type { GenieCompleteAsyncResult } from './genieJobs';

/** The `/api/genie/message` and `/api/genie/message/submit` body. */
export interface GenieMessageRequest {
  question: string;
  conversation_id?: string | null;
}

/** The `/api/genie/message/progress` body. */
export interface GenieProgressRequest {
  conversation_id: string;
  message_id: string;
  progress_token: string;
}

/** The synchronous `/api/genie/message/complete` body (no `respond_async`). */
export interface GenieCompleteAsyncRequest {
  conversation_id: string;
  message_id: string;
  progress_token: string;
  question: string;
}

/**
 * The `/api/genie/start` body; its wire schema is an untyped object or null.
 * An object type alias, like the inline literal it replaces, so it keeps the
 * implicit index signature an open `{ [key: string]: unknown }` body needs.
 */
export type GenieStartBody = {
  context: Record<string, never>;
};

/** The `/api/genie/sessions` response: past conversations, newest first. */
export interface GenieSessionListResponse {
  sessions: GenieSessionSummary[];
}

/** The `/api/genie/actions` body: exactly the fields `genieAction` sends. */
export interface GenieActionRequest {
  action_type: GenieActionSuggestion['action_type'];
  conversation_id: string | null;
  message_id: string | null;
  question_hash: string | null;
  borrower_ids: NonNullable<GenieActionSuggestion['borrower_ids']>;
  criteria: NonNullable<GenieActionSuggestion['criteria']>;
  route: NonNullable<GenieActionSuggestion['route']> | null;
  request_id: NonNullable<GenieActionSuggestion['request_id']>;
  confirmed: true;
  confirmation_token: NonNullable<GenieActionSuggestion['confirmation_token']> | null;
}

/** The `/api/genie/feedback` body (the request id travels as the Idempotency-Key). */
export interface GenieFeedbackRequest {
  conversation_id: string;
  message_id: string;
  helpful: boolean;
  comment?: string;
}

/** The `/api/genie/refusal-report` body: hash-only, never the question. */
export interface GenieRefusalReportRequest {
  question_hash: string;
  refusal_reason: GenieRefusalReason;
  conversation_id: string | null;
  message_id: string | null;
}

export const genieApi = {
  genie: (question: string, conversationId?: string | null, signal?: AbortSignal) =>
    postJson<GenieResult, GenieMessageRequest>(
      '/api/genie/message',
      { question, conversation_id: conversationId ?? null },
      signal,
    ),

  /** Creates a Genie message and carries no Idempotency-Key: re-sent only
   *  after a 429 the middleware returned before the handler ran. */
  genieSubmit: (question: string, conversationId?: string | null, signal?: AbortSignal) =>
    postJson<GenieSubmitResult, GenieMessageRequest>(
      '/api/genie/message/submit',
      { question, conversation_id: conversationId ?? null },
      signal,
      undefined,
      { retry: 'rejected-only' },
    ),

  genieProgress: (
    conversationId: string,
    messageId: string,
    progressToken: string,
    signal?: AbortSignal,
  ) =>
    postJson<GenieLiveProgress, GenieProgressRequest>(
      '/api/genie/message/progress',
      { conversation_id: conversationId, message_id: messageId, progress_token: progressToken },
      signal,
    ),

  genieComplete: (
    conversationId: string,
    messageId: string,
    progressToken: string,
    question: string,
    signal?: AbortSignal,
  ) =>
    // The wire ok is GenieMessageResponse | GenieCompletionJobStatus
    // (quality-04 P2): the caller narrows (genieAsk.requestGenieCompletion).
    postJson<GenieCompleteAsyncResult, GenieCompleteAsyncRequest>(
      '/api/genie/message/complete',
      {
        conversation_id: conversationId,
        message_id: messageId,
        progress_token: progressToken,
        question,
      },
      signal,
    ),

  genieStart: (signal?: AbortSignal) =>
    postJson<GenieStartResult, GenieStartBody>(
      '/api/genie/start',
      { context: {} },
      signal,
    ),

  /** Past Genie conversations for the current actor, newest first. The
   *  History menu treats any failure (404 on an older backend, 5xx, network)
   *  as "History unavailable" — it is an optional affordance, never a
   *  blocking dependency of the chat. */
  genieSessions: (signal?: AbortSignal) =>
    getJson<GenieSessionListResponse>('/api/genie/sessions', signal).then(
      (body) => body.sessions ?? [],
    ),

  /** Full transcript of one past conversation, in the same
   *  `{question, response}` turn shape the local transcript store uses. */
  genieSession: (conversationId: string, signal?: AbortSignal) =>
    getJson<GenieSessionDetail>(
      `/api/genie/sessions/${encodeURIComponent(conversationId)}`,
      signal,
    ),

  genieAction: (
    action: GenieActionSuggestion & {
      conversation_id?: string | null;
      message_id?: string | null;
      question_hash?: string | null;
    },
    signal?: AbortSignal,
  ) =>
    postJson<GenieActionResult, GenieActionRequest>(
      '/api/genie/actions',
      {
        action_type: action.action_type,
        conversation_id: action.conversation_id ?? null,
        message_id: action.message_id ?? null,
        question_hash: action.question_hash ?? null,
        borrower_ids: action.borrower_ids ?? [],
        criteria: action.criteria ?? {},
        route: action.route ?? null,
        request_id: action.request_id ?? _newRequestId(),
        confirmed: true,
        confirmation_token: action.confirmation_token ?? null,
      },
      signal,
    ),

  /** Record replay-safe thumbs-up/down feedback on a Genie answer. */
  genieFeedback: (
    payload: {
      conversation_id: string;
      message_id: string;
      helpful: boolean;
      comment?: string;
      request_id?: string;
    },
    signal?: AbortSignal,
  ) => {
    const { request_id: requestId, ...body } = payload;
    return postJson<GenieFeedbackResult, GenieFeedbackRequest>(
      '/api/genie/feedback',
      body,
      signal,
      { 'Idempotency-Key': requestId ?? crypto.randomUUID() },
    );
  },

  /**
   * Report a governed refusal as a false positive. Hash-only by contract
   * (audit 2026-09-21 `genie-05`): the body carries the coarse family and the
   * `refusal_report_hash` the refused turn returned, never the question.
   */
  genieRefusalReport: (
    payload: {
      question_hash: string;
      refusal_reason: GenieRefusalReason;
      conversation_id?: string | null;
      message_id?: string | null;
    },
    signal?: AbortSignal,
  ) =>
    postJson<GenieRefusalReportResult, GenieRefusalReportRequest>(
      '/api/genie/refusal-report',
      {
        question_hash: payload.question_hash,
        refusal_reason: payload.refusal_reason,
        conversation_id: payload.conversation_id ?? null,
        message_id: payload.message_id ?? null,
      },
      signal,
    ),
};
