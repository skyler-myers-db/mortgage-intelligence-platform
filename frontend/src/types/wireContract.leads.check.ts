/**
 * Wire contract, Leads domain (audit 2026-09-21 quality-04, D-api-types-a3 P1):
 * leads, borrower and offers (plus the outreach decisions and Lead Queue filters).
 *
 * Compile-time assertions only, checked by tsc through `include: ["src"]`;
 * nothing imports this file. (i) pairs every schema-named hand type with its
 * schema; (ii) pairs every bound call site whose type argument is not
 * schema-named with its operation. tests/unit/test_frontend_wire_contract.py
 * proves the coverage and the binding. A failing pair carries a dated
 * wire-drift entry tagged to the lane that fixes it at the cause.
 */
import type { ApiBody, ApiOk, ApiRequest, ApiResponse } from './api.gen';
import type { DeepNoPhantomKeys, Equal, Expect, NoPhantomKeys, WireFits } from './wireContract.check';
import type { LeadExportReceipt } from '../lib/apiClients/leadExport';
import type { OutreachApproveRequest, OutreachDraftRequest, OutreachRejectRequest } from '../lib/apiClients/outreach';
import type { WIRE_OFFER_CODES, WIRE_REJECT_REASON_CODES, WireOfferCode, WireRejectReasonCode } from '../lib/outreachWireCodes';
import type { RejectReasonCode } from '../components/mortgage/LeadTable.types';
import type { ApproveResult, OutreachDraftResult, RejectResult } from '../lib/apiTypes';
import type { ApprovalStatus, Borrower360, BorrowerLifecycle, BorrowerProof, LeadSummary, OfferRecommendation, OutreachStatus, ProofReproduceQuery, ProofScoreComponent, WhyPanel } from '../types';
import type { ApprovalRequestBatchView, ApprovalRequestCreate, ApprovalRequestCreated, ApprovalRequestList, ApprovalRequestRow, ApprovalRequestSkip, ApprovalRequestWithdrawBody, ApprovalRequestWithdrawn } from './approvalRequests';

export type WireContractLeads = [
  // (i) schema-named hand types
  Expect<WireFits<ApiResponse<'LeadExportReceipt'>, LeadExportReceipt>>,
  Expect<NoPhantomKeys<LeadExportReceipt, ApiResponse<'LeadExportReceipt'>>>,
  Expect<WireFits<OutreachApproveRequest, ApiRequest<'OutreachApproveRequest'>>>,
  Expect<DeepNoPhantomKeys<OutreachApproveRequest, ApiRequest<'OutreachApproveRequest'>>>,
  Expect<WireFits<OutreachDraftRequest, ApiRequest<'OutreachDraftRequest'>>>,
  Expect<DeepNoPhantomKeys<OutreachDraftRequest, ApiRequest<'OutreachDraftRequest'>>>,
  Expect<WireFits<OutreachRejectRequest, ApiRequest<'OutreachRejectRequest'>>>,
  Expect<DeepNoPhantomKeys<OutreachRejectRequest, ApiRequest<'OutreachRejectRequest'>>>,
  // The approve / reject code vocabularies (lib/outreachWireCodes.ts) equal their wire unions, both ways.
  Expect<Equal<(typeof WIRE_OFFER_CODES)[number], WireOfferCode>>,
  Expect<Equal<WireOfferCode, NonNullable<ApiRequest<'OutreachRejectRequest'>['offer_code']>>>,
  Expect<Equal<(typeof WIRE_REJECT_REASON_CODES)[number], WireRejectReasonCode>>,
  Expect<Equal<RejectReasonCode, WireRejectReasonCode>>,
  Expect<string extends WireOfferCode ? false : true>,
  Expect<string extends RejectReasonCode ? false : true>,
  // The LeadSummary status vocabularies (P3 indexed aliases) stay closed unions.
  Expect<string extends ApprovalStatus ? false : true>,
  Expect<string extends OutreachStatus ? false : true>,
  Expect<WireFits<ApiResponse<'Borrower360'>, Borrower360>>,
  Expect<NoPhantomKeys<Borrower360, ApiResponse<'Borrower360'>>>,
  Expect<WireFits<ApiResponse<'BorrowerProof'>, BorrowerProof>>,
  Expect<NoPhantomKeys<BorrowerProof, ApiResponse<'BorrowerProof'>>>,
  Expect<WireFits<ApiResponse<'LeadSummary'>, LeadSummary>>,
  Expect<NoPhantomKeys<LeadSummary, ApiResponse<'LeadSummary'>>>,
  Expect<WireFits<ApiResponse<'OfferRecommendation'>, OfferRecommendation>>,
  Expect<NoPhantomKeys<OfferRecommendation, ApiResponse<'OfferRecommendation'>>>,
  Expect<WireFits<ApiResponse<'ProofScoreComponent'>, ProofScoreComponent>>,
  Expect<NoPhantomKeys<ProofScoreComponent, ApiResponse<'ProofScoreComponent'>>>,
  Expect<WireFits<ApiResponse<'ProofReproduceQuery'>, ProofReproduceQuery>>,
  Expect<NoPhantomKeys<ProofReproduceQuery, ApiResponse<'ProofReproduceQuery'>>>,
  Expect<WireFits<ApiResponse<'WhyPanel'>, WhyPanel>>,
  Expect<NoPhantomKeys<WhyPanel, ApiResponse<'WhyPanel'>>>,
  // (ii) bound call sites whose type argument is not schema-named
  Expect<WireFits<ApiOk<'GET /api/v1/borrowers/{borrower_id}/lifecycle'>, BorrowerLifecycle>>,
  Expect<NoPhantomKeys<BorrowerLifecycle, ApiOk<'GET /api/v1/borrowers/{borrower_id}/lifecycle'>>>,
  Expect<WireFits<ApiOk<'POST /api/v1/outreach/approve'>, ApproveResult>>,
  Expect<NoPhantomKeys<ApproveResult, ApiOk<'POST /api/v1/outreach/approve'>>>,
  Expect<WireFits<ApiOk<'POST /api/v1/outreach/reject'>, RejectResult>>,
  Expect<NoPhantomKeys<RejectResult, ApiOk<'POST /api/v1/outreach/reject'>>>,
  Expect<WireFits<ApiOk<'POST /api/v1/outreach/draft'>, OutreachDraftResult>>,
  Expect<NoPhantomKeys<OutreachDraftResult, ApiOk<'POST /api/v1/outreach/draft'>>>,
  // Maker-checker approval requests (flow-02 / shell-06, W5d w5-lq-requests-binding).
  Expect<WireFits<ApiResponse<'ApprovalRequestBatchView'>, ApprovalRequestBatchView>>,
  Expect<NoPhantomKeys<ApprovalRequestBatchView, ApiResponse<'ApprovalRequestBatchView'>>>,
  Expect<WireFits<ApprovalRequestCreate, ApiRequest<'ApprovalRequestCreate'>>>,
  Expect<DeepNoPhantomKeys<ApprovalRequestCreate, ApiRequest<'ApprovalRequestCreate'>>>,
  Expect<WireFits<ApiResponse<'ApprovalRequestCreated'>, ApprovalRequestCreated>>,
  Expect<NoPhantomKeys<ApprovalRequestCreated, ApiResponse<'ApprovalRequestCreated'>>>,
  Expect<WireFits<ApiResponse<'ApprovalRequestList'>, ApprovalRequestList>>,
  Expect<NoPhantomKeys<ApprovalRequestList, ApiResponse<'ApprovalRequestList'>>>,
  Expect<WireFits<ApiResponse<'ApprovalRequestRow'>, ApprovalRequestRow>>,
  Expect<NoPhantomKeys<ApprovalRequestRow, ApiResponse<'ApprovalRequestRow'>>>,
  Expect<WireFits<ApiResponse<'ApprovalRequestSkip'>, ApprovalRequestSkip>>,
  Expect<NoPhantomKeys<ApprovalRequestSkip, ApiResponse<'ApprovalRequestSkip'>>>,
  Expect<WireFits<ApiResponse<'ApprovalRequestWithdrawn'>, ApprovalRequestWithdrawn>>,
  Expect<NoPhantomKeys<ApprovalRequestWithdrawn, ApiResponse<'ApprovalRequestWithdrawn'>>>,
  Expect<WireFits<ApprovalRequestWithdrawBody, ApiBody<'POST /api/v1/outreach/approval-requests/{batch_id}/withdraw'>>>,
  Expect<DeepNoPhantomKeys<ApprovalRequestWithdrawBody, ApiBody<'POST /api/v1/outreach/approval-requests/{batch_id}/withdraw'>>>,
];
