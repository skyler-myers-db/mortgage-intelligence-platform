/**
 * LeadTableStatusChips — the provenance chip rows between the ranked-borrower
 * surface header and the table: the verified Growth Agent cohort proof, the
 * campaign-binding validation status, and the campaign-bound outreach row with
 * its deep link to the bound offer. Presentation only — every value is
 * verified upstream. Extracted from LeadTable.tsx (file-size gate, plan item
 * 2); markup, class names, and test ids are unchanged.
 */

import { Link } from 'react-router';
import { Icon } from '../Icon';
import { Chip } from '../Primitives';
import type { GrowthAgentCohortVerification } from '../../lib/api';
import type { CampaignBinding } from './LeadTable.logic';
import type { CampaignBindingState } from './useLeadApprovalActions';

interface LeadTableStatusChipsProps {
  growthAgentVerification: GrowthAgentCohortVerification | null;
  campaignBindingState: CampaignBindingState;
  /** The binding asked for in the URL — rendered only while validating. */
  requestedCampaignBinding: CampaignBinding | null;
  /** The binding the backend confirmed. Null until (or unless) verified. */
  campaignBinding: CampaignBinding | null;
  /** Borrower id of the expanded row, if any — drives the bound-offer link. */
  expandedBorrowerId: string | null;
}

export function LeadTableStatusChips({
  growthAgentVerification,
  campaignBindingState,
  requestedCampaignBinding,
  campaignBinding,
  expandedBorrowerId,
}: LeadTableStatusChipsProps) {
  return (
    <>
      {growthAgentVerification && (
        <div className="table-success chip-row" role="status" data-testid="growth-agent-cohort-proof">
          <Chip variant="success" icon="shield">Verified Growth Agent cohort</Chip>
          <span className="num">{growthAgentVerification.total.toLocaleString()} borrowers</span>
          <span className="mono" title={growthAgentVerification.cohortFingerprint}>
            proof {growthAgentVerification.cohortFingerprint.slice(0, 12)}
          </span>
          <span title={growthAgentVerification.snapshotId}>
            snapshot {growthAgentVerification.snapshotId}
          </span>
          <span className="mono" title={growthAgentVerification.runId}>
            run {growthAgentVerification.runId.slice(0, 12)}
          </span>
        </div>
      )}
      {campaignBindingState === 'validating' && requestedCampaignBinding && (
        <div id="campaign-binding-status" className="table-neutral chip-row" role="status" aria-live="polite" data-testid="campaign-binding-status">
          <Chip variant="neutral" icon="shield">Validating campaign binding</Chip>
          <span className="mono" title={requestedCampaignBinding.campaign_id}>
            campaign {requestedCampaignBinding.campaign_id.slice(0, 12)}
          </span>
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
          <span className="mono" title={campaignBinding.campaign_id}>
            campaign {campaignBinding.campaign_id.slice(0, 12)}
          </span>
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
