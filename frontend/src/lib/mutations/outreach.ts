/**
 * Governed outreach decisions on the TanStack mutation layer (audit
 * stack-09 / tables-05 step 2): approve and reject as keyed mutations, so
 * any surface can see which borrower has a decision on the wire.
 *
 * Invariants (every one is pinned by outreach.test.ts):
 *
 *   - PESSIMISTIC. No onMutate, no setQueryData, no optimistic state: the
 *     Lakebase audit row is the truth, so a caller shows Approved / Rejected
 *     only after the POST returned approved / rejected = true.
 *   - networkMode 'always', retry false. TanStack's default ('online') would
 *     PAUSE a write started offline and fire it on reconnect: a governed
 *     write the person never re-confirmed. With 'always' it fails at once
 *     and the caller's offline copy shows.
 *   - One request_id per intent, passed in the variables (see
 *     requestIds.ts); the backend replays a matching id without a second
 *     audit row.
 *   - Invalidation goes only through invalidateOperationalQueries, which uses
 *     refetchType 'none': an active ['mip','borrower'] or ['mip','leads']
 *     refetch would write VIEW_* audit rows nobody asked for.
 *   - No shared `scope`: a scope would serialize a bulk run's POSTs.
 *
 * Bulk rows carry their bulkId in the variables, so a progress view can
 * read a run with useMutationState and no new plumbing (W3 contract).
 * Variables stay in the MutationCache, which queryClient.clear() empties on
 * an actor change; they are never sent to telemetry.
 */
import {
  useMutation,
  useMutationState,
  type Mutation,
  type QueryClient,
} from '@tanstack/react-query';
import { api, ApiError, isAbortError } from '../api';
import type { ApproveResult, OutreachDraftResult, RejectResult } from '../apiTypes';
import { invalidateOperationalQueries } from '../queryKeys';

export const outreachMutationKeys = {
  all: ['mip', 'outreach'] as const,
  approve: ['mip', 'outreach', 'approve'] as const,
  reject: ['mip', 'outreach', 'reject'] as const,
};

/** A verified `?campaign_id=&variant_name=` binding (LeadTable.logic's CampaignBinding). */
export interface OutreachCampaignBinding {
  campaign_id: string;
  variant_name: string;
}

export type OutreachDecision = 'approve' | 'reject';

export interface ApproveLeadVariables {
  decision: 'approve';
  borrowerId: string;
  requestId: string;
  /** The exact draft the approver was shown; null drafts here (unsampled bulk rows). */
  reviewedDraft: OutreachDraftResult | null;
  campaignBinding: OutreachCampaignBinding | null;
  evidenceIds: string[];
  /** The row's recommended offer, used when the draft names none. */
  offerCode: string | null;
  rationale: string | null;
  bulkId: string | null;
  bulkRationale: string | null;
  signal?: AbortSignal;
  /** A bulk run invalidates once at the end instead of per row. */
  suppressInvalidation?: boolean;
}

export interface RejectLeadVariables {
  decision: 'reject';
  borrowerId: string;
  requestId: string;
  rationaleCode: string;
  rationale: string | null;
  campaignBinding: OutreachCampaignBinding | null;
  evidenceIds: string[];
  offerCode: string | null;
}

type DecisionVariables = ApproveLeadVariables | RejectLeadVariables;

/**
 * Generate the governed email draft an approval certifies. Called only on
 * explicit intent (the review's Approve / A, a bulk run, or "Preview 3
 * sample drafts"): each call writes a DRAFT_OUTREACH audit row, so never
 * on row expand, hover, cursor movement or prefetch.
 */
export async function draftForApproval(
  borrowerId: string,
  campaignBinding: OutreachCampaignBinding | null,
  signal?: AbortSignal,
): Promise<OutreachDraftResult> {
  const draft = campaignBinding
    ? await api.draftOutreach(borrowerId, 'email', signal, campaignBinding)
    : await api.draftOutreach(borrowerId, 'email', signal);
  if (
    campaignBinding
    && (
      draft.campaign_id !== campaignBinding.campaign_id
      || draft.variant_name !== campaignBinding.variant_name
    )
  ) {
    throw new Error('Campaign variant proof is stale. Reopen the saved campaign before approval.');
  }
  if (!draft.subject?.trim()) {
    throw new Error('Governed email draft returned without a subject. Regenerate before approval.');
  }
  return draft;
}

async function approveLeadRequest(variables: ApproveLeadVariables): Promise<ApproveResult> {
  const draft = variables.reviewedDraft
    ?? await draftForApproval(variables.borrowerId, variables.campaignBinding, variables.signal);
  return api.approve(
    variables.borrowerId,
    {
      evidence_ids: variables.evidenceIds,
      offer_code: draft.offer_code ?? variables.offerCode,
      draft_subject: draft.subject?.trim() ?? '',
      draft_body: draft.body,
      draft_generation_id: draft.generation_id,
      draft_response_hash: draft.response_hash,
      draft_source_refreshed_at: draft.source_refreshed_at,
      channel: 'email',
      rationale: variables.rationale,
      bulk_id: variables.bulkId,
      bulk_rationale: variables.bulkRationale,
      campaign_id: variables.campaignBinding?.campaign_id ?? null,
      variant_name: variables.campaignBinding?.variant_name ?? null,
      request_id: variables.requestId,
    },
    variables.signal,
  );
}

function rejectLeadRequest(variables: RejectLeadVariables): Promise<RejectResult> {
  return api.reject(
    variables.borrowerId,
    {
      evidence_ids: variables.evidenceIds,
      offer_code: variables.offerCode,
      rationale_code: variables.rationaleCode,
      rationale: variables.rationale,
      campaign_id: variables.campaignBinding?.campaign_id ?? null,
      variant_name: variables.campaignBinding?.variant_name ?? null,
      request_id: variables.requestId,
    },
  );
}

export function useApproveLead(queryClient: QueryClient) {
  return useMutation<ApproveResult, Error, ApproveLeadVariables>(
    {
      mutationKey: outreachMutationKeys.approve,
      mutationFn: approveLeadRequest,
      networkMode: 'always',
      retry: false,
      onSuccess: (result, variables) => {
        if (result.approved && !variables.suppressInvalidation) {
          void invalidateOperationalQueries(queryClient);
        }
      },
    },
    queryClient,
  );
}

export function useRejectLead(queryClient: QueryClient) {
  return useMutation<RejectResult, Error, RejectLeadVariables>(
    {
      mutationKey: outreachMutationKeys.reject,
      mutationFn: rejectLeadRequest,
      networkMode: 'always',
      retry: false,
      onSuccess: (result) => {
        if (result.rejected) void invalidateOperationalQueries(queryClient);
      },
    },
    queryClient,
  );
}

function decisionOf(variables: unknown): { borrowerId: string; decision: OutreachDecision } | null {
  if (typeof variables !== 'object' || variables === null) return null;
  const { borrowerId, decision } = variables as Partial<DecisionVariables>;
  if (typeof borrowerId !== 'string') return null;
  if (decision !== 'approve' && decision !== 'reject') return null;
  return { borrowerId, decision };
}

function selectPendingDecision(mutation: Mutation<unknown, Error, unknown, unknown>) {
  const found = decisionOf(mutation.state.variables);
  return found ? ([found.borrowerId, found.decision] as const) : null;
}

/**
 * Which borrowers have an approve or reject on the wire, and which. Read
 * from the MutationCache, so a table that remounted mid-write still shows
 * the row as pending.
 */
export function usePendingDecisions(queryClient: QueryClient): ReadonlyMap<string, OutreachDecision> {
  const pending = useMutationState(
    {
      filters: { mutationKey: outreachMutationKeys.all, status: 'pending' },
      select: selectPendingDecision,
    },
    queryClient,
  );
  const byBorrower = new Map<string, OutreachDecision>();
  for (const entry of pending) {
    if (entry) byBorrower.set(entry[0], entry[1]);
  }
  return byBorrower;
}

/** Synchronous: is an approve or reject for this borrower on the wire right now? */
export function isDecisionPending(queryClient: QueryClient, borrowerId: string): boolean {
  return queryClient.isMutating({
    mutationKey: outreachMutationKeys.all,
    predicate: (mutation) => decisionOf(mutation.state.variables)?.borrowerId === borrowerId,
  }) > 0;
}

export type DecisionFailure = 'aborted' | 'network' | 'backend';

/**
 * How a failed decision write ended. 'network' means the request never
 * reached the audit table (ApiError with no status: offline, unreachable);
 * 'aborted' is a cancelled POST the server may still have committed.
 */
export function decisionFailure(error: unknown): DecisionFailure {
  if (isAbortError(error)) return 'aborted';
  if (error instanceof ApiError && error.status === null) return 'network';
  return 'backend';
}
