/**
 * The audit-free Lead Queue match count read (W5e: the filter omnibox's live
 * count, wow-power-6, and More like this, wow-power-2): how many borrowers a
 * reviewed filter pattern matches. GET /api/leads/count writes no audit row
 * and refuses borrower_ids, approval_request_batch and include_identity_proof
 * (backend/api/leads.py).
 *
 * The query is the allowlist in lib/leadCountQuery.ts. Imported by the
 * PARENTS of the lazy omnibox and More like this chunks only (the Lead Queue
 * route, the row preview, Borrower 360), which hand `leadCountQueryOptions`
 * down: they already reach api.ts, so the initial transport chunk stays one
 * chunk. Never spread into `api` (api.ts sits in the initial closure;
 * precedent: leadFacets.ts).
 */
import type { LeadCountResponse } from '../../types/leadFilters';
import { getJson } from '../apiTransport';
import { leadCountQuery, type LeadCountQueryOptions } from '../leadCountQuery';
import { queryKeys } from '../queryKeys';

export function fetchLeadCount(params: URLSearchParams, signal?: AbortSignal): Promise<LeadCountResponse> {
  return getJson<LeadCountResponse>(`/api/leads/count?${leadCountQuery(params)}`, signal);
}

/** The TanStack options of one count read, keyed by its allowlisted query. */
export function leadCountQueryOptions(query: string): LeadCountQueryOptions {
  return {
    queryKey: queryKeys.leadCount([query]),
    queryFn: ({ signal }) => fetchLeadCount(new URLSearchParams(query), signal),
  };
}
