import { describe, expect, it } from 'vitest';
import { resolveOfferApprovalStatus } from './offer-orchestrator';

describe('resolveOfferApprovalStatus', () => {
  it('uses durable approved lifecycle state after a page reload', () => {
    expect(resolveOfferApprovalStatus(undefined, 'approved', 'pending')).toBe('approved');
  });

  it('prefers local in-session approval state when present', () => {
    expect(resolveOfferApprovalStatus('rejected', 'approved', 'pending')).toBe('rejected');
  });

  it('does not promote pending borrower state into a terminal approval', () => {
    expect(resolveOfferApprovalStatus(undefined, undefined, 'pending')).toBeUndefined();
  });
});
