/**
 * The decision outcome of the Offer Orchestrator: the Decision receipt for an
 * approved / rejected borrower, the activation loop for an approval, and the
 * write-failure alert. Where an approval was routed (assignee, follow-up) is
 * a shell toast now (offer-orchestrator.feedback, audit states-07).
 *
 * The receipt renders only from the ledger read-back (DecisionReceipt), so a
 * decision made in this view shows "Recording decision…" until the row is
 * confirmed. A durable decision from an earlier session renders the finished
 * receipt without the reveal; a durable approval whose lifecycle row carries
 * no audit id keeps the plain approved chip. The receipt is handed the
 * outcome this page already knows (effectiveApproval: the resolved POST or
 * the durable lifecycle row), so a read-back this viewer may not make (403:
 * another approver's row) or that fails still says Approved / Rejected.
 */
import { ActivationLoopPanel } from '../components/activation/ActivationLoopPanel';
import { DecisionReceipt } from '../components/mortgage/DecisionReceipt';
import { Chip } from '../components/Primitives';
import type { OutreachChannel } from './offer-orchestrator.constants';

export interface OfferDecisionOutcomeProps {
  borrowerId: string;
  offerCode: string | null;
  channel: OutreachChannel;
  effectiveApproval: string | undefined;
  /** Audit row of the decision shown: the POST's id, or the lifecycle's for a durable decision. */
  auditId: string | null;
  /** The decision was made in this view (borrower + load generation): play the receipt reveal. */
  justDecided: boolean;
  approvalId: string | null;
  approveError: string | null;
  /**
   * The borrower's score as loaded now. Shown on the receipt as "Score at
   * decision" only for a decision made in this view: a durable decision
   * from an earlier session would otherwise show today's score under that label.
   */
  score: { opportunityScore: number; confidence: number } | null;
}

export function OfferDecisionOutcome({
  borrowerId,
  offerCode,
  channel,
  effectiveApproval,
  auditId,
  justDecided,
  approvalId,
  approveError,
  score,
}: OfferDecisionOutcomeProps) {
  const scoreAtDecision = justDecided ? score : null;
  return (
    <>
      {effectiveApproval === 'approved' && (
        <>
          {auditId ? (
            <DecisionReceipt
              auditEventId={auditId}
              decision="approved"
              decidedHere={justDecided}
              reveal={justDecided}
              score={scoreAtDecision}
              className="mt-grid"
            />
          ) : (
            <div className="surface mt-grid">
              <div className="surface__body surface__body--inline">
                <Chip variant="success" icon="check">Approved · governed internal queue</Chip>
                {approvalId && <span className="mono muted fs-11">approval: {approvalId}</span>}
              </div>
            </div>
          )}
          <ActivationLoopPanel
            borrowerId={borrowerId}
            offerCode={offerCode}
            channel={channel}
            approvalId={approvalId}
            approved
          />
        </>
      )}
      {effectiveApproval === 'rejected' && (
        auditId ? (
          <DecisionReceipt
            auditEventId={auditId}
            decision="rejected"
            decidedHere={justDecided}
            reveal={justDecided}
            score={scoreAtDecision}
            className="mt-grid"
          />
        ) : (
          <div className="surface mt-grid">
            <div className="surface__body surface__body--inline">
              <Chip variant="danger" icon="cross">Rejected</Chip>
            </div>
          </div>
        )
      )}
      {approveError && (
        <div
          className="surface surface--danger mt-grid"
          role="alert"
        >
          <div className="surface__body text-danger">
            {approveError}
          </div>
        </div>
      )}
    </>
  );
}
