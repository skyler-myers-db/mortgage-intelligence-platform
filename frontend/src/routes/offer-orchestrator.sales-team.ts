import { assignableReviewers, useSalesRoster } from '../lib/salesRoster';
import type { SalesTeamMember } from '../types';

const NO_ASSIGNEES: readonly SalesTeamMember[] = [];

/**
 * Load active reviewers for optional assignment without gating approval
 * (2026-09-21 audit runtime-06: the last effect-driven read on Offer).
 *
 * The roster is the one shared queryKeys.salesRoster() entry (lib/salesRoster):
 * Lead Queue and Sales ops narrow the same cached roster to loan officers, so
 * Lead Queue then Offer reads GET /sales/team once. The roster read writes no
 * audit row. A failure is swallowed into a stable empty list: assignment is
 * optional and approval stays available.
 */
export function useOfferSalesTeam(): readonly SalesTeamMember[] {
  return useSalesRoster(assignableReviewers).data ?? NO_ASSIGNEES;
}
