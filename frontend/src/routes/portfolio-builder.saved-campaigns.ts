import type { CampaignSummary } from '../types';
import { publicAgentResponsesText } from '../lib/agentLabels';
import { URL_FILTER_KEYS } from './portfolio-builder.logic';

export interface SavedCampaignVariant {
  variantName: string;
  generationMode: string;
  generatorLabel: string;
  verifiedAtCreation: boolean;
}

const SAVED_CAMPAIGN_ROUTE_KEYS = new Set([
  ...URL_FILTER_KEYS,
  'state',
  'states',
  'zip',
  'zips',
  'county',
  'counties',
  'borrower_ids',
  'segment',
  'segment_codes',
  'segment_mode',
  'funnel_stage',
  'portfolio_id',
]);

export function savedCampaignVariants(campaign: CampaignSummary): SavedCampaignVariant[] {
  if (campaign.actionable === false) return [];
  return (campaign.message_variants ?? []).flatMap((raw) => {
    const variantName = typeof raw.variant_name === 'string' ? raw.variant_name.trim() : '';
    if (!variantName) return [];
    return [{
      variantName,
      generationMode: typeof raw.generation_mode === 'string' && raw.generation_mode.trim()
        ? raw.generation_mode.trim()
        : 'operator',
      generatorLabel: typeof raw.generator_label === 'string' && raw.generator_label.trim()
        ? publicAgentResponsesText(raw.generator_label)
        : 'Operator edited',
      verifiedAtCreation: raw.copy_verified_at_creation === true,
    }];
  });
}

/**
 * Only campaigns which cannot be bound to immutable treatment proof may use
 * the quarantine archive affordance. Current servers expose the exact state;
 * the issue-only fallback preserves the remediation path during a rolling
 * deployment and the API remains the final transition guard.
 */
export function savedCampaignCanArchive(campaign: CampaignSummary): boolean {
  if (
    campaign.status === 'archived'
    || campaign.actionable !== false
    || campaign.actionability_issue !== 'treatment_unbound'
  ) {
    return false;
  }
  return campaign.treatment_state === undefined
    || campaign.treatment_state === 'legacy_unbound'
    || campaign.treatment_state === 'failed';
}

export function savedCampaignLeadQueueUrl(
  campaign: CampaignSummary,
  variantName: string,
): string {
  const route = typeof campaign.criteria.route === 'string'
    && campaign.criteria.route.startsWith('/lead-queue')
    ? campaign.criteria.route
    : '/lead-queue';
  const url = new URL(route, 'https://mortgage-intelligence.local');
  const nestedFilters = campaign.criteria.result_filters;
  const replayFilters = nestedFilters && typeof nestedFilters === 'object' && !Array.isArray(nestedFilters)
    ? nestedFilters as Record<string, unknown>
    : campaign.criteria;
  for (const [key, raw] of Object.entries(replayFilters)) {
    if (!SAVED_CAMPAIGN_ROUTE_KEYS.has(key) || raw === null || raw === undefined) continue;
    const value = Array.isArray(raw) ? raw.join(',') : String(raw);
    if (value.trim()) url.searchParams.set(key, value);
  }
  url.searchParams.set('campaign_id', campaign.campaign_id);
  url.searchParams.set('variant_name', variantName);
  return `${url.pathname}${url.search}`;
}
