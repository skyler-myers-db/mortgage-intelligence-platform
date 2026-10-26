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
import type { DistributeLeadsResponse } from '../lib/apiClients/sales';
import type { AssignmentReleaseResponse, DistributionReleaseResponse } from '../lib/apiClients/salesLoop';
import type { QueueVersionBody } from '../lib/queueVersion';
import type { LeadAssignment, SalesTeamMember } from './loanOfficer';
import type { SavedDraftInput, SavedLead, SavedLeadInput, SessionResponse, WorkspaceMutationResult, WorkspaceState } from './workspace';

export type WireContractSales = [
  // (i) schema-named hand types
  Expect<WireFits<ApiResponse<'DistributeLeadsResponse'>, DistributeLeadsResponse>>,
  Expect<NoPhantomKeys<DistributeLeadsResponse, ApiResponse<'DistributeLeadsResponse'>>>,
  Expect<WireFits<ApiResponse<'AssignmentReleaseResponse'>, AssignmentReleaseResponse>>,
  Expect<NoPhantomKeys<AssignmentReleaseResponse, ApiResponse<'AssignmentReleaseResponse'>>>,
  Expect<WireFits<ApiResponse<'DistributionReleaseResponse'>, DistributionReleaseResponse>>,
  Expect<NoPhantomKeys<DistributionReleaseResponse, ApiResponse<'DistributionReleaseResponse'>>>,
  Expect<WireFits<ApiResponse<'LeadAssignment'>, LeadAssignment>>,
  Expect<NoPhantomKeys<LeadAssignment, ApiResponse<'LeadAssignment'>>>,
  Expect<WireFits<ApiResponse<'SalesTeamMember'>, SalesTeamMember>>,
  Expect<NoPhantomKeys<SalesTeamMember, ApiResponse<'SalesTeamMember'>>>,
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
