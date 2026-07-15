import { useMemo } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { PageShell } from '../components/layout/PageShell';
import { TopLeadsQuickPick } from '../components/mortgage/TopLeadsQuickPick';
import { Chip } from '../components/Primitives';
import { WarmingUpBlock } from '../components/ui/WarmingUpBlock';
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

export function OfferOrchestratorEmptyRoute() {
  return (
    <PageShell
      eyebrow="Offer Orchestrator"
      title="Choose a borrower to compose an offer"
      lede="Offer Orchestrator explains the selected offer path, considered alternatives, and borrower-facing draft before any outreach can be approved. Pick a borrower to begin."
      heroRight={<OfferOrchestratorEmptyHero to="/lead-queue" />}
    >
      <OfferOrchestratorEmptyState />
      <TopLeadsQuickPick basePath="/offer-orchestrator" />
    </PageShell>
  );
}

export function OfferWarmingRoute({
  borrowerId,
  warmingUp,
}: {
  borrowerId: string;
  warmingUp: WarmingUpState;
}) {
  return (
    <PageShell
      eyebrow={warmingUp.label}
      title={`Loading ${borrowerId}…`}
      lede="Databricks SQL warehouses auto-suspend when idle. It takes ~30 seconds to warm up. Retrying automatically…"
    >
      <WarmingUpBlock state={warmingUp} title={`Loading offer for ${borrowerId}`} />
    </PageShell>
  );
}

export function OfferLoadErrorRoute({
  borrowerId,
  loadError,
  notFound,
  onRetry,
}: {
  borrowerId: string;
  loadError: string;
  notFound: boolean;
  onRetry: () => void;
}) {
  return (
    <PageShell
      eyebrow="Offer & Outreach"
      title={notFound ? `Borrower ${borrowerId} not found` : `Couldn't load ${borrowerId}`}
      lede={notFound ? `Borrower ${borrowerId} was not found. Check the ID, use search, or return to the lead queue.` : loadError}
    >
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
          <Link className="btn" to="/lead-queue">
            Back to lead queue
          </Link>
        </div>
      </div>
    </PageShell>
  );
}
