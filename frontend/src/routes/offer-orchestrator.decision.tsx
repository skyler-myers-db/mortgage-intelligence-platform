/**
 * The approval-result panel of the Offer Orchestrator: the routing
 * confirmation chip, the approved / rejected surfaces and the write-failure
 * alert. Moved verbatim out of offer-orchestrator.tsx (file-size gate); the
 * route passes the same values it used to read inline.
 */
import type { Borrower360 as Borrower360Type, OfferRecommendation } from '../types';
import { ActivationLoopPanel } from '../components/activation/ActivationLoopPanel';
import { Chip } from '../components/Primitives';
import type { OutreachChannel } from './offer-orchestrator.constants';

export interface OfferDecisionOutcomeProps {
  id: string;
  b: Borrower360Type | null;
  rec: OfferRecommendation | null;
  activeDraftChannel: OutreachChannel;
  effectiveApproval: string | undefined;
  justApproved: { id: string; reloadToken: number } | null;
  reloadToken: number;
  auditId: string | null;
  approvalId: string | null;
  approveError: string | null;
  routingConfirm: { email: string | null; followUpAt: string | null } | null;
}

export function OfferDecisionOutcome({
  id,
  b,
  rec,
  activeDraftChannel,
  effectiveApproval,
  justApproved,
  reloadToken,
  auditId,
  approvalId,
  approveError,
  routingConfirm,
}: OfferDecisionOutcomeProps) {
  return (
    <>
      {routingConfirm && (routingConfirm.email || routingConfirm.followUpAt) && (
        <div className="outreach-routing__confirm mt-grid" role="status" data-testid="routing-confirm">
          <Chip variant="success">
            {routingConfirm.email ? `Assigned to ${routingConfirm.email}` : 'Unassigned'}
            {routingConfirm.followUpAt
              ? ` · follow-up ${new Date(routingConfirm.followUpAt).toLocaleDateString(undefined, {
                  month: 'short',
                  day: 'numeric',
                })}`
              : ''}
          </Chip>
        </div>
      )}

      {effectiveApproval === 'approved' && (
        <>
          <div className="surface mt-grid">
            <div className="surface__body surface__body--inline">
              <span
                className={
                  justApproved?.id === id && justApproved.reloadToken === reloadToken
                    ? 'burst inline-flex'
                    : 'inline-flex'
                }
              >
                <Chip variant="success" icon="check">Approved · governed internal queue</Chip>
              </span>
              {auditId && <span className="mono muted fs-11">audit: {auditId}</span>}
              {approvalId && <span className="mono muted fs-11">approval: {approvalId}</span>}
            </div>
          </div>
          <ActivationLoopPanel
            borrowerId={b?.borrower_id ?? id}
            offerCode={rec?.offer_code ?? b?.recommended_offer_code ?? null}
            channel={activeDraftChannel}
            approvalId={approvalId}
            approved
          />
        </>
      )}
      {effectiveApproval === 'rejected' && (
        <div className="surface mt-grid">
          <div className="surface__body surface__body--inline">
            <Chip variant="danger" icon="cross">Rejected</Chip>
            {auditId && <span className="mono muted fs-11">audit: {auditId}</span>}
          </div>
        </div>
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
