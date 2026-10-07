import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { QueueContext } from '../lib/queueContext';
import { queryKeys } from '../lib/queryKeys';
import { offerPath } from '../lib/routeMeta';
import type { CampaignSummary } from '../types';
import type { OfferCampaignBinding } from './offer-orchestrator.route-ui';

/**
 * The Offer Orchestrator's campaign binding through the queue pager and Next
 * in queue (2026-09-21 audit shell-04 / flow-09; D-approval-flow-a2). The
 * pager and Next used to open the neighbour at plain offerPath(id), dropping
 * `?campaign_id=&variant_name=`, so stepping from a campaign-bound approval
 * landed on an unbound one.
 *
 * A binding is VERIFIED only when both hold:
 *   (i)  GET /api/campaigns/{id} (audit-free, Lakebase) returns the same
 *        campaign with an email variant of that name: the Lead Queue's own
 *        rule (LeadTable.logic verifiedCampaignBinding), restated here so the
 *        Offer route never imports the LeadTable chunk (the queueContext
 *        chunk-hoist trap); a parity test feeds both one table. The read
 *        shares queryKeys.campaign(id) with the Lead Queue.
 *   (ii) the queue this offer was opened from carries the identical
 *        campaign_id and variant_name in its search.
 *
 * Outcomes: an unbound offer is unchanged (offerPath); a verified one steps
 * with both params; while the campaign read runs the pager renders disabled;
 * an UNVERIFIED binding (a half binding, a campaign that does not confirm it,
 * or a queue bound to another campaign or to none) hides the pager and Next
 * in queue. Fail closed: never step from a bound approval into an unbound
 * one, and never carry a binding nobody verified. No new endpoint, wire
 * type, audit row or storage.
 */

export type OfferBindingState = 'absent' | 'validating' | 'verified' | 'unverified';

export interface VerifiedOfferBinding {
  state: OfferBindingState;
  /** Where a pager step or Next in queue opens a neighbour. */
  pathFor: (borrowerId: string) => string;
}

const CAMPAIGN_STALE_MS = 60_000;

/** The Lead Queue's verification rule: the same campaign, with an email variant of that name. */
export function campaignConfirmsBinding(
  campaign: CampaignSummary | null | undefined,
  binding: OfferCampaignBinding,
): boolean {
  if (!campaign || campaign.campaign_id !== binding.campaign_id) return false;
  return (campaign.message_variants ?? []).some((variant) => (
    variant.variant_name === binding.variant_name && variant.channel === 'email'
  ));
}

/** The queue's own search carries exactly this binding. */
export function queueCarriesBinding(queue: QueueContext | null, binding: OfferCampaignBinding): boolean {
  if (!queue) return false;
  const params = new URLSearchParams(queue.search);
  return params.get('campaign_id')?.trim() === binding.campaign_id
    && params.get('variant_name')?.trim() === binding.variant_name;
}

/** offerPath plus the verified binding's two params. */
export function boundOfferPath(binding: OfferCampaignBinding): (borrowerId: string) => string {
  const search = new URLSearchParams({ campaign_id: binding.campaign_id, variant_name: binding.variant_name }).toString();
  return (borrowerId) => `${offerPath(borrowerId)}?${search}`;
}

/**
 * @param campaignBinding the URL's complete binding, or null
 * @param bindingError the URL carries only half a binding (fail closed)
 * @param queue the queue this offer was opened from
 */
export function useVerifiedOfferBinding(
  campaignBinding: OfferCampaignBinding | null,
  bindingError: boolean,
  queue: QueueContext | null,
): VerifiedOfferBinding {
  const campaignId = campaignBinding?.campaign_id ?? '';
  const carried = campaignBinding !== null && queueCarriesBinding(queue, campaignBinding);
  const campaign = useQuery({
    queryKey: queryKeys.campaign(campaignId),
    queryFn: ({ signal }) => api.campaign(campaignId, signal),
    // Read only when it can verify: a queue bound elsewhere is unverified as it is.
    enabled: carried,
    retry: false,
    staleTime: CAMPAIGN_STALE_MS,
  });
  if (bindingError) return { state: 'unverified', pathFor: offerPath };
  if (!campaignBinding) return { state: 'absent', pathFor: offerPath };
  if (!carried) return { state: 'unverified', pathFor: offerPath };
  if (campaignConfirmsBinding(campaign.data, campaignBinding)) {
    return { state: 'verified', pathFor: boundOfferPath(campaignBinding) };
  }
  if (campaign.isPending || campaign.isFetching) return { state: 'validating', pathFor: offerPath };
  return { state: 'unverified', pathFor: offerPath };
}

/** The pager and Next in queue show for an unbound or a verified offer, disabled while validating. */
export function bindingShowsPager(state: OfferBindingState): boolean {
  return state !== 'unverified';
}
