/**
 * Feedback-guard fixtures (lane feedback-guard: audit states-05, states-07).
 *
 * Nothing here registers by default: saving a build and approving an offer
 * are writes, so feedback-guard.fixture.spec.ts registers them per test
 * with `mockApi.register()` and these typed builders. Values are synthetic
 * (Summit Mortgage, masked ids, `.example` loan officers).
 */
import type { PortfolioCreateResponse } from '../../../../src/types';
import type { ApproveResult } from '../../../../src/lib/apiTypes';
import { json, type FixtureReply } from '../mockApi';
import { SALES_TEAM } from './portfolio';
import { TOTALS } from './reference';

export const SAVE_AUDIT_ID = '5c1d7e2f-8a3b-4c6d-9e0f-1a2b3c4d5e6f';
export const ROUTED_APPROVE_AUDIT_ID = '7d3e9f1a-2b4c-4d6e-8f0a-1b2c3d4e5f60';
/** The loan officer the routing test assigns (Loan Officer A in the sales-team fixture). */
export const ROUTED_LOAN_OFFICER = SALES_TEAM[0];
/** Five days after FIXTURE_NOW (2026-07-14T15:00:00Z): Jul 19 in America/New_York. */
export const ROUTED_FOLLOW_UP_AT = '2026-07-19T15:00:00Z';

export function portfolioCreated(name: string): FixtureReply<PortfolioCreateResponse> {
  return json<PortfolioCreateResponse>({
    portfolio_id: 'pf-fixture-0001',
    campaign_id: 'cmp-fixture-0001',
    name,
    marketable_population: TOTALS.contactable,
    campaign_build_limit: 10000,
    campaign_build_eligible: true,
    audit_event_id: SAVE_AUDIT_ID,
  });
}

export function routedApproveResult(): FixtureReply<ApproveResult> {
  return json<ApproveResult>({
    approved: true,
    approval_id: 'apr-fixture-routed',
    audit_event_id: ROUTED_APPROVE_AUDIT_ID,
    assigned_to_email: ROUTED_LOAN_OFFICER.email,
    follow_up_at: ROUTED_FOLLOW_UP_AT,
    draft_generation_id: 'gen-fixture-0001',
    draft_edited: false,
  });
}
