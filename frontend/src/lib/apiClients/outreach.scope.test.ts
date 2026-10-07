/**
 * The approve / reject wire bodies (flow-02 / shell-06): a decision taken in a
 * request-scoped queue carries `approval_request_batch_id`; every other body
 * is byte-identical to the one sent before the scope existed (no key at all,
 * not even null), so unscoped intents and their replays never change.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../api';

const BATCH = '0f8fad5b-d9cb-469f-a165-70867728950e';
let bodies: Array<Record<string, unknown>> = [];

beforeEach(() => {
  bodies = [];
  vi.stubGlobal('fetch', async (_path: string, init?: RequestInit) => {
    bodies.push(JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>);
    return new Response(JSON.stringify({ approved: true, rejected: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const APPROVE_KEYS = [
  'borrower_id', 'actor', 'offer_code', 'evidence_ids', 'draft_subject', 'draft_body', 'draft_generation_id',
  'draft_response_hash', 'draft_source_refreshed_at', 'rationale', 'bulk_id', 'bulk_rationale', 'review_mode',
  'channel', 'campaign_id', 'variant_name', 'assigned_to_email', 'follow_up_in_days', 'lead_view_id', 'request_id',
];
const REJECT_KEYS = [
  'borrower_id', 'actor', 'offer_code', 'evidence_ids', 'rationale_code', 'rationale', 'channel', 'campaign_id',
  'variant_name', 'bulk_id', 'lead_view_id', 'request_id',
];

describe('approval_request_batch_id on the decision bodies', () => {
  it('is absent from an unscoped approve and reject (the pre-scope bodies, key for key)', async () => {
    await api.approve('B-AAAAAAAAAAAA1', { review_mode: 'individual', request_id: 'r1' });
    await api.approve('B-AAAAAAAAAAAA1', { review_mode: 'individual', request_id: 'r2', approval_request_batch_id: null });
    await api.reject('B-AAAAAAAAAAAA1', { rationale_code: 'low_intent', request_id: 'r3' });
    expect(Object.keys(bodies[0])).toEqual(APPROVE_KEYS);
    expect(Object.keys(bodies[1])).toEqual(APPROVE_KEYS);
    expect(Object.keys(bodies[2])).toEqual(REJECT_KEYS);
  });

  it('is sent when the decision answers an approval request', async () => {
    await api.approve('B-AAAAAAAAAAAA1', { review_mode: 'individual', request_id: 'r1', approval_request_batch_id: BATCH });
    await api.reject('B-AAAAAAAAAAAA1', { rationale_code: 'low_intent', request_id: 'r2', approval_request_batch_id: BATCH });
    expect(bodies.map((body) => body.approval_request_batch_id)).toEqual([BATCH, BATCH]);
  });
});
