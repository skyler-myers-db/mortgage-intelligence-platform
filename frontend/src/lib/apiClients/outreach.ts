/**
 * Outreach endpoint clients: the human approval and rejection decisions that
 * write to the Lakebase audit table, and governed draft generation.
 *
 * Members moved verbatim out of `api.ts`, which spreads them back into the
 * exported `api` object in the original order. Consumers import `api` from
 * `../api` — never from this module.
 */
import type { ApproveResult, RejectResult, OutreachDraftResult } from '../apiTypes';
import { _newRequestId, postJson } from '../apiTransport';

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
    postJson<
      ApproveResult,
      {
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
        channel?: 'email' | 'sms' | 'direct_mail';
        campaign_id?: string | null;
        variant_name?: string | null;
        assigned_to_email?: string | null;
        follow_up_in_days?: number | null;
        request_id: string;
      }
    >(
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
      request_id?: string;
    },
    signal?: AbortSignal,
  ) =>
    postJson<
      RejectResult,
      {
        borrower_id: string;
        actor: string;
        offer_code?: string | null;
        evidence_ids?: string[];
        rationale_code: string;
        rationale?: string | null;
        channel?: 'email' | 'sms' | 'direct_mail';
        campaign_id?: string | null;
        variant_name?: string | null;
        request_id: string;
      }
    >(
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
        request_id: opts.request_id ?? _newRequestId(),
      },
      signal,
    ),

  /**
   * Fetch the backend-generated outreach draft for a borrower. The
   * backend emits a DRAFT_OUTREACH audit row as a side effect so we
   * know which draft copy was shown to the approver. Callers must fail
   * closed if this rejects; outreach copy is never generated locally.
   */
  draftOutreach: (
    borrower_id: string,
    channel: 'email' | 'sms' | 'direct_mail' = 'email',
    signal?: AbortSignal,
    campaign?: { campaign_id: string; variant_name: string },
  ) =>
    postJson<OutreachDraftResult, {
      borrower_id: string;
      channel: 'email' | 'sms' | 'direct_mail';
      campaign_id: string | null;
      variant_name: string | null;
    }>(
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
