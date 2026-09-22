/**
 * Portfolio and campaign endpoint clients: audience preview, the campaign
 * recommendation, portfolio creation, and the saved-campaign reads plus the
 * campaign status transition.
 *
 * Members moved verbatim out of `api.ts`, which spreads them back into the
 * exported `api` object in the original order. Consumers import `api` from
 * `../api` — never from this module.
 */
import type {
  CampaignListResponse,
  CampaignRecommendationResponse,
  CampaignSummary,
  PortfolioCreateResponse,
  PortfolioPreview,
} from '../../types';
import {
  _newRequestId,
  getJson,
  postJson,
  patchJson,
} from '../apiTransport';

export const portfolioApi = {
  portfolioPreview: (
    criteria: Record<string, unknown> = {},
    signal?: AbortSignal,
    campaignBuildConfig?: {
      suppression_policy: Record<string, unknown>;
      household_dedup: Record<string, unknown>;
    },
  ) =>
    postJson<PortfolioPreview, {
      criteria: Record<string, unknown>;
      campaign_build_config?: {
        suppression_policy: Record<string, unknown>;
        household_dedup: Record<string, unknown>;
      };
    }>(
      '/api/portfolio/preview',
      campaignBuildConfig
        ? { criteria, campaign_build_config: campaignBuildConfig }
        : { criteria },
      signal,
    ),

  campaignRecommendation: (
    criteria: Record<string, unknown> = {},
    signal?: AbortSignal,
  ) =>
    postJson<CampaignRecommendationResponse, { criteria: Record<string, unknown> }>(
      '/api/portfolio/campaign-recommendation',
      { criteria },
      signal,
    ),

  portfolioCreate: (
    name: string,
    criteria: Record<string, unknown> = {},
    config: Partial<{
      suppression_policy: Record<string, unknown>;
      message_variants: Record<string, unknown>[];
      channel_cascade: Record<string, unknown>[];
      send_window: Record<string, unknown>;
      holdout: Record<string, unknown>;
      roi_assumptions: Record<string, unknown>;
      household_dedup: Record<string, unknown>;
      request_id: string;
    }> = {},
    signal?: AbortSignal,
  ) =>
    postJson<PortfolioCreateResponse, {
      name: string;
      criteria: Record<string, unknown>;
      suppression_policy: Record<string, unknown>;
      message_variants: Record<string, unknown>[];
      channel_cascade: Record<string, unknown>[];
      send_window: Record<string, unknown>;
      holdout: Record<string, unknown>;
      roi_assumptions: Record<string, unknown>;
      household_dedup: Record<string, unknown>;
    }>(
      '/api/portfolio/create',
      {
        name,
        criteria,
        suppression_policy: config.suppression_policy ?? { default: 'eligible_only', frequency_cap_days: 30 },
        message_variants: config.message_variants ?? [],
        channel_cascade: config.channel_cascade ?? [
          { channel: 'email', step: 1 },
          { channel: 'sms', step: 2, after_days: 3 },
          { channel: 'direct_mail', step: 3, after_days: 10 },
        ],
        send_window: config.send_window ?? { days: ['Tuesday', 'Wednesday', 'Thursday'], start_local: '09:00', end_local: '16:00' },
        holdout: config.holdout ?? { method: 'hash_modulo', size_pct: 10 },
        roi_assumptions: config.roi_assumptions ?? { source: 'operator_required_before_live_send' },
        household_dedup: config.household_dedup ?? {
          enabled: false,
          dedupe_unit: 'borrower',
          primary_contact_strategy: 'highest_opportunity_eligible',
        },
      },
      signal,
      { 'Idempotency-Key': config.request_id ?? _newRequestId() },
    ),
};

export const campaignApi = {
  campaigns: (signal?: AbortSignal) =>
    getJson<CampaignListResponse>('/api/campaigns', signal),

  campaign: (campaignId: string, signal?: AbortSignal) =>
    getJson<CampaignSummary>(`/api/campaigns/${encodeURIComponent(campaignId)}`, signal),

  campaignStatus: (
    campaignId: string,
    status: CampaignSummary['status'],
    rationale?: string | null,
    signal?: AbortSignal,
  ) =>
    patchJson<CampaignSummary, { status: CampaignSummary['status']; rationale?: string | null }>(
      `/api/campaigns/${encodeURIComponent(campaignId)}`,
      { status, rationale: rationale ?? null },
      signal,
    ),
};
