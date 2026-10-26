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
import type { CampaignListResponse, CampaignSummary, HouseholdDedupConfig, PortfolioCreateResponse, PortfolioPreview, SegmentSummary } from '../types';

export type WireContractPortfolio = [
  // (i) schema-named hand types
  Expect<WireFits<CampaignRecommendationRequest, ApiRequest<'CampaignRecommendationRequest'>>>,
  Expect<DeepNoPhantomKeys<CampaignRecommendationRequest, ApiRequest<'CampaignRecommendationRequest'>>>,
  // The shallow pair beside the deep element (W5b M4a): a phantom at this level fails both.
  Expect<NoPhantomKeys<CampaignRecommendationRequest, ApiRequest<'CampaignRecommendationRequest'>>>,
  Expect<WireFits<CampaignStatusPatchRequest, ApiRequest<'CampaignStatusPatchRequest'>>>,
  Expect<DeepNoPhantomKeys<CampaignStatusPatchRequest, ApiRequest<'CampaignStatusPatchRequest'>>>,
  Expect<WireFits<PortfolioCreateRequest, ApiRequest<'PortfolioCreateRequest'>>>,
  Expect<DeepNoPhantomKeys<PortfolioCreateRequest, ApiRequest<'PortfolioCreateRequest'>>>,
  // The shallow pair beside the deep element (W5b M4a): a phantom at this level fails both.
  Expect<NoPhantomKeys<PortfolioCreateRequest, ApiRequest<'PortfolioCreateRequest'>>>,
  Expect<WireFits<PortfolioPreviewRequest, ApiRequest<'PortfolioPreviewRequest'>>>,
  Expect<DeepNoPhantomKeys<PortfolioPreviewRequest, ApiRequest<'PortfolioPreviewRequest'>>>,
  // The shallow pair beside the deep element (W5b M4a): a phantom at this level fails both.
  Expect<NoPhantomKeys<PortfolioPreviewRequest, ApiRequest<'PortfolioPreviewRequest'>>>,
  Expect<NoPhantomKeys<PortfolioPreviewRequest['campaign_build_config'], ApiRequest<'CampaignBuildPreviewConfig'>>>,
  Expect<WireFits<ApiResponse<'CampaignListResponse'>, CampaignListResponse>>,
  Expect<NoPhantomKeys<CampaignListResponse, ApiResponse<'CampaignListResponse'>>>,
  Expect<WireFits<ApiResponse<'CampaignSummary'>, CampaignSummary>>,
  Expect<NoPhantomKeys<CampaignSummary, ApiResponse<'CampaignSummary'>>>,
  Expect<WireFits<HouseholdDedupConfig, ApiRequest<'HouseholdDedupConfig'>>>,
  Expect<DeepNoPhantomKeys<HouseholdDedupConfig, ApiRequest<'HouseholdDedupConfig'>>>,
  Expect<WireFits<ApiResponse<'HouseholdDedupConfig'>, HouseholdDedupConfig>>,
  Expect<NoPhantomKeys<HouseholdDedupConfig, ApiResponse<'HouseholdDedupConfig'>>>,
  Expect<WireFits<ApiResponse<'PortfolioCreateResponse'>, PortfolioCreateResponse>>,
  Expect<NoPhantomKeys<PortfolioCreateResponse, ApiResponse<'PortfolioCreateResponse'>>>,
  Expect<WireFits<ApiResponse<'PortfolioPreview'>, PortfolioPreview>>,
  Expect<NoPhantomKeys<PortfolioPreview, ApiResponse<'PortfolioPreview'>>>,
  Expect<WireFits<ApiResponse<'SegmentSummary'>, SegmentSummary>>,
  Expect<NoPhantomKeys<SegmentSummary, ApiResponse<'SegmentSummary'>>>,
];
