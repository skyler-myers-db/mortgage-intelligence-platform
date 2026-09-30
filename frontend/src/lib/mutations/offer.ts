/**
 * The Offer Orchestrator's governed writes on the TanStack mutation layer
 * (2026-09-21 audit stack-09 item 1, states-07 item 3, runtime-06 (b)).
 *
 * Approve and reject reuse outreach.ts's mutation keys and its
 * `{decision, borrowerId}` variables shape, so `isDecisionPending`,
 * `usePendingDecisions` (the Lead Queue's in-flight row state) and any
 * other surface see an Offer write exactly like a queue write.
 *
 * Invariants (pinned by offer.test.ts and the route's writes test):
 *
 *   - PESSIMISTIC. No onMutate, no setQueryData: the caller shows Approved /
 *     Rejected only after the POST returned approved / rejected = true.
 *   - The body is exactly the object the route sent before this port, built
 *     from the ON-SCREEN draft. Never draftForApproval, never a second /draft.
 *   - networkMode 'always', retry false, and no AbortSignal (an aborted
 *     approve is audit-ambiguous; the route never cancels one).
 *   - One request_id per intent, in the variables (requestIds.ts): a retry of
 *     the identical intent resends it and the backend replays it; a changed
 *     field is a new intent with a new id.
 *   - Invalidation only through invalidateOperationalQueries (refetchType
 *     'none'), which never touches the Offer snapshot.
 */
import { useMutation, type QueryClient } from '@tanstack/react-query';
import type { SavedDraft, SavedDraftInput } from '../../types';
import { api } from '../api';
import type { ApproveResult, RejectResult } from '../apiTypes';
import { invalidateOperationalQueries } from '../queryKeys';
import { outreachMutationKeys } from './outreach';

export type OfferChannel = 'email' | 'sms' | 'direct_mail';

/** The approve POST body the Offer Orchestrator sends (request_id rides beside it). */
export interface OfferApproveBody {
  offer_code: string | null;
  evidence_ids: string[];
  /** null for SMS: an SMS draft has no subject. */
  draft_subject: string | null;
  draft_body: string;
  draft_generation_id: string | null;
  draft_response_hash: string | null;
  draft_source_refreshed_at: string | null;
  channel: OfferChannel;
  assigned_to_email: string | null;
  follow_up_in_days: number | null;
  campaign_id: string | null;
  variant_name: string | null;
}

export interface OfferRejectBody {
  offer_code: string | null;
  evidence_ids: string[];
  channel: OfferChannel;
  rationale_code: string;
  rationale: string | null;
  campaign_id: string | null;
  variant_name: string | null;
}

export interface OfferApproveVariables {
  decision: 'approve';
  borrowerId: string;
  requestId: string;
  body: OfferApproveBody;
}

export interface OfferRejectVariables {
  decision: 'reject';
  borrowerId: string;
  requestId: string;
  body: OfferRejectBody;
}

export const offerMutationKeys = {
  draftSave: ['mip', 'offer', 'draft-save'] as const,
};

export function useOfferApprove(queryClient: QueryClient) {
  return useMutation<ApproveResult, Error, OfferApproveVariables>(
    {
      mutationKey: outreachMutationKeys.approve,
      mutationFn: ({ borrowerId, requestId, body }) => api.approve(borrowerId, { ...body, request_id: requestId }),
      networkMode: 'always',
      retry: false,
      onSuccess: (result) => {
        if (result.approved) void invalidateOperationalQueries(queryClient);
      },
    },
    queryClient,
  );
}

export function useOfferReject(queryClient: QueryClient) {
  return useMutation<RejectResult, Error, OfferRejectVariables>(
    {
      mutationKey: outreachMutationKeys.reject,
      mutationFn: ({ borrowerId, requestId, body }) => api.reject(borrowerId, { ...body, request_id: requestId }),
      networkMode: 'always',
      retry: false,
      onSuccess: (result) => {
        if (result.rejected) void invalidateOperationalQueries(queryClient);
      },
    },
    queryClient,
  );
}

/**
 * Save the reviewed draft to the workspace through AppContext's saveDraft
 * (which owns the workspace store). Pending and failure are the mutation's
 * state, not a hand-rolled pair of useState calls.
 */
export function useOfferDraftSave(saveDraft: (draft: SavedDraftInput) => Promise<SavedDraft>) {
  return useMutation<SavedDraft, Error, SavedDraftInput>({
    mutationKey: offerMutationKeys.draftSave,
    mutationFn: (draft) => saveDraft(draft),
    networkMode: 'always',
    retry: false,
  });
}
