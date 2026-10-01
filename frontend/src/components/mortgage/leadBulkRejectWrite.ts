/**
 * One row of a bulk rejection run (audit tables-07, D-approval-flow-d),
 * split out of leadDecisionWrites so it rides the lazy bulk chunk with the
 * reject gate (the only place a bulk rejection starts; re-exported from
 * LeadBulkApproveReview.tsx): leadBulkDecisions.runBulkReject runs it with
 * useLeadApprovalActions' DecisionWriteDeps. Same invariants as every
 * decision write there.
 */
import { clientFailureReason } from '../../lib/apiTransport';
import { markUnrecordedWrite } from '../../lib/sessionStatus';
import { decisionFailure } from '../../lib/mutations/outreach';
import { intentFingerprint } from '../../lib/mutations/requestIds';
import type { BulkRowReport } from './useLeadBulkRun';
import { released, type BulkRejectRow, type DecisionWriteDeps } from './leadDecisionWrites';

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
