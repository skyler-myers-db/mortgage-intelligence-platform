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
export interface ApprovalRequestCreate {
  borrower_ids: string[];
  /** The governed justification, at most 500 characters ("Reason for the request"). */
  rationale: string;
  /** One UUID per intent: a retry of the same content replays the stored answer. */
  request_key: string;
}

export interface ApprovalRequestSkip {
  borrower_id: string;
  reason: ApprovalRequestSkipReason;
}

export interface ApprovalRequestCreated {
  batch_id: string;
  audit_event_id: string;
  requested: string[];
  skipped: ApprovalRequestSkip[];
}

export interface ApprovalRequestRow {
  borrower_id: string;
  state: ApprovalRequestRowState;
  approval_id: string | null;
}

export interface ApprovalRequestBatchView {
  batch_id: string;
  /** A readable label derived from the requester's identity; never an email. */
  requested_by_display: string | null;
  is_mine: boolean;
  rationale: string;
  created_at: string;
  requested_by: string | null;
  rows: ApprovalRequestRow[];
}

export interface ApprovalRequestList {
  scope: ApprovalRequestScope;
  batches: ApprovalRequestBatchView[];
}

export interface ApprovalRequestWithdrawn {
  batch_id: string;
  withdrawn_now: number;
  already_closed: number;
  audit_event_id: string | null;
}

/**
 * The withdraw POST takes no body (the operation's body is `never`); the
 * transport sends `{}` with the JSON content type the route requires.
 */
export type ApprovalRequestWithdrawBody = never;
