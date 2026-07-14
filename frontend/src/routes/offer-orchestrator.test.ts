import { describe, expect, it } from 'vitest';
import {
  draftProofMatchesSnapshot,
  resolveOfferApprovalStatus,
} from './offer-orchestrator.snapshot';

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

describe('draftProofMatchesSnapshot', () => {
  it('normalizes whitespace while requiring all three refresh versions to match', () => {
    const borrower = { source_refreshed_at: ' 2026-07-13T12:00:00Z ' } as never;
    const recommendation = { source_refreshed_at: '2026-07-13T12:00:00Z' } as never;

    expect(draftProofMatchesSnapshot(
      borrower,
      recommendation,
      ' 2026-07-13T12:00:00Z ',
    )).toBe(true);
    expect(draftProofMatchesSnapshot(
      borrower,
      recommendation,
      '2026-07-13T12:05:00Z',
    )).toBe(false);
  });

  it('fails closed when any source payload or refresh version is absent', () => {
    const borrower = { source_refreshed_at: '2026-07-13T12:00:00Z' } as never;
    expect(draftProofMatchesSnapshot(borrower, null, '2026-07-13T12:00:00Z')).toBe(false);
    expect(draftProofMatchesSnapshot(null, null, null)).toBe(false);
  });
});
