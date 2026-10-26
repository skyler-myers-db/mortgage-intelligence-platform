/**
 * Test factories for the Leads domain (audit quality-04 D-api-types-a3 P3,
 * W5e w5-wire-types B3): a complete, wire-shaped LeadSummary or Borrower360
 * from the fixture harness's wire bases, under least-privileged, pending
 * defaults, with the caller's overrides on top. Test-only: production code
 * never imports src/test (eslint import hygiene).
 */
import type { Borrower360, LeadSummary } from '../../types';
import { BORROWER_360_BASE, LEAD_SUMMARY_BASE } from '../../../tests/e2e/fixture/data/wireBases';

/** Pending and undecided: nothing a test did not ask for reads as approved. */
const PENDING = { approval_status: 'pending', approved_at: null } as const;

export function makeLeadSummary(overrides: Partial<LeadSummary> = {}): LeadSummary {
  return { ...LEAD_SUMMARY_BASE, ...PENDING, ...overrides };
}

export function makeBorrower360(overrides: Partial<Borrower360> = {}): Borrower360 {
  return { ...BORROWER_360_BASE, ...PENDING, ...overrides };
}
