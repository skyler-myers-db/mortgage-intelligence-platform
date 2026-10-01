/**
 * Wire contract, Portfolio domain (audit 2026-09-21 quality-04, D-api-types-a3 P1):
 * segments and portfolio (plus campaigns and segment combinations).
 *
 * Compile-time assertions only, checked by tsc through `include: ["src"]`;
 * nothing imports this file. (i) pairs every schema-named hand type with its
 * schema; (ii) pairs every bound call site whose type argument is not
 * schema-named with its operation. tests/unit/test_frontend_wire_contract.py
 * proves the coverage and the binding. A failing pair carries a dated
 * wire-drift entry tagged to the lane that fixes it at the cause.
 */
import type { ApiRequest, ApiResponse } from './api.gen';
import type { DeepNoPhantomKeys, Expect, NoPhantomKeys, WireFits } from './wireContract.check';
import type { CampaignRecommendationRequest, CampaignStatusPatchRequest, PortfolioCreateRequest, PortfolioPreviewRequest } from '../lib/apiClients/portfolio';
import type { CampaignListResponse, CampaignSummary, DimensionFacetCount, HouseholdDedupConfig, HouseholdDedupSummary, PortfolioCreateResponse, PortfolioPreview, SegmentSummary } from '../types';
import type { SegmentCombination, SegmentCombinationProvenance, SegmentCombinationResponse } from './segmentCombinations';

export type WireContractPortfolio = [
  // (i) schema-named hand types
  Expect<WireFits<CampaignRecommendationRequest, ApiRequest<'CampaignRecommendationRequest'>>>,
  // @ts-expect-error wire-drift quality-04 2026-10-01 w5-wire-types: criteria is an open Record<string, unknown> but the wire PortfolioCriteria is a closed key set
  Expect<DeepNoPhantomKeys<CampaignRecommendationRequest, ApiRequest<'CampaignRecommendationRequest'>>>,
  // The drift above masks the whole deep element; a new phantom at this level still fails here.
  Expect<NoPhantomKeys<CampaignRecommendationRequest, ApiRequest<'CampaignRecommendationRequest'>>>,
  Expect<WireFits<CampaignStatusPatchRequest, ApiRequest<'CampaignStatusPatchRequest'>>>,
  Expect<DeepNoPhantomKeys<CampaignStatusPatchRequest, ApiRequest<'CampaignStatusPatchRequest'>>>,
  Expect<WireFits<PortfolioCreateRequest, ApiRequest<'PortfolioCreateRequest'>>>,
  // @ts-expect-error wire-drift quality-04 2026-10-01 w5-wire-types: criteria and household_dedup are open Record<string, unknown> but PortfolioCriteria and HouseholdDedupConfig are closed key sets
  Expect<DeepNoPhantomKeys<PortfolioCreateRequest, ApiRequest<'PortfolioCreateRequest'>>>,
  // The drift above masks the whole deep element; a new phantom at this level still fails here.
  Expect<NoPhantomKeys<PortfolioCreateRequest, ApiRequest<'PortfolioCreateRequest'>>>,
  Expect<WireFits<PortfolioPreviewRequest, ApiRequest<'PortfolioPreviewRequest'>>>,
  // @ts-expect-error wire-drift quality-04 2026-10-01 w5-wire-types: criteria and campaign_build_config.household_dedup are open Record<string, unknown> but PortfolioCriteria and HouseholdDedupConfig are closed key sets
  Expect<DeepNoPhantomKeys<PortfolioPreviewRequest, ApiRequest<'PortfolioPreviewRequest'>>>,
  // The drift above masks the whole deep element; a new phantom at this level still fails here.
  Expect<NoPhantomKeys<PortfolioPreviewRequest, ApiRequest<'PortfolioPreviewRequest'>>>,
  Expect<NoPhantomKeys<PortfolioPreviewRequest['campaign_build_config'], ApiRequest<'CampaignBuildPreviewConfig'>>>,
  Expect<WireFits<ApiResponse<'CampaignListResponse'>, CampaignListResponse>>,
  Expect<NoPhantomKeys<CampaignListResponse, ApiResponse<'CampaignListResponse'>>>,
  Expect<WireFits<ApiResponse<'CampaignSummary'>, CampaignSummary>>,
  Expect<NoPhantomKeys<CampaignSummary, ApiResponse<'CampaignSummary'>>>,
  Expect<WireFits<ApiResponse<'DimensionFacetCount'>, DimensionFacetCount>>,
  Expect<NoPhantomKeys<DimensionFacetCount, ApiResponse<'DimensionFacetCount'>>>,
  Expect<WireFits<HouseholdDedupConfig, ApiRequest<'HouseholdDedupConfig'>>>,
  Expect<DeepNoPhantomKeys<HouseholdDedupConfig, ApiRequest<'HouseholdDedupConfig'>>>,
  Expect<WireFits<ApiResponse<'HouseholdDedupConfig'>, HouseholdDedupConfig>>,
  Expect<NoPhantomKeys<HouseholdDedupConfig, ApiResponse<'HouseholdDedupConfig'>>>,
  Expect<WireFits<ApiResponse<'HouseholdDedupSummary'>, HouseholdDedupSummary>>,
  Expect<NoPhantomKeys<HouseholdDedupSummary, ApiResponse<'HouseholdDedupSummary'>>>,
  Expect<WireFits<ApiResponse<'PortfolioCreateResponse'>, PortfolioCreateResponse>>,
  Expect<NoPhantomKeys<PortfolioCreateResponse, ApiResponse<'PortfolioCreateResponse'>>>,
  // @ts-expect-error wire-drift quality-04 2026-10-01 w5-wire-types: trends values carry a plain-string direction on the wire but up | down | flat in KpiTrend
  Expect<WireFits<ApiResponse<'PortfolioPreview'>, PortfolioPreview>>,
  Expect<NoPhantomKeys<PortfolioPreview, ApiResponse<'PortfolioPreview'>>>,
  Expect<WireFits<ApiResponse<'SegmentSummary'>, SegmentSummary>>,
  Expect<NoPhantomKeys<SegmentSummary, ApiResponse<'SegmentSummary'>>>,
  Expect<WireFits<ApiResponse<'SegmentCombination'>, SegmentCombination>>,
  Expect<NoPhantomKeys<SegmentCombination, ApiResponse<'SegmentCombination'>>>,
  Expect<WireFits<ApiResponse<'SegmentCombinationProvenance'>, SegmentCombinationProvenance>>,
  Expect<NoPhantomKeys<SegmentCombinationProvenance, ApiResponse<'SegmentCombinationProvenance'>>>,
  Expect<WireFits<ApiResponse<'SegmentCombinationResponse'>, SegmentCombinationResponse>>,
  Expect<NoPhantomKeys<SegmentCombinationResponse, ApiResponse<'SegmentCombinationResponse'>>>,
];
