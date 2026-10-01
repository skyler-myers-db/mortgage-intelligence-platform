/**
 * Outreach endpoint clients: the human approval and rejection decisions that
 * write to the Lakebase audit table, and governed draft generation.
 *
 * Members moved verbatim out of `api.ts`, which spreads them back into the
 * exported `api` object in the original order. Consumers import `api` from
 * `../api` — never from this module.
 */
import type { ApproveResult, RejectResult, OutreachDraftResult, ReviewMode } from '../apiTypes';
import { _newRequestId, postJson } from '../apiTransport';

/** The `/api/outreach/approve` body as the client sends it. */
export interface OutreachApproveRequest {
  borrower_id: string;
  actor: string;
  offer_code?: string | null;
  evidence_ids?: string[];
  draft_subject?: string | null;
  draft_body?: string | null;
  draft_generation_id?: string | null;
  draft_response_hash?: string | null;
  draft_source_refreshed_at?: string | null;
  rationale?: string | null;
  bulk_id?: string | null;
  bulk_rationale?: string | null;
  review_mode?: ReviewMode | null;
  channel?: 'email' | 'sms' | 'direct_mail';
  campaign_id?: string | null;
  variant_name?: string | null;
  assigned_to_email?: string | null;
  follow_up_in_days?: number | null;
  request_id: string;
}

/** The `/api/outreach/reject` body as the client sends it. */
export interface OutreachRejectRequest {
  borrower_id: string;
  actor: string;
  offer_code?: string | null;
  evidence_ids?: string[];
  rationale_code: string;
  rationale?: string | null;
  channel?: 'email' | 'sms' | 'direct_mail';
  campaign_id?: string | null;
  variant_name?: string | null;
  bulk_id?: string | null;
  request_id: string;
}

/** The `/api/outreach/draft` body. */
export interface OutreachDraftRequest {
  borrower_id: string;
  channel: 'email' | 'sms' | 'direct_mail';
  campaign_id: string | null;
  variant_name: string | null;
}

export const outreachApi = {
  approve: (
    borrower_id: string,
    opts: {
      actor?: string;
      offer_code?: string | null;
      evidence_ids?: string[];
      draft_subject?: string | null;
      draft_body?: string | null;
      draft_generation_id?: string | null;
      draft_response_hash?: string | null;
      draft_source_refreshed_at?: string | null;
      rationale?: string | null;
      bulk_id?: string | null;
      bulk_rationale?: string | null;
      /**
       * How the approver saw this copy (the APPROVE row's review ledger).
       * Omitted, the server records 'undeclared'.
       */
      review_mode?: ReviewMode | null;
      channel?: 'email' | 'sms' | 'direct_mail';
      campaign_id?: string | null;
      variant_name?: string | null;
      /** Optional loan-officer assignment captured at approval time. */
      assigned_to_email?: string | null;
      /** Optional follow-up reminder window (1..30 days) persisted as follow_up_at. */
      follow_up_in_days?: number | null;
      request_id?: string;
    } = {},
    signal?: AbortSignal,
  ) =>
    postJson<ApproveResult, OutreachApproveRequest>(
      '/api/outreach/approve',
      {
        borrower_id,
        actor: opts.actor ?? 'anonymous',
        offer_code: opts.offer_code ?? null,
        evidence_ids: opts.evidence_ids ?? [],
        draft_subject: opts.draft_subject ?? null,
        draft_body: opts.draft_body ?? null,
        draft_generation_id: opts.draft_generation_id ?? null,
        draft_response_hash: opts.draft_response_hash ?? null,
        draft_source_refreshed_at: opts.draft_source_refreshed_at ?? null,
        rationale: opts.rationale ?? null,
        bulk_id: opts.bulk_id ?? null,
        bulk_rationale: opts.bulk_rationale ?? null,
        review_mode: opts.review_mode ?? null,
        channel: opts.channel ?? 'email',
        campaign_id: opts.campaign_id ?? null,
        variant_name: opts.variant_name ?? null,
        assigned_to_email: opts.assigned_to_email ?? null,
        follow_up_in_days: opts.follow_up_in_days ?? null,
        // R5-01 idempotency: generate one UUID per user action and reuse
        // across any transparent retries inside _fetchWithRetry. The
        // backend has a unique index on mip_app.approvals(request_id)
        // and ON CONFLICT DO NOTHING so a duplicate POST (e.g. after a
        // 503 that the server actually committed before losing the
        // response) does not write a second audit row.
        request_id: opts.request_id ?? _newRequestId(),
      },
      signal,
    ),

  /**
   * Reject a borrower from outreach. Structural twin of `approve` —
   * writes one row to `mip_app.approvals` (action='reject') + one to
   * `mip_app.action_audit` (event_type='OUTREACH_REJECT'). Fires the
   * same debounced lifecycle-sync trigger so the funnel snapshot
   * reflects rejected counts through the event-triggered lifecycle path
   * or an explicit Admin Data operations repair run.
   */
  reject: (
    borrower_id: string,
    opts: {
      actor?: string;
      offer_code?: string | null;
      evidence_ids?: string[];
      rationale_code: string;
      rationale?: string | null;
      channel?: 'email' | 'sms' | 'direct_mail';
      campaign_id?: string | null;
      variant_name?: string | null;
      /** One bulk rejection run: every row carries the same id and shared note. */
      bulk_id?: string | null;
      request_id?: string;
    },
    signal?: AbortSignal,
  ) =>
    postJson<RejectResult, OutreachRejectRequest>(
      '/api/outreach/reject',
      {
        borrower_id,
        actor: opts.actor ?? 'anonymous',
        offer_code: opts.offer_code ?? null,
        evidence_ids: opts.evidence_ids ?? [],
        rationale_code: opts.rationale_code,
        rationale: opts.rationale ?? null,
        channel: opts.channel ?? 'email',
        campaign_id: opts.campaign_id ?? null,
        variant_name: opts.variant_name ?? null,
        bulk_id: opts.bulk_id ?? null,
        request_id: opts.request_id ?? _newRequestId(),
      },
      signal,
    ),

  /**
   * Fetch the backend-generated outreach draft for a borrower. The
   * backend emits a DRAFT_OUTREACH audit row as a side effect: a draft was
   * generated for this actor. Whether the approver saw it is the APPROVE
   * row's review_mode, not this row. Callers must fail closed if this
   * rejects; outreach copy is never generated locally.
   */
  draftOutreach: (
    borrower_id: string,
    channel: 'email' | 'sms' | 'direct_mail' = 'email',
    signal?: AbortSignal,
    campaign?: { campaign_id: string; variant_name: string },
  ) =>
    postJson<OutreachDraftResult, OutreachDraftRequest>(
      '/api/outreach/draft',
      {
        borrower_id,
        channel,
        campaign_id: campaign?.campaign_id ?? null,
        variant_name: campaign?.variant_name ?? null,
      },
      signal,
    ),
};
