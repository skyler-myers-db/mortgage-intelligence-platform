/**
 * Wire contract, Sales domain (audit 2026-09-21 quality-04, D-api-types-a3 P1):
 * workspace, session, loanOfficer and sales.
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
import type { AssignLeadRequest, AssignLoanOfficerRequest, AssignmentOutcomeRequest, AssignmentStatusUpdateRequest, DispositionRequest, DistributeLeadsRequest, DistributeLeadsResponse, LoanOfficerAssignmentResponse } from '../lib/apiClients/sales';
import type { AssignmentResponse, DispositionResponse } from '../lib/apiTypes';
import type { QueueVersionBody } from '../lib/queueVersion';
import type { CallDisposition, SalesAgingLead, SalesConversionResponse, SalesOutcomeSummaryResponse, SalesStandupResponse } from '../types';
import type { LeadAssignment, LoanOfficer, LoanOfficerAssignment, SalesTeamMember } from './loanOfficer';
import type { SavedDraft, SavedDraftInput, SavedLead, SavedLeadInput, SessionResponse, WorkspaceMutationResult, WorkspaceState } from './workspace';

export type WireContractSales = [
  // (i) schema-named hand types
  Expect<WireFits<AssignLeadRequest, ApiRequest<'AssignLeadRequest'>>>,
  Expect<DeepNoPhantomKeys<AssignLeadRequest, ApiRequest<'AssignLeadRequest'>>>,
  Expect<WireFits<AssignLoanOfficerRequest, ApiRequest<'AssignLoanOfficerRequest'>>>,
  Expect<DeepNoPhantomKeys<AssignLoanOfficerRequest, ApiRequest<'AssignLoanOfficerRequest'>>>,
  Expect<WireFits<AssignmentOutcomeRequest, ApiRequest<'AssignmentOutcomeRequest'>>>,
  Expect<DeepNoPhantomKeys<AssignmentOutcomeRequest, ApiRequest<'AssignmentOutcomeRequest'>>>,
  Expect<WireFits<AssignmentStatusUpdateRequest, ApiRequest<'AssignmentStatusUpdateRequest'>>>,
  Expect<DeepNoPhantomKeys<AssignmentStatusUpdateRequest, ApiRequest<'AssignmentStatusUpdateRequest'>>>,
  Expect<WireFits<DispositionRequest, ApiRequest<'DispositionRequest'>>>,
  Expect<DeepNoPhantomKeys<DispositionRequest, ApiRequest<'DispositionRequest'>>>,
  Expect<WireFits<DistributeLeadsRequest, ApiRequest<'DistributeLeadsRequest'>>>,
  Expect<DeepNoPhantomKeys<DistributeLeadsRequest, ApiRequest<'DistributeLeadsRequest'>>>,
  Expect<WireFits<ApiResponse<'DistributeLeadsResponse'>, DistributeLeadsResponse>>,
  Expect<NoPhantomKeys<DistributeLeadsResponse, ApiResponse<'DistributeLeadsResponse'>>>,
  Expect<WireFits<ApiResponse<'LoanOfficerAssignmentResponse'>, LoanOfficerAssignmentResponse>>,
  Expect<NoPhantomKeys<LoanOfficerAssignmentResponse, ApiResponse<'LoanOfficerAssignmentResponse'>>>,
  Expect<WireFits<ApiResponse<'AssignmentResponse'>, AssignmentResponse>>,
  Expect<NoPhantomKeys<AssignmentResponse, ApiResponse<'AssignmentResponse'>>>,
  Expect<WireFits<ApiResponse<'DispositionResponse'>, DispositionResponse>>,
  Expect<NoPhantomKeys<DispositionResponse, ApiResponse<'DispositionResponse'>>>,
  Expect<WireFits<ApiResponse<'CallDisposition'>, CallDisposition>>,
  Expect<NoPhantomKeys<CallDisposition, ApiResponse<'CallDisposition'>>>,
  Expect<WireFits<ApiResponse<'SalesAgingLead'>, SalesAgingLead>>,
  Expect<NoPhantomKeys<SalesAgingLead, ApiResponse<'SalesAgingLead'>>>,
  // @ts-expect-error wire-drift quality-04 2026-10-01 w5-palette-offer-sales-loop: rows are untyped objects on the wire but typed conversion rows in the hand type
  Expect<WireFits<ApiResponse<'SalesConversionResponse'>, SalesConversionResponse>>,
  Expect<NoPhantomKeys<SalesConversionResponse, ApiResponse<'SalesConversionResponse'>>>,
  // @ts-expect-error wire-drift quality-04 2026-10-01 w5-palette-offer-sales-loop: by_lo, by_source_system, source_statuses and top_competitors rows are untyped objects on the wire but typed rows in the hand type
  Expect<WireFits<ApiResponse<'SalesOutcomeSummaryResponse'>, SalesOutcomeSummaryResponse>>,
  Expect<NoPhantomKeys<SalesOutcomeSummaryResponse, ApiResponse<'SalesOutcomeSummaryResponse'>>>,
  // @ts-expect-error wire-drift quality-04 2026-10-01 w5-palette-offer-sales-loop: by_lo rows are untyped objects on the wire but typed rows in the hand type
  Expect<WireFits<ApiResponse<'SalesStandupResponse'>, SalesStandupResponse>>,
  Expect<NoPhantomKeys<SalesStandupResponse, ApiResponse<'SalesStandupResponse'>>>,
  Expect<WireFits<ApiResponse<'LeadAssignment'>, LeadAssignment>>,
  Expect<NoPhantomKeys<LeadAssignment, ApiResponse<'LeadAssignment'>>>,
  Expect<WireFits<ApiResponse<'LoanOfficer'>, LoanOfficer>>,
  Expect<NoPhantomKeys<LoanOfficer, ApiResponse<'LoanOfficer'>>>,
  Expect<WireFits<ApiResponse<'LoanOfficerAssignment'>, LoanOfficerAssignment>>,
  Expect<NoPhantomKeys<LoanOfficerAssignment, ApiResponse<'LoanOfficerAssignment'>>>,
  Expect<WireFits<ApiResponse<'SalesTeamMember'>, SalesTeamMember>>,
  Expect<NoPhantomKeys<SalesTeamMember, ApiResponse<'SalesTeamMember'>>>,
  Expect<WireFits<ApiResponse<'SavedDraft'>, SavedDraft>>,
  Expect<NoPhantomKeys<SavedDraft, ApiResponse<'SavedDraft'>>>,
  Expect<WireFits<SavedDraftInput, ApiRequest<'SavedDraftInput'>>>,
  Expect<DeepNoPhantomKeys<SavedDraftInput, ApiRequest<'SavedDraftInput'>>>,
  Expect<WireFits<ApiResponse<'SavedLead'>, SavedLead>>,
  Expect<NoPhantomKeys<SavedLead, ApiResponse<'SavedLead'>>>,
  Expect<WireFits<SavedLeadInput, ApiRequest<'SavedLeadInput'>>>,
  Expect<DeepNoPhantomKeys<SavedLeadInput, ApiRequest<'SavedLeadInput'>>>,
  Expect<WireFits<ApiResponse<'SessionResponse'>, SessionResponse>>,
  Expect<NoPhantomKeys<SessionResponse, ApiResponse<'SessionResponse'>>>,
  Expect<WireFits<ApiResponse<'WorkspaceState'>, WorkspaceState>>,
  Expect<NoPhantomKeys<WorkspaceState, ApiResponse<'WorkspaceState'>>>,
  // (ii) bound call sites whose type argument is not schema-named
  Expect<WireFits<ApiOk<'DELETE /api/v1/workspace/leads/{borrower_id}'>, WorkspaceMutationResult>>,
  Expect<NoPhantomKeys<WorkspaceMutationResult, ApiOk<'DELETE /api/v1/workspace/leads/{borrower_id}'>>>,
  Expect<WireFits<ApiOk<'DELETE /api/v1/workspace/drafts/{borrower_id}'>, WorkspaceMutationResult>>,
  Expect<NoPhantomKeys<WorkspaceMutationResult, ApiOk<'DELETE /api/v1/workspace/drafts/{borrower_id}'>>>,
  Expect<WireFits<ApiOk<'GET /api/v1/workspace/queue-version'>, QueueVersionBody>>,
  Expect<NoPhantomKeys<QueueVersionBody, ApiOk<'GET /api/v1/workspace/queue-version'>>>,
];
