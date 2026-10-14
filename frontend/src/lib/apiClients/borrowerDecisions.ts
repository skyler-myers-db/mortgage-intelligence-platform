/**
 * The borrower decision history (audit flow-04 phase 2, D-audit-reads-c2):
 * one borrower's governed decisions for the working team, from
 * GET /api/borrowers/{id}/decisions.
 *
 * The read is audit-free (it writes no VIEW_* row). It is read on a Borrower
 * 360 or Offer mount only: never on hover or idle, never polled, never
 * prefetched, never persisted. Its key sits under ['mip','borrower'], so
 * invalidateOperationalQueries marks it stale after every governed decision.
 *
 * Imported by the history chunk only, never spread into `api` (api.ts sits in
 * the initial closure).
 */
import { queryOptions } from '@tanstack/react-query';
import { getJson } from '../apiTransport';
import { queryKeys } from '../queryKeys';

type OfferCode = 'refi' | 'heloc' | 'cash_out' | 'purchase' | 'retention' | 'recapture' | 'refi_plus_heloc' | 'investor' | 'nurture';
type AssignmentStatus = 'assigned' | 'contact_drafted' | 'approved' | 'actioned' | 'outcome_recorded';

/** One governed decision, projected server-side through closed vocabularies (no free text). */
export interface BorrowerDecisionEvent {
  audit_event_id: string;
  event_type:
    | 'ACTIVATION_STAGE'
    | 'APPROVAL_REQUESTED'
    | 'APPROVE'
    | 'CALL_DISPOSITION'
    | 'LEAD_ASSIGN'
    | 'LEAD_ASSIGNMENT_STATUS'
    | 'LEAD_DISTRIBUTE'
    | 'LEAD_OUTCOME'
    | 'LEAD_OUTCOME_RECORDED'
    | 'LEAD_UNASSIGN'
    | 'OUTREACH_REJECT'
    | 'OUTREACH_REVOKE'
    | 'SUPPRESS_CONTACT';
  outcome:
    | 'approved'
    | 'rejected'
    | 'revoked'
    | 'requested'
    | 'assigned'
    | 'unassigned'
    | 'distributed'
    | 'status_changed'
    | 'disposition'
    | 'outcome'
    | 'activation'
    | 'contact_blocked';
  occurred_at: string;
  actor_display: string;
  actor_kind: 'staff' | 'automation' | 'unverified';
  is_own: boolean;
  offer_code: OfferCode | null;
  channel: 'email' | 'sms' | 'direct_mail' | null;
  rationale_label: 'Out of footprint' | 'Contact preference' | 'Compliance review' | 'Low intent' | 'Data quality' | 'Other' | null;
  /** The client prefixes "Contact blocked: ". */
  contact_block_label: 'No marketing consent' | 'Contacted within 30 days' | 'Suppressed' | 'Eligibility not proven' | null;
  assigned_to_display: string | null;
  from_status: AssignmentStatus | null;
  to_status: AssignmentStatus | null;
  disposition_outcome:
    | 'called_no_answer'
    | 'called_left_voicemail'
    | 'connected'
    | 'callback_scheduled'
    | 'application_started'
    | 'not_interested'
    | 'not_now'
    | 'dead'
    | null;
  lead_outcome_type: 'application_submitted' | 'closed_funded' | 'lost_to_competitor' | 'withdrawn' | 'not_qualified' | null;
  activation_status: 'dry_run' | 'staged' | 'delivered' | 'failed' | 'cancelled' | null;
  bulk: boolean;
  receipt_available: boolean;
}

/** The latest 50 decisions, newest first; `truncated` when older ones exist. */
export interface BorrowerDecisionHistoryResponse {
  borrower_id: string;
  items: BorrowerDecisionEvent[];
  truncated: boolean;
}

export const borrowerDecisionsApi = {
  list: (id: string, signal?: AbortSignal) =>
    getJson<BorrowerDecisionHistoryResponse>(`/api/borrowers/${id}/decisions`, signal),
};

/** Thirty seconds fresh, no retry: a 403 hides the section, anything else offers Retry. */
export function borrowerDecisionsQuery(id: string) {
  return queryOptions<BorrowerDecisionHistoryResponse>({
    queryKey: queryKeys.borrowerDecisions(id),
    queryFn: ({ signal }) => borrowerDecisionsApi.list(id, signal),
    staleTime: 30_000,
    retry: false,
  });
}
