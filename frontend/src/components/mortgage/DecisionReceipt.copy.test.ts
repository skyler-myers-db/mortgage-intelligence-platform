import { describe, expect, it } from 'vitest';
import { decisionChip, isDecisionReceiptEvent } from './DecisionReceipt.copy';

describe('decisionChip', () => {
  it('labels each recorded decision with its own chip', () => {
    expect(decisionChip('approved')).toEqual({ variant: 'success', icon: 'check', label: 'Approved' });
    expect(decisionChip('rejected')).toEqual({ variant: 'danger', icon: 'cross', label: 'Rejected' });
    expect(decisionChip('held')).toEqual({ variant: 'warning', icon: 'shield', label: 'Held' });
  });

  it('reads a revoke (audit flow-v2) as Revoked, never as the approval it superseded', () => {
    expect(decisionChip('revoked')).toEqual({ variant: 'warning', icon: 'cross', label: 'Revoked' });
  });
});

describe('isDecisionReceiptEvent', () => {
  it('offers a receipt for a revoke row by event type or by action, normalized', () => {
    expect(isDecisionReceiptEvent({ event_type: 'OUTREACH_REVOKE', action: null })).toBe(true);
    expect(isDecisionReceiptEvent({ event_type: ' outreach_revoke ', action: null })).toBe(true);
    expect(isDecisionReceiptEvent({ event_type: null, action: ' Outreach.Revoke ' })).toBe(true);
  });

  it('offers none for an approval request, which decides nothing', () => {
    expect(isDecisionReceiptEvent({ event_type: 'APPROVAL_REQUESTED', action: 'outreach.approval_request' })).toBe(false);
    expect(isDecisionReceiptEvent({ event_type: 'APPROVAL_REQUEST_WITHDRAWN', action: null })).toBe(false);
  });
});
