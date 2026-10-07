/**
 * Maker-checker approval-request clients (audit flow-02 / shell-06,
 * D-approval-flow-c): the audit-free list, the create (APPROVAL_REQUESTED, or
 * APPROVAL_REQUEST_REFUSED and a 409 when nothing is requestable) and the
 * requester's withdraw (APPROVAL_REQUEST_WITHDRAWN).
 *
 * Imported directly by the Lead Queue's request hook and the lazy request
 * form and panels, never spread into `api` (api.ts sits in the initial
 * closure; precedent: leadFacets, rateScenario). Nothing here drafts: a
 * request never calls /outreach/draft.
 */
import type {
  ApprovalRequestCreate,
  ApprovalRequestCreated,
  ApprovalRequestList,
  ApprovalRequestScope,
  ApprovalRequestWithdrawBody,
  ApprovalRequestWithdrawn,
} from '../../types/approvalRequests';
import { ApiError, getJson, postJson } from '../apiTransport';

/**
 * The withdraw POST has no body (its operation's body is `never`); the
 * transport serializes an absent body as `{}` with the JSON content type the
 * route requires.
 */
const NO_BODY = undefined as ApprovalRequestWithdrawBody;

export const approvalRequestsApi = {
  /** GET: audit-free Lakebase workflow state; approvers read `open`, everyone else `mine`. */
  list: (scope: ApprovalRequestScope, signal?: AbortSignal) =>
    getJson<ApprovalRequestList>(`/api/outreach/approval-requests?scope=${scope}`, signal),
  create: (body: ApprovalRequestCreate, signal?: AbortSignal) =>
    postJson<ApprovalRequestCreated, ApprovalRequestCreate>('/api/outreach/approval-requests', body, signal),
  withdraw: (batchId: string, signal?: AbortSignal) =>
    postJson<ApprovalRequestWithdrawn, ApprovalRequestWithdrawBody>(
      `/api/outreach/approval-requests/${encodeURIComponent(batchId)}/withdraw`,
      NO_BODY,
      signal,
    ),
};

/** The router's two string 409 details (backend/api/approval_requests.py), matched exactly. */
export const APPROVER_DECIDES_DIRECTLY = 'Approvers decide directly; open the review instead.';
export const REQUEST_KEY_CONFLICT = 'request_key already belongs to a different approval request';
/** The governed-text refusal's 422 detail on `rationale` (owner ruling O1: the refusal is recorded). */
export const RATIONALE_GOVERNED_REFUSAL = 'rationale failed the governed text policy';

/**
 * Why a create failed, classified explicitly (W5d C3 / O1):
 * - `actor_changed`: the signed-in user changed; nothing was recorded.
 * - `governed`: the governed text screen refused the reason; that refusal IS
 *   recorded (GOVERNED_TEXT_REFUSED).
 * - `invalid`: any other 422 (a schema refusal, the redacted-length cap, an
 *   audit payload the ledger refused): nothing was recorded.
 * - `approver`: the unaudited approver 409 (can_approve went stale).
 * - `key_conflict`: the request key belongs to another request (unaudited).
 * - `none_requestable`: the router's third 409, the zero-eligible refusal,
 *   the only one with an object detail (the transport drops it, so it is
 *   told apart by elimination, never by the fallback message); it wrote
 *   APPROVAL_REQUEST_REFUSED.
 * - `other`: everything else (describeApiError's body).
 */
export type ApprovalRequestFailure =
  | 'actor_changed'
  | 'governed'
  | 'invalid'
  | 'approver'
  | 'key_conflict'
  | 'none_requestable'
  | 'other';

export function approvalRequestFailure(error: unknown): ApprovalRequestFailure {
  if (!(error instanceof ApiError)) return 'other';
  if (error.reason === 'actor_changed') return 'actor_changed';
  if (error.status === 422) return error.message === RATIONALE_GOVERNED_REFUSAL ? 'governed' : 'invalid';
  if (error.status !== 409) return 'other';
  if (error.message === APPROVER_DECIDES_DIRECTLY) return 'approver';
  if (error.message === REQUEST_KEY_CONFLICT) return 'key_conflict';
  return 'none_requestable';
}
