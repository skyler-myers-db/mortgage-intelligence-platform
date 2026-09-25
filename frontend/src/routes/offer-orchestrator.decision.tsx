/**
 * The decision outcome of the Offer Orchestrator: the Decision receipt for an
 * approved / rejected borrower, the activation loop for an approval, and the
 * write-failure alert. Where an approval was routed (assignee, follow-up) is
 * a shell toast (offer-orchestrator.feedback, audit states-07) and, for the
 * decision made in this view, a persistent routing line on the receipt
 * (carryover #12). The receipt takes focus when it is this view's decision
 * (carryover #11), so Approve / Confirm reject never strand it on <body>.
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
import { Link } from 'react-router';
import { ActivationLoopPanel } from '../components/activation/ActivationLoopPanel';
import type { DecisionRouting } from '../components/mortgage/DecisionReceipt.copy';
import { lazyModule, useLazyModule } from '../components/mortgage/useLazyModule';
import { Chip } from '../components/Primitives';
import { queueHref, type QueueContext } from '../lib/queueContext';
import { offerPath } from '../lib/routeMeta';
import type { OutreachChannel } from './offer-orchestrator.constants';

// The receipt renders only once a decision exists, so it loads then (its
// shared interaction chunk) instead of riding in the route closure (budget,
// wave 3). The slot stays empty while it loads; the plain decision chip below
// states the outcome only without an audit id or if the chunk cannot load.
const RECEIPT_CHUNK = lazyModule(() => import('../components/mortgage/DecisionReceipt'));

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
  /**
   * Where this view's approval was routed, from its approve response
   * (carryover #12): a persistent line on the receipt, beside the toast.
   */
  routing?: DecisionRouting | null;
  /** The Lead Queue this offer was opened from, or null (shell-04 / flow-09). */
  queue?: QueueContext | null;
  /** The next ranked borrower in that queue; null at its end. */
  nextId?: string | null;
}

/**
 * The step after a decision made HERE (audit shell-04 / flow-09): the next
 * ranked borrower's offer, or the filtered queue at its end. Rendered only
 * once the write returned true in this view (justDecided), never for a
 * durable decision, never on its own: no auto-navigation and no focus move
 * (the receipt keeps focus). A plain Link: no prefetch and no hover read of
 * the neighbour, whose open writes audit rows only when the reviewer goes.
 */
function NextInQueue({ queue, nextId }: { queue: QueueContext; nextId: string | null }) {
  return (
    <div className="section-actions" data-testid="offer-next-in-queue">
      {nextId ? (
        <Link className="btn btn--sm" to={offerPath(nextId)} state={{ queue }}>
          Next in queue: <span className="mono">{nextId}</span>
        </Link>
      ) : (
        <Link className="btn btn--sm" to={queueHref(queue)}>
          Back to lead queue
        </Link>
      )}
    </div>
  );
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
  routing = null,
  queue = null,
  nextId = null,
}: OfferDecisionOutcomeProps) {
  const scoreAtDecision = justDecided ? score : null;
  const decided = effectiveApproval === 'approved' || effectiveApproval === 'rejected';
  // An empty audit id is no receipt to read back: the plain decision chip.
  const hasAuditId = Boolean(auditId);
  const receiptChunk = useLazyModule(RECEIPT_CHUNK, decided && hasAuditId);
  const DecisionReceipt = receiptChunk.module?.DecisionReceipt;
  const receiptSlot = hasAuditId && !receiptChunk.failed;
  const nextStep = justDecided && queue ? <NextInQueue queue={queue} nextId={nextId} /> : null;
  return (
    <>
      {effectiveApproval === 'approved' && (
        receiptSlot ? (DecisionReceipt && auditId && (
          <DecisionReceipt
            auditEventId={auditId}
            decision="approved"
            decidedHere={justDecided}
            reveal={justDecided}
            score={scoreAtDecision}
            focusHeading={justDecided}
            routing={routing}
            className="mt-grid"
          />
        )) : (
          <div className="surface mt-grid">
            <div className="surface__body surface__body--inline">
              <Chip variant="success" icon="check">Approved · governed internal queue</Chip>
              {approvalId && <span className="mono muted fs-11">approval: {approvalId}</span>}
            </div>
          </div>
        )
      )}
      {effectiveApproval === 'rejected' && (
        receiptSlot ? (DecisionReceipt && auditId && (
          <DecisionReceipt
            auditEventId={auditId}
            decision="rejected"
            decidedHere={justDecided}
            reveal={justDecided}
            score={scoreAtDecision}
            focusHeading={justDecided}
            className="mt-grid"
          />
        )) : (
          <div className="surface mt-grid">
            <div className="surface__body surface__body--inline">
              <Chip variant="danger" icon="cross">Rejected</Chip>
            </div>
          </div>
        )
      )}
      {nextStep}
      {effectiveApproval === 'approved' && (
        <ActivationLoopPanel
          borrowerId={borrowerId}
          offerCode={offerCode}
          channel={channel}
          approvalId={approvalId}
          approved
        />
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
