import { describe, expect, it } from 'vitest';
import type { BorrowerDecisionEvent } from './apiClients/borrowerDecisions';
import { ACTIVITY_LABELS, decisionTone, recentActivityPresentation } from './auditEventPresentation';

const DECISION_TYPES: BorrowerDecisionEvent['event_type'][] = [
  'ACTIVATION_STAGE', 'APPROVAL_REQUESTED', 'APPROVE', 'CALL_DISPOSITION', 'LEAD_ASSIGN',
  'LEAD_ASSIGNMENT_STATUS', 'LEAD_DISTRIBUTE', 'LEAD_OUTCOME', 'LEAD_OUTCOME_RECORDED',
  'LEAD_UNASSIGN', 'OUTREACH_REJECT', 'OUTREACH_REVOKE', 'SUPPRESS_CONTACT',
];

describe('audit event presentation', () => {
  it('labels every decision-history type and the W5b/W5c ledger reads', () => {
    for (const code of DECISION_TYPES) expect(ACTIVITY_LABELS[code], code).toBeTruthy();
    expect(ACTIVITY_LABELS.SUPPRESS_CONTACT).toBe('Contact blocked');
    expect(ACTIVITY_LABELS.OUTREACH_REVOKE).toBe('Outreach approval revoked');
    expect(ACTIVITY_LABELS.LEAD_UNASSIGN).toBe('Lead assignment released');
    expect(ACTIVITY_LABELS.VIEW_AUDIT_LEDGER).toBe('Audit ledger read');
    expect(ACTIVITY_LABELS.VIEW_REFUSAL_REPORT_TEXT).toBe('Refusal question read');
    expect(ACTIVITY_LABELS.GENIE_SECTION_REVEALED).toBe('Genie verified section made available');
  });

  it('gives the Console the new label instead of a sentence-cased code', () => {
    const presented = recentActivityPresentation({
      event_type: 'APPROVAL_REQUESTED',
      entity_type: 'approval_request_batch',
      subject_id: null,
      created_at: '2026-10-01T12:00:00Z',
    });
    expect(presented.label).toBe('Approval requested');
  });

  it('tones approvals and assignments green, refusals red, everything else amber', () => {
    expect(decisionTone('approved')).toBe('green');
    expect(decisionTone('assigned')).toBe('green');
    expect(decisionTone('rejected')).toBe('red');
    expect(decisionTone('revoked')).toBe('red');
    expect(decisionTone('contact_blocked')).toBe('red');
    for (const outcome of ['requested', 'unassigned', 'distributed', 'status_changed', 'disposition', 'outcome', 'activation'] as const) {
      expect(decisionTone(outcome), outcome).toBe('amber');
    }
  });
});
