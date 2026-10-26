/**
 * Wire contract, GrowthAgent domain (audit 2026-09-21 quality-04, D-api-types-a3 P1):
 * growthAgent.
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
import type { ComposePlanRequest, ComposePlanResponse, ComposedPlan, ExecutePlanRequest, GrowthAgentCustomRunRequest, GrowthAgentDueMonitorRunRequest, GrowthAgentDueMonitorRunResponse, GrowthAgentGovernanceChip, GrowthAgentHomeResponse, GrowthAgentMonitor, GrowthAgentMonitorDraftRequest, GrowthAgentNotificationDraft, GrowthAgentPlanProgressResponse, GrowthAgentPolicyCheck, GrowthAgentPromptRunRequest, GrowthAgentRateWatchRequest, GrowthAgentRateWatchResponse, GrowthAgentRunRequest, GrowthAgentRunResponse, GrowthAgentRunSummary, GrowthAgentRunWatchlistRequest, GrowthAgentSchedulerStatus, GrowthAgentToolStep, GrowthAgentWatchlistBriefing, GrowthAgentWatchlistSummaryResponse, GrowthAgentWorkflow, PlanStep, PlanStepTrace } from './growthAgent';
import type { GrowthAgentReviewedWorkflowId, GrowthAgentWorkflowId } from './growthAgent';

export type WireContractGrowthAgent = [
  // (i) schema-named hand types
  Expect<WireFits<ComposePlanRequest, ApiRequest<'ComposePlanRequest'>>>,
  Expect<DeepNoPhantomKeys<ComposePlanRequest, ApiRequest<'ComposePlanRequest'>>>,
  Expect<WireFits<ApiResponse<'ComposePlanResponse'>, ComposePlanResponse>>,
  Expect<NoPhantomKeys<ComposePlanResponse, ApiResponse<'ComposePlanResponse'>>>,
  Expect<WireFits<ApiResponse<'ComposedPlan'>, ComposedPlan>>,
  Expect<NoPhantomKeys<ComposedPlan, ApiResponse<'ComposedPlan'>>>,
  Expect<WireFits<ExecutePlanRequest, ApiRequest<'ExecutePlanRequest'>>>,
  Expect<DeepNoPhantomKeys<ExecutePlanRequest, ApiRequest<'ExecutePlanRequest'>>>,
  Expect<WireFits<GrowthAgentCustomRunRequest, ApiRequest<'GrowthAgentCustomRunRequest'>>>,
  Expect<DeepNoPhantomKeys<GrowthAgentCustomRunRequest, ApiRequest<'GrowthAgentCustomRunRequest'>>>,
  Expect<WireFits<GrowthAgentDueMonitorRunRequest, ApiRequest<'GrowthAgentDueMonitorRunRequest'>>>,
  Expect<DeepNoPhantomKeys<GrowthAgentDueMonitorRunRequest, ApiRequest<'GrowthAgentDueMonitorRunRequest'>>>,
  Expect<WireFits<ApiResponse<'GrowthAgentDueMonitorRunResponse'>, GrowthAgentDueMonitorRunResponse>>,
  Expect<NoPhantomKeys<GrowthAgentDueMonitorRunResponse, ApiResponse<'GrowthAgentDueMonitorRunResponse'>>>,
  Expect<WireFits<ApiResponse<'GrowthAgentGovernanceChip'>, GrowthAgentGovernanceChip>>,
  Expect<NoPhantomKeys<GrowthAgentGovernanceChip, ApiResponse<'GrowthAgentGovernanceChip'>>>,
  Expect<WireFits<ApiResponse<'GrowthAgentHomeResponse'>, GrowthAgentHomeResponse>>,
  Expect<NoPhantomKeys<GrowthAgentHomeResponse, ApiResponse<'GrowthAgentHomeResponse'>>>,
  Expect<WireFits<ApiResponse<'GrowthAgentMonitor'>, GrowthAgentMonitor>>,
  Expect<NoPhantomKeys<GrowthAgentMonitor, ApiResponse<'GrowthAgentMonitor'>>>,
  Expect<WireFits<GrowthAgentMonitorDraftRequest, ApiRequest<'GrowthAgentMonitorDraftRequest'>>>,
  Expect<DeepNoPhantomKeys<GrowthAgentMonitorDraftRequest, ApiRequest<'GrowthAgentMonitorDraftRequest'>>>,
  Expect<WireFits<ApiResponse<'GrowthAgentNotificationDraft'>, GrowthAgentNotificationDraft>>,
  Expect<NoPhantomKeys<GrowthAgentNotificationDraft, ApiResponse<'GrowthAgentNotificationDraft'>>>,
  Expect<WireFits<ApiResponse<'GrowthAgentPlanProgressResponse'>, GrowthAgentPlanProgressResponse>>,
  Expect<NoPhantomKeys<GrowthAgentPlanProgressResponse, ApiResponse<'GrowthAgentPlanProgressResponse'>>>,
  Expect<WireFits<ApiResponse<'GrowthAgentPolicyCheck'>, GrowthAgentPolicyCheck>>,
  Expect<NoPhantomKeys<GrowthAgentPolicyCheck, ApiResponse<'GrowthAgentPolicyCheck'>>>,
  Expect<WireFits<GrowthAgentPromptRunRequest, ApiRequest<'GrowthAgentPromptRunRequest'>>>,
  Expect<DeepNoPhantomKeys<GrowthAgentPromptRunRequest, ApiRequest<'GrowthAgentPromptRunRequest'>>>,
  Expect<WireFits<GrowthAgentRateWatchRequest, ApiRequest<'GrowthAgentRateWatchRequest'>>>,
  Expect<DeepNoPhantomKeys<GrowthAgentRateWatchRequest, ApiRequest<'GrowthAgentRateWatchRequest'>>>,
  Expect<WireFits<ApiResponse<'GrowthAgentRateWatchResponse'>, GrowthAgentRateWatchResponse>>,
  Expect<NoPhantomKeys<GrowthAgentRateWatchResponse, ApiResponse<'GrowthAgentRateWatchResponse'>>>,
  Expect<WireFits<GrowthAgentRunRequest, ApiRequest<'GrowthAgentRunRequest'>>>,
  Expect<DeepNoPhantomKeys<GrowthAgentRunRequest, ApiRequest<'GrowthAgentRunRequest'>>>,
  Expect<WireFits<ApiResponse<'GrowthAgentRunResponse'>, GrowthAgentRunResponse>>,
  Expect<NoPhantomKeys<GrowthAgentRunResponse, ApiResponse<'GrowthAgentRunResponse'>>>,
  Expect<WireFits<ApiResponse<'GrowthAgentRunSummary'>, GrowthAgentRunSummary>>,
  Expect<NoPhantomKeys<GrowthAgentRunSummary, ApiResponse<'GrowthAgentRunSummary'>>>,
  Expect<WireFits<GrowthAgentRunWatchlistRequest, ApiRequest<'GrowthAgentRunWatchlistRequest'>>>,
  Expect<DeepNoPhantomKeys<GrowthAgentRunWatchlistRequest, ApiRequest<'GrowthAgentRunWatchlistRequest'>>>,
  Expect<WireFits<ApiResponse<'GrowthAgentSchedulerStatus'>, GrowthAgentSchedulerStatus>>,
  Expect<NoPhantomKeys<GrowthAgentSchedulerStatus, ApiResponse<'GrowthAgentSchedulerStatus'>>>,
  Expect<WireFits<ApiResponse<'GrowthAgentToolStep'>, GrowthAgentToolStep>>,
  Expect<NoPhantomKeys<GrowthAgentToolStep, ApiResponse<'GrowthAgentToolStep'>>>,
  Expect<WireFits<ApiResponse<'GrowthAgentWatchlistBriefing'>, GrowthAgentWatchlistBriefing>>,
  Expect<NoPhantomKeys<GrowthAgentWatchlistBriefing, ApiResponse<'GrowthAgentWatchlistBriefing'>>>,
  Expect<WireFits<ApiResponse<'GrowthAgentWatchlistSummaryResponse'>, GrowthAgentWatchlistSummaryResponse>>,
  Expect<NoPhantomKeys<GrowthAgentWatchlistSummaryResponse, ApiResponse<'GrowthAgentWatchlistSummaryResponse'>>>,
  Expect<WireFits<ApiResponse<'GrowthAgentWorkflow'>, GrowthAgentWorkflow>>,
  Expect<NoPhantomKeys<GrowthAgentWorkflow, ApiResponse<'GrowthAgentWorkflow'>>>,
  Expect<WireFits<ApiResponse<'PlanStep'>, PlanStep>>,
  Expect<NoPhantomKeys<PlanStep, ApiResponse<'PlanStep'>>>,
  Expect<WireFits<ApiResponse<'PlanStepTrace'>, PlanStepTrace>>,
  Expect<NoPhantomKeys<PlanStepTrace, ApiResponse<'PlanStepTrace'>>>,
  // The workflow-id vocabularies stay closed unions (never widened to string).
  Expect<string extends GrowthAgentWorkflowId ? false : true>,
  Expect<string extends GrowthAgentReviewedWorkflowId ? false : true>,
];
