import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { queryKeys } from '../lib/queryKeys';
import type { SalesTeamMember } from '../types';

const NO_ASSIGNEES: readonly SalesTeamMember[] = [];

/** Who an Offer approval may be routed to: active loan officers and sales managers. */
function assignableReviewers(members: SalesTeamMember[]): readonly SalesTeamMember[] {
  return members.filter((member) => (
    member.active
    && (member.role === 'loan_officer' || member.role === 'sales_manager')
  ));
}

/**
 * Load active reviewers for optional assignment without gating approval
 * (2026-09-21 audit runtime-06: the last effect-driven read on Offer).
 *
 * Its own key, queryKeys.salesRoster(): Lead Queue and Sales ops cache a
 * loan-officer-only list under queryKeys.salesTeam(), so sharing that key
 * would drop the sales managers here or corrupt the queue's filter. The
 * roster read writes no audit row. A failure is swallowed into a stable
 * empty list: assignment is optional and approval stays available.
 */
export function useOfferSalesTeam(): readonly SalesTeamMember[] {
  const query = useQuery({
    queryKey: queryKeys.salesRoster(),
    queryFn: ({ signal }) => api.salesTeam(signal),
    select: assignableReviewers,
    staleTime: 60_000,
  });
  return query.data ?? NO_ASSIGNEES;
}
