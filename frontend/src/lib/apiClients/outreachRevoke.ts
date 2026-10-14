/**
 * The revoke client (audit flow-v2): an approver revokes a borrower's
 * current, unbound approval while its outreach is still none or queued.
 * POST /api/outreach/revoke appends a revoke decision row and its
 * OUTREACH_REVOKE audit row in one Lakebase transaction; the approve row is
 * never changed. A refused revoke answers 409 `{detail, reason}` (one of
 * the six REVOKE_REFUSAL_REASONS) and writes nothing; a governed-text
 * refusal of the rationale is a 422 that WAS recorded (GOVERNED_TEXT_REFUSED,
 * owner ruling O1).
 *
 * Imported only by the lazy revoke mutation hook and panel, never spread
 * into `api` (api.ts sits in the initial closure).
 */
import { postJson } from '../apiTransport';

/** The `/api/outreach/revoke` body (backend/schemas/outreach_revoke.py). */
export interface OutreachRevokeRequest {
  borrower_id: string;
  /** The approval being revoked: it must still be the borrower's current decision. */
  approval_id: string;
  /** Why: required, 1-500 characters, without personal details (governed). */
  rationale: string;
  /** One per intent, reused on a retry (the server replays the stored answer). */
  request_id: string;
}

/** The revoke row and what it superseded. */
export interface OutreachRevokeResponse {
  revoked: boolean;
  approval_id: string;
  revoked_approval_id: string;
  audit_event_id: string;
  released_assignment_id: string | null;
}

/** The 409 `reason` codes (backend/services/outreach_revoke.REVOKE_REFUSED_DETAIL). */
export const REVOKE_REFUSAL_REASONS = [
  'not_current',
  'campaign_bound',
  'dispositioned',
  'outcome_recorded',
  'delivered',
  'assignment_worked',
] as const;
export type RevokeRefusalReason = (typeof REVOKE_REFUSAL_REASONS)[number];

export function isRevokeRefusalReason(value: unknown): value is RevokeRefusalReason {
  return typeof value === 'string' && (REVOKE_REFUSAL_REASONS as readonly string[]).includes(value);
}

export function revokeApproval(body: OutreachRevokeRequest, signal?: AbortSignal): Promise<OutreachRevokeResponse> {
  return postJson<OutreachRevokeResponse, OutreachRevokeRequest>('/api/outreach/revoke', body, signal);
}
