/**
 * One row's governed approve or reject write, with the per-row report a bulk
 * run lists (audit tables-07, flow-03). Split out of useLeadApprovalActions
 * (file-size gate): the hook hands over the state it owns through
 * DecisionWriteDeps, so every guard below still reads that state
 * synchronously.
 *
 * Both writes keep the hook's invariants: a decision already on the wire is
 * a 'duplicate' (the R5-04 latch or the MutationCache), the approver /
 * campaign-binding gate runs before any draft or POST, one request_id per
 * intent, pessimistic (the row reads Approved / Rejected only after the POST
 * returned true), and a bulk row reports into its run instead of a toast.
 */
import type { LeadSummary } from '../../types';
import { clientFailureReason } from '../../lib/apiTransport';
import { markUnrecordedWrite } from '../../lib/sessionStatus';
import type { ApproveResult, OutreachDraftResult, RejectResult, ReviewMode } from '../../lib/apiTypes';
import {
  decisionFailure,
  OfferNotPreviewedError,
  type ApproveLeadVariables,
  type RejectLeadVariables,
} from '../../lib/mutations/outreach';
import { intentFingerprint, type IntentRequestIds } from '../../lib/mutations/requestIds';
import type { CampaignBinding } from './LeadTable.logic';
import type { RejectReasonCode } from './LeadTable.types';
import type { BulkRowReport } from './useLeadBulkRun';
import { toastWriteFailure, toastWriteRefusal } from './leadWriteFailureToast';

/** A single-row approval with no reviewed draft: refused before any draft or POST. */
const REVIEWED_DRAFT_REQUIRED = 'Approval requires the reviewed draft.';
/** A bulk row whose drafted offer the gate never previewed (OfferNotPreviewedError). */
const OFFER_NOT_PREVIEWED = 'Offer changed since preview — review individually';

/** What a decision certifies about its row, snapshotted when a bulk run starts. */
export interface DecisionSnapshot {
  evidenceIds: readonly string[];
  offerCode: string | null;
}

export interface ApproveExtras {
  rationale?: string | null;
  bulk_id?: string | null;
  bulk_rationale?: string | null;
  suppressInvalidation?: boolean;
  /** A bulk row: the evidence and offer read when the run started, never live mid-run. */
  snapshot?: DecisionSnapshot;
  /** A single-row review's mode ('triage' from the Triage deck); default 'individual'. */
  reviewMode?: Extract<ReviewMode, 'individual' | 'triage'>;
  /** A bulk run: the offers its gate previewed (the in-run offer check). */
  coveredOfferCodes?: ReadonlySet<string> | null;
}

/** One bulk rejection run's shared inputs, as each of its rows sends them. */
export interface BulkRejectRow {
  bulkId: string;
  reasonCode: RejectReasonCode;
  note: string;
  snapshot?: DecisionSnapshot;
}

export interface DecisionWriteDeps {
  leadsById: ReadonlyMap<string, LeadSummary>;
  campaignBinding: CampaignBinding | null;
  approve: (variables: ApproveLeadVariables) => Promise<ApproveResult>;
  reject: (variables: RejectLeadVariables) => Promise<RejectResult>;
  requestIds: IntentRequestIds;
  /** A decision for this row is on the wire (this mount's latch or the MutationCache). */
  isInFlight: (borrowerId: string) => boolean;
  /** The approver / campaign-binding gate; `report` false keeps a bulk row's refusal out of the toasts. */
  passesGate: (noun: 'approval' | 'rejection', subject: string, report: boolean) => boolean;
  /** The R5-04 per-row latch. */
  latch: (borrowerId: string, inFlight: boolean) => void;
  /** The write returned ok: record the decision and its receipt. */
  onDecided: (borrowerId: string, decision: 'approved' | 'rejected', auditEventId: string | null) => void;
}

function released<T>(deps: DecisionWriteDeps, borrowerId: string, report: Promise<T>): Promise<T> {
  // Released before the caller sees the outcome (this reaction is first).
  const release = () => deps.latch(borrowerId, false);
  void report.then(release, release);
  return report;
}

/**
 * approveLead with the per-row report a bulk run lists: the outcome
 * (session_expired told apart) and the server's reason. A bulk row reports
 * into the run's result, not the table's single alert, and certifies the
 * evidence and offer snapshotted when the run started.
 *
 * The review mode it records: a bulk row is 'bulk_sample' when its copy
 * was previewed (a reviewedDraft), else 'bulk_cohort'; a single row is the
 * review's own mode. A single row with no reviewed draft is refused here,
 * before any draft or POST: nothing approves copy nobody was shown.
 */
export function approveWithReport(
  deps: DecisionWriteDeps,
  borrowerId: string,
  signal: AbortSignal | undefined,
  extras: ApproveExtras,
  reviewedDraft: OutreachDraftResult | null,
): Promise<BulkRowReport> {
  if (deps.isInFlight(borrowerId)) return Promise.resolve({ outcome: 'duplicate', message: null });
  const inBulk = Boolean(extras.bulk_id);
  if (!deps.passesGate('approval', borrowerId, !inBulk)) return Promise.resolve({ outcome: 'backend', message: null });
  if (!inBulk && reviewedDraft === null) {
    return Promise.resolve({ outcome: 'backend', message: REVIEWED_DRAFT_REQUIRED });
  }
  const reviewMode: ReviewMode = inBulk
    ? (reviewedDraft ? 'bulk_sample' : 'bulk_cohort')
    : (extras.reviewMode ?? 'individual');
  deps.latch(borrowerId, true);
  const lead = deps.leadsById.get(borrowerId);
  const snapshot = extras.snapshot ?? {
    evidenceIds: lead?.evidence_ids ?? [],
    offerCode: lead?.recommended_offer_code ?? null,
  };
  const intent = extras.bulk_id
    ? intentFingerprint('approve-bulk', extras.bulk_id, borrowerId)
    : intentFingerprint('approve', borrowerId, reviewedDraft?.generation_id);
  return released(deps, borrowerId, deps.approve({
    decision: 'approve',
    borrowerId,
    requestId: deps.requestIds.idFor(intent),
    reviewedDraft,
    campaignBinding: deps.campaignBinding,
    evidenceIds: [...snapshot.evidenceIds],
    offerCode: snapshot.offerCode,
    rationale: extras.rationale ?? null,
    bulkId: extras.bulk_id ?? null,
    bulkRationale: extras.bulk_rationale ?? null,
    reviewMode,
    coveredOfferCodes: extras.coveredOfferCodes ?? null,
    signal,
    suppressInvalidation: extras.suppressInvalidation,
  }).then(
    (res): BulkRowReport => {
      if (!res.approved) {
        if (!inBulk) toastWriteRefusal(`Couldn't approve ${borrowerId}`, 'The endpoint returned approved=false.');
        return { outcome: 'backend', message: 'The endpoint returned approved=false.' };
      }
      deps.requestIds.settle(intent);
      deps.onDecided(borrowerId, 'approved', res.audit_event_id ?? null);
      return { outcome: 'ok', message: null };
    },
    (err: unknown): BulkRowReport => {
      const failure = decisionFailure(err);
      if (failure === 'aborted') return { outcome: 'aborted', message: null };
      // The drafted offer was never previewed: nothing was sent; the row
      // stays selected for an individual review.
      if (err instanceof OfferNotPreviewedError) return { outcome: 'backend', message: OFFER_NOT_PREVIEWED };
      const message = err instanceof Error ? err.message : null;
      // A single row's failure is a toast; a bulk row reports in the run.
      if (!inBulk) toastWriteFailure(`Couldn't approve ${borrowerId}`, err, 'the approval');
      // The session ended mid-click (on the draft step or the approve POST):
      // the session dialog must say this approval was NOT recorded.
      if (clientFailureReason(err) === 'session_expired') {
        markUnrecordedWrite('approval');
        return { outcome: 'session_expired', message };
      }
      return { outcome: failure, message };
    },
  ));
}

/**
 * One row of a bulk rejection run (tables-07, D-approval-flow-d): the
 * reject write with the run's bulk id, reason and shared note, reporting
 * into the run (no toast), like a bulk approve row. The evidence and offer
 * are the ones snapshotted when the run started. The signal aborts only on
 * unmount (R5-21), never on Stop.
 */
export function rejectWithReport(
  deps: DecisionWriteDeps,
  borrowerId: string,
  signal: AbortSignal,
  run: BulkRejectRow,
): Promise<BulkRowReport> {
  if (deps.isInFlight(borrowerId)) return Promise.resolve({ outcome: 'duplicate', message: null });
  if (!deps.passesGate('rejection', borrowerId, false)) return Promise.resolve({ outcome: 'backend', message: null });
  deps.latch(borrowerId, true);
  const intent = intentFingerprint('reject-bulk', run.bulkId, borrowerId, run.reasonCode, run.note);
  return released(deps, borrowerId, deps.reject({
    decision: 'reject',
    borrowerId,
    requestId: deps.requestIds.idFor(intent),
    rationaleCode: run.reasonCode,
    rationale: run.note,
    campaignBinding: deps.campaignBinding,
    evidenceIds: [...(run.snapshot?.evidenceIds ?? [])],
    offerCode: run.snapshot?.offerCode ?? null,
    bulkId: run.bulkId,
    signal,
    suppressInvalidation: true,
  }).then(
    (res): BulkRowReport => {
      if (!res.rejected) return { outcome: 'backend', message: 'The endpoint returned rejected=false.' };
      deps.requestIds.settle(intent);
      deps.onDecided(borrowerId, 'rejected', res.audit_event_id ?? null);
      return { outcome: 'ok', message: null };
    },
    (err: unknown): BulkRowReport => {
      const failure = decisionFailure(err);
      if (failure === 'aborted') return { outcome: 'aborted', message: null };
      const message = err instanceof Error ? err.message : null;
      if (clientFailureReason(err) === 'session_expired') {
        markUnrecordedWrite('rejection');
        return { outcome: 'session_expired', message };
      }
      return { outcome: failure, message };
    },
  ));
}
