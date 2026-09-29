import { useMemo, type ReactNode } from 'react';
import { Link, useSearchParams } from 'react-router';
import { PageShell } from '../components/layout/PageShell';
import { TopLeadsQuickPick } from '../components/mortgage/TopLeadsQuickPick';
import { Chip } from '../components/Primitives';
import { WAREHOUSE_WARMING_BODY, WarmingUpBlock } from '../components/ui/WarmingUpBlock';
import type { WarmingUpState } from '../lib/useWarmingUpRetry';
import {
  OfferOrchestratorEmptyHero,
  OfferOrchestratorEmptyState,
} from './offer-orchestrator.panels';

export interface OfferCampaignBinding {
  campaign_id: string;
  variant_name: string;
}

export function useOfferCampaignBinding(): {
  campaignBinding: OfferCampaignBinding | null;
  campaignBindingError: boolean;
} {
  const [searchParams] = useSearchParams();
  const campaignId = searchParams.get('campaign_id')?.trim() ?? '';
  const variantName = searchParams.get('variant_name')?.trim() ?? '';
  const campaignBinding = useMemo(
    () => campaignId && variantName
      ? { campaign_id: campaignId, variant_name: variantName }
      : null,
    [campaignId, variantName],
  );
  return {
    campaignBinding,
    campaignBindingError: Boolean(campaignId || variantName) && !campaignBinding,
  };
}

/** `backTo`: the exact filtered queue the page was opened from (queueHref), else '/lead-queue'. */
export function OfferOrchestratorEmptyRoute({ backTo }: { backTo: string }) {
  return (
    <PageShell
      eyebrow="Offer Orchestrator"
      title="Choose a borrower to compose an offer"
      lede="Offer Orchestrator explains the selected offer path, considered alternatives, and borrower-facing draft before any outreach can be approved. Pick a borrower to begin."
      heroRight={<OfferOrchestratorEmptyHero to={backTo} />}
    >
      <OfferOrchestratorEmptyState />
      <TopLeadsQuickPick basePath="/offer-orchestrator" />
    </PageShell>
  );
}

export function OfferWarmingRoute({
  borrowerId,
  warmingUp,
  pager = null,
}: {
  borrowerId: string;
  warmingUp: WarmingUpState;
  /** The queue pager, so the reviewer can step past a borrower that will not load. */
  pager?: ReactNode;
}) {
  return (
    <PageShell
      eyebrow={warmingUp.label}
      title={`Loading ${borrowerId}…`}
      lede={WAREHOUSE_WARMING_BODY}
    >
      {pager}
      <WarmingUpBlock state={warmingUp} title={`Loading offer for ${borrowerId}`} />
    </PageShell>
  );
}

export function OfferLoadErrorRoute({
  borrowerId,
  loadError,
  notFound,
  onRetry,
  backTo,
  pager = null,
}: {
  borrowerId: string;
  loadError: string;
  notFound: boolean;
  onRetry: () => void;
  /** The exact filtered queue the page was opened from (queueHref), else '/lead-queue'. */
  backTo: string;
  pager?: ReactNode;
}) {
  return (
    <PageShell
      eyebrow="Offer & Outreach"
      title={notFound ? `Borrower ${borrowerId} not found` : `Couldn't load ${borrowerId}`}
      lede={notFound ? `Borrower ${borrowerId} was not found. Check the ID, use search, or return to the lead queue.` : loadError}
    >
      {pager}
      <div className="surface">
        <div className="surface__body surface__body--inline">
          <Chip variant={notFound ? 'warning' : 'danger'} icon={notFound ? 'search' : 'cross'}>
            {notFound ? 'Not found' : 'Backend unavailable'}
          </Chip>
          {!notFound && (
            <button
              type="button"
              className="btn"
              onClick={onRetry}
              aria-label="Retry loading borrower and offer"
            >
              Retry
            </button>
          )}
          <Link className="btn" to={backTo}>
            Back to lead queue
          </Link>
        </div>
      </div>
    </PageShell>
  );
}
