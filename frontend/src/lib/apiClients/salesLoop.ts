/**
 * The sales-loop writes (W5d w5-sales-loop): assignment and distribution
 * release (the Undo behind the assign / distribute toasts, audit states-07
 * item 1) and the Distribution Board's dry run and commit (wow-power-5 step 2).
 *
 * LAZY on purpose: api.ts and apiClients/sales.ts are in the initial closure
 * (through AppContext), so these clients are imported only by
 * lib/mutations/salesLoop, which the Undo toast and the Board load on demand.
 *
 * Every write carries a `request_id`; a retry of the same intent reuses it and
 * the server replays instead of writing twice. The dry run carries none and
 * writes nothing (no Lakebase row, no audit row).
 */
import type { LeadAssignment } from '../../types';
import { postJson } from '../apiTransport';
import type { DistributeLeadsRequest, DistributeLeadsResponse } from './sales';

/** The `/api/leads/{id}/assignment/release` body. */
export interface AssignmentReleaseRequest {
  assignment_id: string;
  request_id: string;
}

/** Why a release did not hand the lead back to the officer it replaced. */
export type RestoreSkippedReason =
  | 'previous_assignee_inactive'
  | 'previous_assignee_out_of_scope'
  | 'lead_not_approved'
  | 'lead_not_contactable';

/** The `/api/leads/{id}/assignment/release` response. */
export interface AssignmentReleaseResponse {
  released: LeadAssignment;
  restored: LeadAssignment | null;
  restore_skipped_reason: RestoreSkippedReason | null;
  audit_event_id: string;
}

/** The `/api/sales/distribute/release` body. */
export interface DistributionReleaseRequest {
  distribution_request_id: string;
  request_id: string;
}

/** The `/api/sales/distribute/release` response. */
export interface DistributionReleaseResponse {
  released_count: number;
  restored_count: number;
  skipped_started_count: number;
  skipped_not_active_count: number;
  restore_skipped_count: number;
  released: LeadAssignment[];
  restored: LeadAssignment[];
  audit_event_ids: string[];
}

/** What the Board plans across: the roster's loan officers and an optional pool. */
export interface DistributionPlanInput {
  borrowerIds: string[];
  loEmails: string[];
}

function boardBody(input: DistributionPlanInput): DistributeLeadsRequest {
  return {
    borrower_ids: input.borrowerIds,
    lo_emails: input.loEmails,
    strategy: 'score_balanced',
    expires_in_hours: 24,
  };
}

export const salesLoopApi = {
  releaseAssignment: (borrowerId: string, body: AssignmentReleaseRequest, signal?: AbortSignal) =>
    postJson<AssignmentReleaseResponse, AssignmentReleaseRequest>(
      `/api/leads/${encodeURIComponent(borrowerId)}/assignment/release`,
      body,
      signal,
    ),

  releaseDistribution: (body: DistributionReleaseRequest, signal?: AbortSignal) =>
    postJson<DistributionReleaseResponse, DistributionReleaseRequest>(
      '/api/sales/distribute/release',
      body,
      signal,
    ),

  /** The Board's dry run: plans on live counts, writes nothing. */
  previewDistribution: (input: DistributionPlanInput, signal?: AbortSignal) =>
    postJson<DistributeLeadsResponse, DistributeLeadsRequest>(
      '/api/sales/distribute',
      { ...boardBody(input), dry_run: true },
      signal,
    ),

  /** Commit the previewed plan; a 409 `distribution_plan_changed` writes nothing. */
  commitDistribution: (
    input: DistributionPlanInput & { fingerprint: string; requestId: string },
    signal?: AbortSignal,
  ) =>
    postJson<DistributeLeadsResponse, DistributeLeadsRequest>(
      '/api/sales/distribute',
      { ...boardBody(input), expected_plan_fingerprint: input.fingerprint, request_id: input.requestId },
      signal,
    ),
};
