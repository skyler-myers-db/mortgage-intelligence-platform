/**
 * The one reader of GET /sales/team (2026-09-21 audit runtime-06).
 *
 * Lead Queue, Sales ops and Offer used to cache the roster under two keys
 * (a loan-officer-only list and the unfiltered roster), so opening the queue
 * and then Offer read it twice. Every caller now shares
 * queryKeys.salesRoster(), which caches the UNFILTERED roster; each narrows
 * it with a module-level `select` (stable identity, so TanStack re-runs it
 * only when the roster changes). The read writes no audit row.
 */
import { useQuery } from '@tanstack/react-query';
import type { SalesTeamMember } from '../types';
import { api } from './api';
import { queryKeys } from './queryKeys';

/** Loan officers only: the ASSIGNED filter, the queue's assign actions and Sales ops. */
export function loanOfficersOnly(members: SalesTeamMember[]): SalesTeamMember[] {
  return members.filter((member) => member.role === 'loan_officer');
}

/** Who an Offer approval may be routed to: active loan officers and sales managers. */
export function assignableReviewers(members: SalesTeamMember[]): SalesTeamMember[] {
  return members.filter((member) => (
    member.active
    && (member.role === 'loan_officer' || member.role === 'sales_manager')
  ));
}

/** The roster through one shared cache entry, narrowed by `select`. */
export function useSalesRoster<T>(select: (members: SalesTeamMember[]) => T) {
  return useQuery({
    queryKey: queryKeys.salesRoster(),
    queryFn: ({ signal }) => api.salesTeam(signal),
    select,
    staleTime: 60_000,
  });
}
