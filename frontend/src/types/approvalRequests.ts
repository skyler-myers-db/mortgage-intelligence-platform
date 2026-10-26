import type { ApiRequest, ApiResponse } from './api.gen';
/**
 * Maker-checker approval requests (audit flow-02 / shell-06, D-approval-flow-c):
 * hand types named after backend/schemas/approval_request.py, each paired with
 * its schema in wireContract.leads.check.ts. A signed-in non-approver asks an
 * approver to review named borrowers with a governed "Reason for the request"
 * (the wire field is `rationale`; the Lakebase column keeps its name `note`).
 * Request state is audit-free Lakebase workflow state, derived per borrower.
 */

export type ApprovalRequestScope = 'open' | 'mine';

export type ApprovalRequestSkipReason = 'not_found' | 'not_contactable' | 'already_decided' | 'already_requested';

export type ApprovalRequestRowState = 'open' | 'approved' | 'rejected' | 'decided_outside' | 'withdrawn' | 'expired';

/** POST /outreach/approval-requests. */
export type ApprovalRequestCreate = ApiRequest<'ApprovalRequestCreate'>;

export type ApprovalRequestSkip = ApiResponse<'ApprovalRequestSkip'>;

export type ApprovalRequestCreated = ApiResponse<'ApprovalRequestCreated'>;

export type ApprovalRequestRow = ApiResponse<'ApprovalRequestRow'>;

export type ApprovalRequestBatchView = ApiResponse<'ApprovalRequestBatchView'>;

export type ApprovalRequestList = ApiResponse<'ApprovalRequestList'>;

export type ApprovalRequestWithdrawn = ApiResponse<'ApprovalRequestWithdrawn'>;

/**
 * The withdraw POST takes no body (the operation's body is `never`); the
 * transport sends `{}` with the JSON content type the route requires.
 */
export type ApprovalRequestWithdrawBody = never;
