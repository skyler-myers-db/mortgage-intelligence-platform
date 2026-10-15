/**
 * Revoke fixtures (W5e w5-lead-table-compile, flow-v2): the approved Lead
 * Queue row, its lifecycle naming the approval to revoke (audit-free), and
 * the revoke write. The default handler answers a recorded revoke; a spec
 * that counts, holds or refuses it registers its own (the 409 bodies below
 * are the house shape: the fixed detail plus its `reason`).
 *
 * Synthetic only: masked `B-` ids, uuid-shaped record ids.
 */
import type { OutreachRevokeResponse } from '../../../../src/lib/apiClients/outreachRevoke';
import type { BorrowerLifecycle, LeadSummary } from '../../../../src/types';
import type { ContractSample } from '../contractSamples';
import { fixture, json, type FixtureEntry, type FixtureReply } from '../mockApi';
import { LEADS } from './borrowers';
import { SNAPSHOT_AT } from './reference';

/** The queue's one approved row (outreach queued): it offers Revoke to an approver. */
export const APPROVED_LEAD: LeadSummary = LEADS.find((lead) => lead.approval_status === 'approved') ?? LEADS[1];
export const REVOKED_APPROVAL_ID = '4c3b2a19-8f7e-4d6c-9b5a-0e1f2a3b4c5d';
export const REVOKE_ROW_ID = '7a6b5c4d-3e2f-4a1b-8c9d-0e1f2a3b4c5e';
export const REVOKE_AUDIT_ID = '2b3c4d5e-6f70-4a8b-9c0d-1e2f3a4b5c6d';

/** The approved row's lifecycle: the approval the Approved-row Revoke names. */
export function approvedLifecycle(borrowerId: string = APPROVED_LEAD.borrower_id): BorrowerLifecycle {
  return {
    borrower_id: borrowerId,
    approval_status: 'approved',
    outreach_status: 'queued',
    approval_id: REVOKED_APPROVAL_ID,
    audit_event_id: null,
    approved_at: '2026-07-14T15:00:04Z',
    outreach_at: null,
    synced_at: SNAPSHOT_AT,
    assignment: null,
    latest_disposition: null,
  };
}

export function revokedBody(approvalId: string = REVOKED_APPROVAL_ID, released: string | null = null): OutreachRevokeResponse {
  return {
    revoked: true,
    approval_id: REVOKE_ROW_ID,
    revoked_approval_id: approvalId,
    audit_event_id: REVOKE_AUDIT_ID,
    released_assignment_id: released,
  };
}

/** The server's 409 for a refused revoke (backend/api/outreach_revoke.py). */
export function revokeRefused(reason: string, detail: string): FixtureReply<{ detail: string; reason: string }> {
  return json({ detail, reason }, { status: 409 });
}

export const revokeFixtures: FixtureEntry[] = [
  fixture('POST', '/api/outreach/revoke', ({ body }) => {
    const approvalId = body && typeof body === 'object' && typeof (body as { approval_id?: unknown }).approval_id === 'string'
      ? (body as { approval_id: string }).approval_id
      : REVOKED_APPROVAL_ID;
    return json<OutreachRevokeResponse>(revokedBody(approvalId));
  }),
];

export function contractSamples(): ContractSample[] {
  return [
    {
      source: 'data/revoke.ts#revoked',
      method: 'POST',
      pattern: '/api/outreach/revoke',
      body: revokedBody(),
    },
    {
      source: 'data/revoke.ts#revoked_released',
      method: 'POST',
      pattern: '/api/outreach/revoke',
      body: revokedBody(REVOKED_APPROVAL_ID, '8d7c6b5a-4f3e-4d2c-9b1a-0f9e8d7c6b5a'),
    },
    {
      source: 'data/revoke.ts#approved_lifecycle',
      method: 'GET',
      pattern: '/api/borrowers/:id/lifecycle',
      body: approvedLifecycle(),
    },
  ];
}
