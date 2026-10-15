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
import type { ApiRequest } from '../../types/api.gen';
import {
  _newRequestId,
  getJson,
  postJson,
  patchJson,
} from '../apiTransport';

/** The `/api/portfolio/preview` body. Criteria and dedup are the wire's closed key sets. */
export interface PortfolioPreviewRequest {
  criteria: ApiRequest<'PortfolioCriteria'>;
  campaign_build_config?: {
    suppression_policy: Record<string, unknown>;
    household_dedup: ApiRequest<'HouseholdDedupConfig'>;
  };
}

/** The `/api/portfolio/campaign-recommendation` body. */
export interface CampaignRecommendationRequest {
  criteria: ApiRequest<'PortfolioCriteria'>;
}

/** The `/api/portfolio/create` body as the client sends it. */
export interface PortfolioCreateRequest {
  name: string;
  criteria: ApiRequest<'PortfolioCriteria'>;
  suppression_policy: Record<string, unknown>;
  message_variants: Record<string, unknown>[];
  channel_cascade: Record<string, unknown>[];
  send_window: Record<string, unknown>;
  holdout: Record<string, unknown>;
  roi_assumptions: Record<string, unknown>;
  household_dedup: ApiRequest<'HouseholdDedupConfig'>;
}

export const portfolioApi = {
  portfolioPreview: (
    criteria: ApiRequest<'PortfolioCriteria'> = {},
    signal?: AbortSignal,
    campaignBuildConfig?: {
      suppression_policy: Record<string, unknown>;
      household_dedup: ApiRequest<'HouseholdDedupConfig'>;
    },
  ) =>
    postJson<PortfolioPreview, PortfolioPreviewRequest>(
      '/api/portfolio/preview',
      campaignBuildConfig
        ? { criteria, campaign_build_config: campaignBuildConfig }
        : { criteria },
      signal,
    ),

  campaignRecommendation: (
    criteria: ApiRequest<'PortfolioCriteria'> = {},
    signal?: AbortSignal,
  ) =>
    postJson<CampaignRecommendationResponse, CampaignRecommendationRequest>(
      '/api/portfolio/campaign-recommendation',
      { criteria },
      signal,
    ),

  portfolioCreate: (
    name: string,
    criteria: ApiRequest<'PortfolioCriteria'> = {},
    config: Partial<{
      suppression_policy: Record<string, unknown>;
      message_variants: Record<string, unknown>[];
      channel_cascade: Record<string, unknown>[];
      send_window: Record<string, unknown>;
      holdout: Record<string, unknown>;
      roi_assumptions: Record<string, unknown>;
      household_dedup: ApiRequest<'HouseholdDedupConfig'>;
      request_id: string;
    }> = {},
    signal?: AbortSignal,
  ) =>
    postJson<PortfolioCreateResponse, PortfolioCreateRequest>(
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

/** The `PATCH /api/campaigns/{id}` body. */
export interface CampaignStatusPatchRequest {
  status: CampaignSummary['status'];
  rationale?: string | null;
}

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
    patchJson<CampaignSummary, CampaignStatusPatchRequest>(
      `/api/campaigns/${encodeURIComponent(campaignId)}`,
      { status, rationale: rationale ?? null },
      signal,
    ),
};
