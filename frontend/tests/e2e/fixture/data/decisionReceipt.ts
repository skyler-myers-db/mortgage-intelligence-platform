/**
 * Decision receipt fixtures (lane decision-receipt, wow-stage-3).
 *
 * Nothing here registers by default: the approve / reject POSTs are writes
 * and the receipt GET depends on the id those writes return, so the spec
 * registers them per test with `mockApi.register()`. The builders below
 * are typed against the frontend's own response types.
 *
 * The ledger values are deliberately ones the POST responses do NOT carry
 * (approver, copy hash, correlation id): a receipt that shows them was read
 * back from the row, not assembled from the POST.
 */
import type { Borrower360 } from '../../../../src/types';
import type { ApproveResult, DecisionOutcome, DecisionReceipt, RejectResult } from '../../../../src/lib/apiTypes';
import { json, type FixtureReply } from '../mockApi';

export const APPROVE_AUDIT_ID = '6f2a9c1e-3b4d-4e5f-8a6b-7c8d9e0f1a2b';
export const REJECT_AUDIT_ID = '0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d';
export const LEDGER_APPROVER = 'ledger.approver@summit-mortgage.example';
export const LEDGER_COPY_HASH = 'ledger'.padEnd(64, 'd');
export const LEDGER_CORRELATION_ID = 'corr-ledger-0001';
export const LEDGER_EVIDENCE_ASSETS = [
  'mip.gold.fn_next_best_offer',
  'mip.gold.fn_rate_spread',
  'mip.gold.fn_lead_score',
] as const;

export function approveResult(auditEventId: string): FixtureReply<ApproveResult> {
  return json<ApproveResult>({
    approved: true,
    approval_id: 'apr-fixture-0001',
    audit_event_id: auditEventId,
    assigned_to_email: null,
    follow_up_at: null,
    draft_generation_id: 'gen-fixture-0001',
    draft_edited: false,
  });
}

export function rejectResult(auditEventId: string): FixtureReply<RejectResult> {
  return json<RejectResult>({
    rejected: true,
    approval_id: 'apr-fixture-0002',
    audit_event_id: auditEventId,
  });
}

export function ledgerReceipt(
  auditEventId: string,
  borrower: Borrower360,
  decision: DecisionOutcome,
): DecisionReceipt {
  const rejected = decision === 'rejected';
  return {
    audit_event_id: auditEventId,
    event_type: rejected ? 'OUTREACH_REJECT' : 'APPROVE',
    decision,
    approval_id: rejected ? 'apr-fixture-0002' : 'apr-fixture-0001',
    borrower_id: borrower.borrower_id,
    offer_code: borrower.recommended_offer_code ?? 'refi',
    offer_label: borrower.recommended_offer,
    campaign_id: null,
    variant_name: null,
    channel: 'email',
    rationale_code: rejected ? 'low_intent' : null,
    copy_generation_id: rejected ? null : 'gen-fixture-0001',
    copy_hash: rejected ? null : LEDGER_COPY_HASH,
    approver: LEDGER_APPROVER,
    request_id: 'b1c2d3e4-f5a6-4b7c-8d9e-0f1a2b3c4d5e',
    correlation_id: LEDGER_CORRELATION_ID,
    created_at: '2026-07-14T15:00:04Z',
    evidence_ids: borrower.evidence_ids,
    evidence_assets: [...LEDGER_EVIDENCE_ASSETS],
  };
}

/**
 * A test-controlled hold on one mocked request. The handler awaits
 * `hold()`; the spec asserts what the page shows while the request is in
 * flight, then calls `release()`. Deterministic under any machine load,
 * unlike a wall-clock delay that can elapse before the assertions run.
 */
export class RequestGate {
  /** True once the app's request reached the handler. */
  received = false;
  private open = false;
  private waiters: Array<() => void> = [];

  hold(): Promise<void> {
    this.received = true;
    if (this.open) return Promise.resolve();
    return new Promise((resolve) => {
      this.waiters.push(resolve);
    });
  }

  release(): void {
    this.open = true;
    for (const resolve of this.waiters.splice(0)) resolve();
  }
}
