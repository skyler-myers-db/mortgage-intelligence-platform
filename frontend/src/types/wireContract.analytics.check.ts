/**
 * Wire contract, Analytics domain (audit 2026-09-21 quality-04, D-api-types-a3 P1):
 * geo, economicsScatter, executiveAnalytics, rateWindow, rateScenario, homeSummary, approvalFunnel, campaign, campaignPerformance and proofMargins.
 *
 * Compile-time assertions only, checked by tsc through `include: ["src"]`;
 * nothing imports this file. (i) pairs every schema-named hand type with its
 * schema; (ii) pairs every bound call site whose type argument is not
 * schema-named with its operation. tests/unit/test_frontend_wire_contract.py
 * proves the coverage and the binding. A failing pair carries a dated
 * wire-drift entry tagged to the lane that fixes it at the cause.
 */
import type { ApiOk, ApiResponse } from './api.gen';
import type { Expect, NoPhantomKeys, WireFits } from './wireContract.check';
import type { GeoAssignmentOverlayResponse, GeoAssignmentOverlayUnit } from '../lib/apiTypes';
import type { AnalyticsScope, EconomicsAnalyticsResponse, EvidenceBySignalRow, EvidenceDailyRow, GeographyAnalyticsResponse, KpiTrend, RateSpreadBucket, SegmentAnalyticsResponse, SegmentByStateRow, SegmentMetricRow, SegmentOverviewRow, SignalAnalyticsResponse, SignalEvidenceExample, StateAvmValueRow, StateOpportunityRow, TopBorrowerAnalyticsRow, TopSegmentByStateRow, TopZipOpportunityRow } from '../types';
import type { ApprovalFunnelResponse, ApprovalFunnelStage, ApproverActivityRow, AssignmentOutcomeCounts, AssignmentOutcomeResponse, LoanOfficerFunnelDetailResponse, LoanOfficerFunnelRow } from './approvalFunnel';
import type { CampaignRecommendationResponse } from './campaign';
import type { CampaignPerformanceFunnelResponse } from './campaignPerformance';
import type { AnalyticsThresholds, EquitySpreadBin, EquitySpreadOverview, EquitySpreadPoint, EquitySpreadPointsResponse, EquitySpreadViewport } from './economicsScatter';
import type { ExecutiveAnalyticsResponse, ExecutiveProvenance, FunnelStage, FunnelTotals, ScoreBucket } from './executiveAnalytics';
import type { CountyRollup, CountyRollupResponse, StateRollup, StateRollupResponse, ZipRollup, ZipRollupResponse } from './geo';
import type { HomeAttributionRate, HomeAttributionState, HomeSummaryAttributionResponse } from './homeAttribution';
import type { HomeSummary, HomeSummaryHighlight } from './homeSummary';
import type { ProofMargin } from './proofMargins';
import type { RateSensitivityProvenance, RateSensitivityResponse, RateSensitivityState, RateSensitivityThresholds } from './rateScenario';
import type { RateWindowProvenance, RateWindowResponse, RateWindowThresholds, RateWindowWeek } from './rateWindow';

export type WireContractAnalytics = [
  // (i) schema-named hand types
  Expect<WireFits<ApiResponse<'GeoAssignmentOverlayResponse'>, GeoAssignmentOverlayResponse>>,
  Expect<NoPhantomKeys<GeoAssignmentOverlayResponse, ApiResponse<'GeoAssignmentOverlayResponse'>>>,
  Expect<WireFits<ApiResponse<'GeoAssignmentOverlayUnit'>, GeoAssignmentOverlayUnit>>,
  Expect<NoPhantomKeys<GeoAssignmentOverlayUnit, ApiResponse<'GeoAssignmentOverlayUnit'>>>,
  Expect<WireFits<ApiResponse<'AnalyticsScope'>, AnalyticsScope>>,
  Expect<NoPhantomKeys<AnalyticsScope, ApiResponse<'AnalyticsScope'>>>,
  Expect<WireFits<ApiResponse<'EconomicsAnalyticsResponse'>, EconomicsAnalyticsResponse>>,
  Expect<NoPhantomKeys<EconomicsAnalyticsResponse, ApiResponse<'EconomicsAnalyticsResponse'>>>,
  Expect<WireFits<ApiResponse<'EvidenceBySignalRow'>, EvidenceBySignalRow>>,
  Expect<NoPhantomKeys<EvidenceBySignalRow, ApiResponse<'EvidenceBySignalRow'>>>,
  Expect<WireFits<ApiResponse<'EvidenceDailyRow'>, EvidenceDailyRow>>,
  Expect<NoPhantomKeys<EvidenceDailyRow, ApiResponse<'EvidenceDailyRow'>>>,
  Expect<WireFits<ApiResponse<'GeographyAnalyticsResponse'>, GeographyAnalyticsResponse>>,
  Expect<NoPhantomKeys<GeographyAnalyticsResponse, ApiResponse<'GeographyAnalyticsResponse'>>>,
  // @ts-expect-error wire-drift quality-04 2026-10-01 w5-wire-types: direction is string on the wire but up | down | flat in the hand type
  Expect<WireFits<ApiResponse<'KpiTrend'>, KpiTrend>>,
  Expect<NoPhantomKeys<KpiTrend, ApiResponse<'KpiTrend'>>>,
  Expect<WireFits<ApiResponse<'RateSpreadBucket'>, RateSpreadBucket>>,
  Expect<NoPhantomKeys<RateSpreadBucket, ApiResponse<'RateSpreadBucket'>>>,
  Expect<WireFits<ApiResponse<'SegmentAnalyticsResponse'>, SegmentAnalyticsResponse>>,
  Expect<NoPhantomKeys<SegmentAnalyticsResponse, ApiResponse<'SegmentAnalyticsResponse'>>>,
  Expect<WireFits<ApiResponse<'SegmentByStateRow'>, SegmentByStateRow>>,
  Expect<NoPhantomKeys<SegmentByStateRow, ApiResponse<'SegmentByStateRow'>>>,
  Expect<WireFits<ApiResponse<'SegmentMetricRow'>, SegmentMetricRow>>,
  Expect<NoPhantomKeys<SegmentMetricRow, ApiResponse<'SegmentMetricRow'>>>,
  Expect<WireFits<ApiResponse<'SegmentOverviewRow'>, SegmentOverviewRow>>,
  Expect<NoPhantomKeys<SegmentOverviewRow, ApiResponse<'SegmentOverviewRow'>>>,
  Expect<WireFits<ApiResponse<'SignalAnalyticsResponse'>, SignalAnalyticsResponse>>,
  Expect<NoPhantomKeys<SignalAnalyticsResponse, ApiResponse<'SignalAnalyticsResponse'>>>,
  Expect<WireFits<ApiResponse<'SignalEvidenceExample'>, SignalEvidenceExample>>,
  Expect<NoPhantomKeys<SignalEvidenceExample, ApiResponse<'SignalEvidenceExample'>>>,
  Expect<WireFits<ApiResponse<'StateAvmValueRow'>, StateAvmValueRow>>,
  Expect<NoPhantomKeys<StateAvmValueRow, ApiResponse<'StateAvmValueRow'>>>,
  Expect<WireFits<ApiResponse<'StateOpportunityRow'>, StateOpportunityRow>>,
  Expect<NoPhantomKeys<StateOpportunityRow, ApiResponse<'StateOpportunityRow'>>>,
  Expect<WireFits<ApiResponse<'TopBorrowerAnalyticsRow'>, TopBorrowerAnalyticsRow>>,
  Expect<NoPhantomKeys<TopBorrowerAnalyticsRow, ApiResponse<'TopBorrowerAnalyticsRow'>>>,
  Expect<WireFits<ApiResponse<'TopSegmentByStateRow'>, TopSegmentByStateRow>>,
  Expect<NoPhantomKeys<TopSegmentByStateRow, ApiResponse<'TopSegmentByStateRow'>>>,
  Expect<WireFits<ApiResponse<'TopZipOpportunityRow'>, TopZipOpportunityRow>>,
  Expect<NoPhantomKeys<TopZipOpportunityRow, ApiResponse<'TopZipOpportunityRow'>>>,
  Expect<WireFits<ApiResponse<'ApprovalFunnelResponse'>, ApprovalFunnelResponse>>,
  Expect<NoPhantomKeys<ApprovalFunnelResponse, ApiResponse<'ApprovalFunnelResponse'>>>,
  Expect<WireFits<ApiResponse<'ApprovalFunnelStage'>, ApprovalFunnelStage>>,
  Expect<NoPhantomKeys<ApprovalFunnelStage, ApiResponse<'ApprovalFunnelStage'>>>,
  Expect<WireFits<ApiResponse<'ApproverActivityRow'>, ApproverActivityRow>>,
  Expect<NoPhantomKeys<ApproverActivityRow, ApiResponse<'ApproverActivityRow'>>>,
  Expect<WireFits<ApiResponse<'AssignmentOutcomeCounts'>, AssignmentOutcomeCounts>>,
  Expect<NoPhantomKeys<AssignmentOutcomeCounts, ApiResponse<'AssignmentOutcomeCounts'>>>,
  Expect<WireFits<ApiResponse<'AssignmentOutcomeResponse'>, AssignmentOutcomeResponse>>,
  Expect<NoPhantomKeys<AssignmentOutcomeResponse, ApiResponse<'AssignmentOutcomeResponse'>>>,
  Expect<WireFits<ApiResponse<'LoanOfficerFunnelDetailResponse'>, LoanOfficerFunnelDetailResponse>>,
  Expect<NoPhantomKeys<LoanOfficerFunnelDetailResponse, ApiResponse<'LoanOfficerFunnelDetailResponse'>>>,
  Expect<WireFits<ApiResponse<'LoanOfficerFunnelRow'>, LoanOfficerFunnelRow>>,
  Expect<NoPhantomKeys<LoanOfficerFunnelRow, ApiResponse<'LoanOfficerFunnelRow'>>>,
  Expect<WireFits<ApiResponse<'CampaignRecommendationResponse'>, CampaignRecommendationResponse>>,
  Expect<NoPhantomKeys<CampaignRecommendationResponse, ApiResponse<'CampaignRecommendationResponse'>>>,
  Expect<WireFits<ApiResponse<'CampaignPerformanceFunnelResponse'>, CampaignPerformanceFunnelResponse>>,
  Expect<NoPhantomKeys<CampaignPerformanceFunnelResponse, ApiResponse<'CampaignPerformanceFunnelResponse'>>>,
  Expect<WireFits<ApiResponse<'AnalyticsThresholds'>, AnalyticsThresholds>>,
  Expect<NoPhantomKeys<AnalyticsThresholds, ApiResponse<'AnalyticsThresholds'>>>,
  Expect<WireFits<ApiResponse<'EquitySpreadBin'>, EquitySpreadBin>>,
  Expect<NoPhantomKeys<EquitySpreadBin, ApiResponse<'EquitySpreadBin'>>>,
  Expect<WireFits<ApiResponse<'EquitySpreadOverview'>, EquitySpreadOverview>>,
  Expect<NoPhantomKeys<EquitySpreadOverview, ApiResponse<'EquitySpreadOverview'>>>,
  Expect<WireFits<ApiResponse<'EquitySpreadPoint'>, EquitySpreadPoint>>,
  Expect<NoPhantomKeys<EquitySpreadPoint, ApiResponse<'EquitySpreadPoint'>>>,
  Expect<WireFits<ApiResponse<'EquitySpreadPointsResponse'>, EquitySpreadPointsResponse>>,
  Expect<NoPhantomKeys<EquitySpreadPointsResponse, ApiResponse<'EquitySpreadPointsResponse'>>>,
  Expect<WireFits<ApiResponse<'EquitySpreadViewport'>, EquitySpreadViewport>>,
  Expect<NoPhantomKeys<EquitySpreadViewport, ApiResponse<'EquitySpreadViewport'>>>,
  Expect<WireFits<ApiResponse<'ExecutiveAnalyticsResponse'>, ExecutiveAnalyticsResponse>>,
  Expect<NoPhantomKeys<ExecutiveAnalyticsResponse, ApiResponse<'ExecutiveAnalyticsResponse'>>>,
  Expect<WireFits<ApiResponse<'ExecutiveProvenance'>, ExecutiveProvenance>>,
  Expect<NoPhantomKeys<ExecutiveProvenance, ApiResponse<'ExecutiveProvenance'>>>,
  Expect<WireFits<ApiResponse<'FunnelStage'>, FunnelStage>>,
  Expect<NoPhantomKeys<FunnelStage, ApiResponse<'FunnelStage'>>>,
  Expect<WireFits<ApiResponse<'FunnelTotals'>, FunnelTotals>>,
  Expect<NoPhantomKeys<FunnelTotals, ApiResponse<'FunnelTotals'>>>,
  Expect<WireFits<ApiResponse<'ScoreBucket'>, ScoreBucket>>,
  Expect<NoPhantomKeys<ScoreBucket, ApiResponse<'ScoreBucket'>>>,
  Expect<WireFits<ApiResponse<'CountyRollup'>, CountyRollup>>,
  Expect<NoPhantomKeys<CountyRollup, ApiResponse<'CountyRollup'>>>,
  Expect<WireFits<ApiResponse<'CountyRollupResponse'>, CountyRollupResponse>>,
  Expect<NoPhantomKeys<CountyRollupResponse, ApiResponse<'CountyRollupResponse'>>>,
  Expect<WireFits<ApiResponse<'StateRollup'>, StateRollup>>,
  Expect<NoPhantomKeys<StateRollup, ApiResponse<'StateRollup'>>>,
  Expect<WireFits<ApiResponse<'StateRollupResponse'>, StateRollupResponse>>,
  Expect<NoPhantomKeys<StateRollupResponse, ApiResponse<'StateRollupResponse'>>>,
  Expect<WireFits<ApiResponse<'ZipRollup'>, ZipRollup>>,
  Expect<NoPhantomKeys<ZipRollup, ApiResponse<'ZipRollup'>>>,
  Expect<WireFits<ApiResponse<'ZipRollupResponse'>, ZipRollupResponse>>,
  Expect<NoPhantomKeys<ZipRollupResponse, ApiResponse<'ZipRollupResponse'>>>,
  Expect<WireFits<ApiResponse<'HomeSummaryHighlight'>, HomeSummaryHighlight>>,
  Expect<NoPhantomKeys<HomeSummaryHighlight, ApiResponse<'HomeSummaryHighlight'>>>,
  Expect<WireFits<ApiResponse<'HomeAttributionRate'>, HomeAttributionRate>>,
  Expect<NoPhantomKeys<HomeAttributionRate, ApiResponse<'HomeAttributionRate'>>>,
  Expect<WireFits<ApiResponse<'HomeAttributionState'>, HomeAttributionState>>,
  Expect<NoPhantomKeys<HomeAttributionState, ApiResponse<'HomeAttributionState'>>>,
  Expect<WireFits<ApiResponse<'HomeSummaryAttributionResponse'>, HomeSummaryAttributionResponse>>,
  Expect<NoPhantomKeys<HomeSummaryAttributionResponse, ApiResponse<'HomeSummaryAttributionResponse'>>>,
  Expect<WireFits<ApiResponse<'ProofMargin'>, ProofMargin>>,
  Expect<NoPhantomKeys<ProofMargin, ApiResponse<'ProofMargin'>>>,
  Expect<WireFits<ApiResponse<'RateSensitivityProvenance'>, RateSensitivityProvenance>>,
  Expect<NoPhantomKeys<RateSensitivityProvenance, ApiResponse<'RateSensitivityProvenance'>>>,
  Expect<WireFits<ApiResponse<'RateSensitivityResponse'>, RateSensitivityResponse>>,
  Expect<NoPhantomKeys<RateSensitivityResponse, ApiResponse<'RateSensitivityResponse'>>>,
  Expect<WireFits<ApiResponse<'RateSensitivityState'>, RateSensitivityState>>,
  Expect<NoPhantomKeys<RateSensitivityState, ApiResponse<'RateSensitivityState'>>>,
  Expect<WireFits<ApiResponse<'RateSensitivityThresholds'>, RateSensitivityThresholds>>,
  Expect<NoPhantomKeys<RateSensitivityThresholds, ApiResponse<'RateSensitivityThresholds'>>>,
  Expect<WireFits<ApiResponse<'RateWindowProvenance'>, RateWindowProvenance>>,
  Expect<NoPhantomKeys<RateWindowProvenance, ApiResponse<'RateWindowProvenance'>>>,
  Expect<WireFits<ApiResponse<'RateWindowResponse'>, RateWindowResponse>>,
  Expect<NoPhantomKeys<RateWindowResponse, ApiResponse<'RateWindowResponse'>>>,
  Expect<WireFits<ApiResponse<'RateWindowThresholds'>, RateWindowThresholds>>,
  Expect<NoPhantomKeys<RateWindowThresholds, ApiResponse<'RateWindowThresholds'>>>,
  Expect<WireFits<ApiResponse<'RateWindowWeek'>, RateWindowWeek>>,
  Expect<NoPhantomKeys<RateWindowWeek, ApiResponse<'RateWindowWeek'>>>,
  // (ii) bound call sites whose type argument is not schema-named
  Expect<WireFits<ApiOk<'GET /api/v1/home/summary'>, HomeSummary>>,
  Expect<NoPhantomKeys<HomeSummary, ApiOk<'GET /api/v1/home/summary'>>>,
];
