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
import type { ApiOk, ApiRequest, ApiResponse } from './api.gen';
import type { DeepNoPhantomKeys, Expect, NoPhantomKeys, WireFits } from './wireContract.check';
import type { LeadExportReceipt, LeadExportReceiptRequest } from '../lib/apiClients/leadExport';
import type { OfferRecommendRequest } from '../lib/apiClients/leads';
import type { OutreachApproveRequest, OutreachDraftRequest, OutreachRejectRequest } from '../lib/apiClients/outreach';
import type { ApproveResult, OutreachDraftResult, RejectResult } from '../lib/apiTypes';
import type { Borrower360, BorrowerLifecycle, BorrowerProof, EvidenceEvent, LeadSummary, OfferAlternative, OfferRecommendation, ProofEvidenceEvent, ProofFormulaLine, ProofOfferBranch, ProofReproduceQuery, ProofScoreComponent, SourceLabel, WhyPanel } from '../types';
import type { LeadCountResponse, LeadFacetBucket, LeadFacetsResponse, SavedView, SavedViewCreateRequest, SavedViewListResponse, SavedViewMutationResponse } from './leadFilters';

export type WireContractLeads = [
  // (i) schema-named hand types
  Expect<WireFits<ApiResponse<'LeadExportReceipt'>, LeadExportReceipt>>,
  Expect<NoPhantomKeys<LeadExportReceipt, ApiResponse<'LeadExportReceipt'>>>,
  Expect<WireFits<LeadExportReceiptRequest, ApiRequest<'LeadExportReceiptRequest'>>>,
  Expect<DeepNoPhantomKeys<LeadExportReceiptRequest, ApiRequest<'LeadExportReceiptRequest'>>>,
  Expect<WireFits<OfferRecommendRequest, ApiRequest<'OfferRecommendRequest'>>>,
  Expect<DeepNoPhantomKeys<OfferRecommendRequest, ApiRequest<'OfferRecommendRequest'>>>,
  // @ts-expect-error wire-drift quality-04 2026-10-01 w5-lead-queue-paging: offer_code is string | null in the client body but a closed offer-code union | null on the wire
  Expect<WireFits<OutreachApproveRequest, ApiRequest<'OutreachApproveRequest'>>>,
  Expect<DeepNoPhantomKeys<OutreachApproveRequest, ApiRequest<'OutreachApproveRequest'>>>,
  Expect<WireFits<OutreachDraftRequest, ApiRequest<'OutreachDraftRequest'>>>,
  Expect<DeepNoPhantomKeys<OutreachDraftRequest, ApiRequest<'OutreachDraftRequest'>>>,
  // @ts-expect-error wire-drift quality-04 2026-10-01 w5-lead-queue-paging: offer_code is string | null and rationale_code is string in the client body, closed literal unions on the wire
  Expect<WireFits<OutreachRejectRequest, ApiRequest<'OutreachRejectRequest'>>>,
  Expect<DeepNoPhantomKeys<OutreachRejectRequest, ApiRequest<'OutreachRejectRequest'>>>,
  Expect<WireFits<ApiResponse<'Borrower360'>, Borrower360>>,
  Expect<NoPhantomKeys<Borrower360, ApiResponse<'Borrower360'>>>,
  Expect<WireFits<ApiResponse<'BorrowerProof'>, BorrowerProof>>,
  Expect<NoPhantomKeys<BorrowerProof, ApiResponse<'BorrowerProof'>>>,
  Expect<WireFits<ApiResponse<'EvidenceEvent'>, EvidenceEvent>>,
  Expect<NoPhantomKeys<EvidenceEvent, ApiResponse<'EvidenceEvent'>>>,
  Expect<WireFits<ApiResponse<'LeadSummary'>, LeadSummary>>,
  Expect<NoPhantomKeys<LeadSummary, ApiResponse<'LeadSummary'>>>,
  Expect<WireFits<ApiResponse<'OfferAlternative'>, OfferAlternative>>,
  Expect<NoPhantomKeys<OfferAlternative, ApiResponse<'OfferAlternative'>>>,
  Expect<WireFits<ApiResponse<'OfferRecommendation'>, OfferRecommendation>>,
  Expect<NoPhantomKeys<OfferRecommendation, ApiResponse<'OfferRecommendation'>>>,
  Expect<WireFits<ApiResponse<'ProofEvidenceEvent'>, ProofEvidenceEvent>>,
  Expect<NoPhantomKeys<ProofEvidenceEvent, ApiResponse<'ProofEvidenceEvent'>>>,
  Expect<WireFits<ApiResponse<'ProofFormulaLine'>, ProofFormulaLine>>,
  Expect<NoPhantomKeys<ProofFormulaLine, ApiResponse<'ProofFormulaLine'>>>,
  Expect<WireFits<ApiResponse<'ProofOfferBranch'>, ProofOfferBranch>>,
  Expect<NoPhantomKeys<ProofOfferBranch, ApiResponse<'ProofOfferBranch'>>>,
  Expect<WireFits<ApiResponse<'ProofReproduceQuery'>, ProofReproduceQuery>>,
  Expect<NoPhantomKeys<ProofReproduceQuery, ApiResponse<'ProofReproduceQuery'>>>,
  Expect<WireFits<ApiResponse<'ProofScoreComponent'>, ProofScoreComponent>>,
  Expect<NoPhantomKeys<ProofScoreComponent, ApiResponse<'ProofScoreComponent'>>>,
  Expect<WireFits<ApiResponse<'SourceLabel'>, SourceLabel>>,
  Expect<NoPhantomKeys<SourceLabel, ApiResponse<'SourceLabel'>>>,
  Expect<WireFits<ApiResponse<'WhyPanel'>, WhyPanel>>,
  Expect<NoPhantomKeys<WhyPanel, ApiResponse<'WhyPanel'>>>,
  Expect<WireFits<ApiResponse<'LeadCountResponse'>, LeadCountResponse>>,
  Expect<NoPhantomKeys<LeadCountResponse, ApiResponse<'LeadCountResponse'>>>,
  Expect<WireFits<ApiResponse<'LeadFacetBucket'>, LeadFacetBucket>>,
  Expect<NoPhantomKeys<LeadFacetBucket, ApiResponse<'LeadFacetBucket'>>>,
  Expect<WireFits<ApiResponse<'LeadFacetsResponse'>, LeadFacetsResponse>>,
  Expect<NoPhantomKeys<LeadFacetsResponse, ApiResponse<'LeadFacetsResponse'>>>,
  Expect<WireFits<ApiResponse<'SavedView'>, SavedView>>,
  Expect<NoPhantomKeys<SavedView, ApiResponse<'SavedView'>>>,
  Expect<WireFits<SavedViewCreateRequest, ApiRequest<'SavedViewCreateRequest'>>>,
  Expect<DeepNoPhantomKeys<SavedViewCreateRequest, ApiRequest<'SavedViewCreateRequest'>>>,
  Expect<WireFits<ApiResponse<'SavedViewListResponse'>, SavedViewListResponse>>,
  Expect<NoPhantomKeys<SavedViewListResponse, ApiResponse<'SavedViewListResponse'>>>,
  Expect<WireFits<ApiResponse<'SavedViewMutationResponse'>, SavedViewMutationResponse>>,
  Expect<NoPhantomKeys<SavedViewMutationResponse, ApiResponse<'SavedViewMutationResponse'>>>,
  // (ii) bound call sites whose type argument is not schema-named
  Expect<WireFits<ApiOk<'GET /api/v1/borrowers/{borrower_id}/lifecycle'>, BorrowerLifecycle>>,
  Expect<NoPhantomKeys<BorrowerLifecycle, ApiOk<'GET /api/v1/borrowers/{borrower_id}/lifecycle'>>>,
  Expect<WireFits<ApiOk<'POST /api/v1/outreach/approve'>, ApproveResult>>,
  Expect<NoPhantomKeys<ApproveResult, ApiOk<'POST /api/v1/outreach/approve'>>>,
  Expect<WireFits<ApiOk<'POST /api/v1/outreach/reject'>, RejectResult>>,
  Expect<NoPhantomKeys<RejectResult, ApiOk<'POST /api/v1/outreach/reject'>>>,
  Expect<WireFits<ApiOk<'POST /api/v1/outreach/draft'>, OutreachDraftResult>>,
  Expect<NoPhantomKeys<OutreachDraftResult, ApiOk<'POST /api/v1/outreach/draft'>>>,
];
