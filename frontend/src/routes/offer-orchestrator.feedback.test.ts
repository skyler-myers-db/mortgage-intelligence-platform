import { afterEach, describe, expect, it } from 'vitest';
import { clearToasts, getToasts } from '../lib/toast';
import { announceApprovalRouting, offerUnsavedMessage } from './offer-orchestrator.feedback';

afterEach(() => clearToasts());

describe('offerUnsavedMessage (audit states-05)', () => {
  it('names what would be lost, or nothing when nothing is unsaved', () => {
    expect(offerUnsavedMessage(false, false)).toBeNull();
    expect(offerUnsavedMessage(false, true)).toBe('Your rejection note has not been recorded. Leaving discards it.');
    expect(offerUnsavedMessage(true, false)).toContain('outreach draft');
    expect(offerUnsavedMessage(true, true)).toContain('outreach draft');
  });
});

describe('announceApprovalRouting (audit states-07)', () => {
  it('raises one confirmation linked to the approval audit row', () => {
    announceApprovalRouting('lo.alpha@summit.example', '2026-07-19T15:00:00Z', 'evt-approve-1');
    const [raised] = getToasts();
    expect(getToasts()).toHaveLength(1);
    expect(raised).toMatchObject({ tone: 'success', title: 'Approval routed', auditEventId: 'evt-approve-1' });
    expect(raised.detail).toMatch(/^Assigned to lo\.alpha@summit\.example · follow-up Jul 1\d$/);
  });

  it('says Unassigned when only a follow-up was set', () => {
    announceApprovalRouting(null, '2026-07-19T15:00:00Z', 'evt-approve-2');
    expect(getToasts()[0].detail).toMatch(/^Unassigned · follow-up Jul 1\d$/);
  });

  it('raises nothing for an approval that was not routed', () => {
    announceApprovalRouting(null, null, 'evt-approve-3');
    expect(getToasts()).toEqual([]);
  });
});
