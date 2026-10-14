/**
 * LeadTableStatusChips — the provenance chip rows between the ranked-borrower
 * surface header and the table: the verified Growth Agent cohort proof, the
 * campaign-binding validation status, and the campaign-bound outreach row with
 * its deep link to the bound offer. Presentation only — every value is
 * verified upstream. Extracted from LeadTable.tsx (file-size gate, plan item
 * 2); markup, class names, and test ids are unchanged.
 */

import type { ReactNode } from 'react';
import { Link } from 'react-router';
import { Icon } from '../Icon';
import { Chip } from '../Primitives';
import { Tooltip } from '../ui/Tooltip';
import type { GrowthAgentCohortVerification } from '../../lib/api';
import { formatCount } from '../../lib/formatters';
import type { CampaignBinding } from './LeadTable.logic';
import type { CampaignBindingState } from './useLeadApprovalActions';
import { APPROVER_ROLE_STATUS_ID } from './approverGate';

interface LeadTableStatusChipsProps {
  growthAgentVerification: GrowthAgentCohortVerification | null;
  campaignBindingState: CampaignBindingState;
  /** The binding asked for in the URL — rendered only while validating. */
  requestedCampaignBinding: CampaignBinding | null;
  /** The binding the backend confirmed. Null until (or unless) verified. */
  campaignBinding: CampaignBinding | null;
  /** Borrower id of the expanded row, if any — drives the bound-offer link. */
  expandedBorrowerId: string | null;
  /** Non-null = the actor may not approve; rendered as the visible reason. */
  approverGate?: string | null;
  /** The signed-in actor's own identity, named in the gate line when known. */
  actorEmail?: string | null;
  /** The request path is open to this non-approver: the gate line says where approval comes from. */
  requestPathOpen?: boolean;
  /** An approver's request-scoped queue (flow-02): its scope row, from the route. */
  requestScopeRow?: ReactNode;
}

export function LeadTableStatusChips({
  growthAgentVerification,
  campaignBindingState,
  requestedCampaignBinding,
  campaignBinding,
  expandedBorrowerId,
  approverGate = null,
  actorEmail = null,
  requestPathOpen = false,
  requestScopeRow = null,
}: LeadTableStatusChipsProps) {
  return (
    <>
      {requestScopeRow}
      {approverGate !== null && (
        // The approve / reject / bulk-approve controls point their
        // aria-describedby here: the gate stays visible, with its reason.
        <div id={APPROVER_ROLE_STATUS_ID} className="table-neutral chip-row" role="status" data-testid="approver-role-status">
          <Chip variant="warning" icon="shield">{approverGate}</Chip>
          <span>
            Approve, reject and bulk approve are disabled
            {actorEmail ? <> for <span className="mono lead-table__actor-email">{actorEmail}</span></> : ' for this sign-in'}.
            {requestPathOpen && ' Request approval sends them to an approver.'}
          </span>
        </div>
      )}
      {growthAgentVerification && (
        <div className="table-success chip-row" role="status" data-testid="growth-agent-cohort-proof">
          <Chip variant="success" icon="shield">Verified Growth Agent cohort</Chip>
          <span className="num">{formatCount(growthAgentVerification.total)} borrowers</span>
          {/* Sliced ids carry the full value on the design-system tooltip (critic-08). */}
          <Tooltip content={growthAgentVerification.cohortFingerprint}>
            <span className="mono">proof {growthAgentVerification.cohortFingerprint.slice(0, 12)}</span>
          </Tooltip>
          <Tooltip content={growthAgentVerification.snapshotId}>
            <span>snapshot {growthAgentVerification.snapshotId}</span>
          </Tooltip>
          <Tooltip content={growthAgentVerification.runId}>
            <span className="mono">run {growthAgentVerification.runId.slice(0, 12)}</span>
          </Tooltip>
        </div>
      )}
      {campaignBindingState === 'validating' && requestedCampaignBinding && (
        <div id="campaign-binding-status" className="table-neutral chip-row" role="status" aria-live="polite" data-testid="campaign-binding-status">
          <Chip variant="neutral" icon="shield">Validating campaign binding</Chip>
          <Tooltip content={requestedCampaignBinding.campaign_id}>
            <span className="mono">campaign {requestedCampaignBinding.campaign_id.slice(0, 12)}</span>
          </Tooltip>
          <span>variant {requestedCampaignBinding.variant_name}</span>
        </div>
      )}
      {campaignBindingState === 'invalid' && (
        <div id="campaign-binding-status" className="table-neutral chip-row" role="status" aria-live="polite" data-testid="campaign-binding-status">
          <Chip variant="neutral" icon="shield">Campaign binding invalid</Chip>
          <span>Reopen the saved campaign and select a verified variant before taking action.</span>
        </div>
      )}
      {campaignBinding && (
        <div className="table-success chip-row" role="status" data-testid="campaign-operational-provenance">
          <Chip variant="success" icon="shield">Campaign-bound outreach</Chip>
          <Tooltip content={campaignBinding.campaign_id}>
            <span className="mono">campaign {campaignBinding.campaign_id.slice(0, 12)}</span>
          </Tooltip>
          <span>variant {campaignBinding.variant_name}</span>
          {expandedBorrowerId && (
            <Link
              className="btn btn--primary btn--sm"
              to={`/offer-orchestrator/${encodeURIComponent(expandedBorrowerId)}?${new URLSearchParams({
                campaign_id: campaignBinding.campaign_id,
                variant_name: campaignBinding.variant_name,
              }).toString()}`}
            >
              Open bound offer
              <Icon name="chevright" size={12} />
            </Link>
          )}
        </div>
      )}
    </>
  );
}
