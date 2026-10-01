/**
 * Wire contract, Genie domain (audit 2026-09-21 quality-04, D-api-types-a3 P1):
 * genie and genieJobs.
 *
 * Compile-time assertions only, checked by tsc through `include: ["src"]`;
 * nothing imports this file. (i) pairs every schema-named hand type with its
 * schema; (ii) pairs every bound call site whose type argument is not
 * schema-named with its operation. tests/unit/test_frontend_wire_contract.py
 * proves the coverage and the binding. A failing pair carries a dated
 * wire-drift entry tagged to the lane that fixes it at the cause.
 */
import type { ApiBody, ApiOk, ApiRequest, ApiResponse } from './api.gen';
import type { DeepNoPhantomKeys, Expect, NoPhantomKeys, WireFits } from './wireContract.check';
import type { GenieActionRequest, GenieCompleteAsyncRequest, GenieFeedbackRequest, GenieMessageRequest, GenieProgressRequest, GenieRefusalReportRequest, GenieSessionListResponse, GenieStartBody } from '../lib/apiClients/genie';
import type { GenieAnswerExportReceipt, GenieAnswerExportReceiptRequest } from '../lib/apiClients/genieExport';
import type { GenieCancelBody, GenieCompleteAsyncJobBody, GenieCompleteAsyncResult, GenieCompletionJobStatusRequest } from '../lib/apiClients/genieJobs';
import type { GenieFeedbackResult, GenieLiveProgress, GenieRefusalReportResult, GenieResult, GenieSubmitResult } from '../lib/apiTypes';
import type { GenieActionResult, GenieActionSuggestion, GenieAnswerSection, GenieClaimsSummary, GenieNativeVisualization, GenieProof, GenieReasoningStep, GenieSessionDetail, GenieSessionSummary, GenieStartResult, GenieVerifiedClaim } from './genie';
import type { GenieCancelResult, GenieCompletionJobStatus, GenieJobStage } from './genieJobs';

export type WireContractGenie = [
  // (i) schema-named hand types
  Expect<WireFits<GenieActionRequest, ApiRequest<'GenieActionRequest'>>>,
  Expect<DeepNoPhantomKeys<GenieActionRequest, ApiRequest<'GenieActionRequest'>>>,
  Expect<WireFits<GenieCompleteAsyncRequest, ApiRequest<'GenieCompleteAsyncRequest'>>>,
  Expect<DeepNoPhantomKeys<GenieCompleteAsyncRequest, ApiRequest<'GenieCompleteAsyncRequest'>>>,
  Expect<WireFits<GenieFeedbackRequest, ApiRequest<'GenieFeedbackRequest'>>>,
  Expect<DeepNoPhantomKeys<GenieFeedbackRequest, ApiRequest<'GenieFeedbackRequest'>>>,
  Expect<WireFits<GenieMessageRequest, ApiRequest<'GenieMessageRequest'>>>,
  Expect<DeepNoPhantomKeys<GenieMessageRequest, ApiRequest<'GenieMessageRequest'>>>,
  Expect<WireFits<GenieProgressRequest, ApiRequest<'GenieProgressRequest'>>>,
  Expect<DeepNoPhantomKeys<GenieProgressRequest, ApiRequest<'GenieProgressRequest'>>>,
  Expect<WireFits<GenieRefusalReportRequest, ApiRequest<'GenieRefusalReportRequest'>>>,
  Expect<DeepNoPhantomKeys<GenieRefusalReportRequest, ApiRequest<'GenieRefusalReportRequest'>>>,
  Expect<WireFits<ApiResponse<'GenieSessionListResponse'>, GenieSessionListResponse>>,
  Expect<NoPhantomKeys<GenieSessionListResponse, ApiResponse<'GenieSessionListResponse'>>>,
  Expect<WireFits<ApiResponse<'GenieAnswerExportReceipt'>, GenieAnswerExportReceipt>>,
  Expect<NoPhantomKeys<GenieAnswerExportReceipt, ApiResponse<'GenieAnswerExportReceipt'>>>,
  Expect<WireFits<GenieAnswerExportReceiptRequest, ApiRequest<'GenieAnswerExportReceiptRequest'>>>,
  Expect<DeepNoPhantomKeys<GenieAnswerExportReceiptRequest, ApiRequest<'GenieAnswerExportReceiptRequest'>>>,
  Expect<WireFits<GenieCompletionJobStatusRequest, ApiRequest<'GenieCompletionJobStatusRequest'>>>,
  Expect<DeepNoPhantomKeys<GenieCompletionJobStatusRequest, ApiRequest<'GenieCompletionJobStatusRequest'>>>,
  Expect<WireFits<ApiResponse<'GenieActionSuggestion'>, GenieActionSuggestion>>,
  Expect<NoPhantomKeys<GenieActionSuggestion, ApiResponse<'GenieActionSuggestion'>>>,
  Expect<WireFits<ApiResponse<'GenieAnswerSection'>, GenieAnswerSection>>,
  Expect<NoPhantomKeys<GenieAnswerSection, ApiResponse<'GenieAnswerSection'>>>,
  Expect<WireFits<ApiResponse<'GenieNativeVisualization'>, GenieNativeVisualization>>,
  Expect<NoPhantomKeys<GenieNativeVisualization, ApiResponse<'GenieNativeVisualization'>>>,
  Expect<WireFits<ApiResponse<'GenieProof'>, GenieProof>>,
  Expect<NoPhantomKeys<GenieProof, ApiResponse<'GenieProof'>>>,
  Expect<WireFits<ApiResponse<'GenieReasoningStep'>, GenieReasoningStep>>,
  Expect<NoPhantomKeys<GenieReasoningStep, ApiResponse<'GenieReasoningStep'>>>,
  Expect<WireFits<ApiResponse<'GenieSessionSummary'>, GenieSessionSummary>>,
  Expect<NoPhantomKeys<GenieSessionSummary, ApiResponse<'GenieSessionSummary'>>>,
  Expect<WireFits<ApiResponse<'GenieCompletionJobStatus'>, GenieCompletionJobStatus>>,
  Expect<NoPhantomKeys<GenieCompletionJobStatus, ApiResponse<'GenieCompletionJobStatus'>>>,
  Expect<WireFits<ApiResponse<'GenieJobStage'>, GenieJobStage>>,
  Expect<NoPhantomKeys<GenieJobStage, ApiResponse<'GenieJobStage'>>>,
  Expect<WireFits<ApiResponse<'GenieClaimsSummary'>, GenieClaimsSummary>>,
  Expect<NoPhantomKeys<GenieClaimsSummary, ApiResponse<'GenieClaimsSummary'>>>,
  Expect<WireFits<ApiResponse<'GenieVerifiedClaim'>, GenieVerifiedClaim>>,
  Expect<NoPhantomKeys<GenieVerifiedClaim, ApiResponse<'GenieVerifiedClaim'>>>,
  // (ii) bound call sites whose type argument is not schema-named
  Expect<WireFits<ApiOk<'POST /api/v1/genie/message'>, GenieResult>>,
  Expect<NoPhantomKeys<GenieResult, ApiOk<'POST /api/v1/genie/message'>>>,
  // @ts-expect-error wire-drift quality-04 2026-10-01 w5-genie-stop-context: the complete ok is GenieMessageResponse | GenieCompletionJobStatus; the sync call never sends respond_async, but GenieResult covers only the first member
  Expect<WireFits<ApiOk<'POST /api/v1/genie/message/complete'>, GenieResult>>,
  // @ts-expect-error wire-drift quality-04 2026-10-01 w5-genie-stop-context: against the union ok only the keys both members share are wire keys, so every GenieResult answer field reads as phantom
  Expect<NoPhantomKeys<GenieResult, ApiOk<'POST /api/v1/genie/message/complete'>>>,
  Expect<WireFits<ApiOk<'POST /api/v1/genie/message/submit'>, GenieSubmitResult>>,
  Expect<NoPhantomKeys<GenieSubmitResult, ApiOk<'POST /api/v1/genie/message/submit'>>>,
  Expect<WireFits<ApiOk<'POST /api/v1/genie/message/progress'>, GenieLiveProgress>>,
  // @ts-expect-error wire-drift quality-04 2026-10-01 w5-genie-stop-context: GenieLiveProgress declares deep, which askGenieLive stamps client-side and GenieProgressResponse does not carry
  Expect<NoPhantomKeys<GenieLiveProgress, ApiOk<'POST /api/v1/genie/message/progress'>>>,
  Expect<WireFits<ApiOk<'POST /api/v1/genie/feedback'>, GenieFeedbackResult>>,
  Expect<NoPhantomKeys<GenieFeedbackResult, ApiOk<'POST /api/v1/genie/feedback'>>>,
  Expect<WireFits<ApiOk<'POST /api/v1/genie/refusal-report'>, GenieRefusalReportResult>>,
  Expect<NoPhantomKeys<GenieRefusalReportResult, ApiOk<'POST /api/v1/genie/refusal-report'>>>,
  Expect<WireFits<ApiOk<'POST /api/v1/genie/start'>, GenieStartResult>>,
  Expect<NoPhantomKeys<GenieStartResult, ApiOk<'POST /api/v1/genie/start'>>>,
  Expect<WireFits<ApiOk<'GET /api/v1/genie/sessions/{conversation_id}'>, GenieSessionDetail>>,
  Expect<NoPhantomKeys<GenieSessionDetail, ApiOk<'GET /api/v1/genie/sessions/{conversation_id}'>>>,
  Expect<WireFits<ApiOk<'POST /api/v1/genie/actions'>, GenieActionResult>>,
  Expect<NoPhantomKeys<GenieActionResult, ApiOk<'POST /api/v1/genie/actions'>>>,
  Expect<WireFits<ApiOk<'POST /api/v1/genie/message/cancel'>, GenieCancelResult>>,
  Expect<NoPhantomKeys<GenieCancelResult, ApiOk<'POST /api/v1/genie/message/cancel'>>>,
  Expect<WireFits<ApiOk<'POST /api/v1/genie/message/complete'>, GenieCompleteAsyncResult>>,
  Expect<NoPhantomKeys<GenieCompleteAsyncResult, ApiOk<'POST /api/v1/genie/message/complete'>>>,
  Expect<WireFits<GenieStartBody, ApiBody<'POST /api/v1/genie/start'>>>,
  Expect<DeepNoPhantomKeys<GenieStartBody, ApiBody<'POST /api/v1/genie/start'>>>,
  Expect<WireFits<GenieCompleteAsyncJobBody, ApiBody<'POST /api/v1/genie/message/complete'>>>,
  Expect<DeepNoPhantomKeys<GenieCompleteAsyncJobBody, ApiBody<'POST /api/v1/genie/message/complete'>>>,
  Expect<WireFits<GenieCancelBody, ApiBody<'POST /api/v1/genie/message/cancel'>>>,
  Expect<DeepNoPhantomKeys<GenieCancelBody, ApiBody<'POST /api/v1/genie/message/cancel'>>>,
];
