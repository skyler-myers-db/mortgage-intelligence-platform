/**
 * The borrower decision history (audit flow-04 phase 2, D-audit-reads-c2):
 * one borrower's governed decisions for the working team, from
 * GET /api/borrowers/{id}/decisions.
 *
 * The read is audit-free (it writes no VIEW_* row). It is read on a Borrower
 * 360 or Offer mount only: never on hover or idle, never polled, never
 * prefetched, never persisted. Its key sits under ['mip','borrower'], so
 * invalidateOperationalQueries marks it stale after every governed decision.
 *
 * Imported by the history chunk only, never spread into `api` (api.ts sits in
 * the initial closure).
 */
import type { ApiResponse } from '../../types/api.gen';
import { queryOptions } from '@tanstack/react-query';
import { getJson } from '../apiTransport';
import { queryKeys } from '../queryKeys';

/** One governed decision, projected server-side through closed vocabularies (no free text). */
export type BorrowerDecisionEvent = ApiResponse<'BorrowerDecisionEvent'>;

/** The latest 50 decisions, newest first; `truncated` when older ones exist. */
export type BorrowerDecisionHistoryResponse = ApiResponse<'BorrowerDecisionHistoryResponse'>;

export const borrowerDecisionsApi = {
  list: (id: string, signal?: AbortSignal) =>
    getJson<BorrowerDecisionHistoryResponse>(`/api/borrowers/${id}/decisions`, signal),
};

/** Thirty seconds fresh, no retry: a 403 hides the section, anything else offers Retry. */
export function borrowerDecisionsQuery(id: string) {
  return queryOptions<BorrowerDecisionHistoryResponse>({
    queryKey: queryKeys.borrowerDecisions(id),
    queryFn: ({ signal }) => borrowerDecisionsApi.list(id, signal),
    staleTime: 30_000,
    retry: false,
  });
}
