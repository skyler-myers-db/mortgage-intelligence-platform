/**
 * The audit-free Lead Queue match count (W5e: the filter omnibox's live
 * count, wow-power-6, and More like this, wow-power-2): how many borrowers a
 * reviewed filter pattern matches. GET /api/leads/count writes no audit row
 * and refuses borrower_ids, approval_request_batch and include_identity_proof
 * (backend/api/leads.py).
 *
 * The query is built from an ALLOWLIST of the grammar / pattern keys only,
 * in this fixed order, so a count can never carry a borrower list, an
 * approval-request scope, a Genie cohort, an include_* switch, a page size,
 * a cursor, a sort or a Growth Agent proof, whatever the caller hands in.
 * Imported by the omnibox and the More like this chunks only, never spread
 * into `api` (api.ts sits in the initial closure; precedent: leadFacets.ts).
 */
import type { LeadCountResponse } from '../../types/leadFilters';
import { getJson } from '../apiTransport';

/** The only keys a count query may carry, in the order they are written. */
export const LEAD_COUNT_QUERY_KEYS = [
  'state',
  'states',
  'segment',
  'segment_codes',
  'segment_mode',
  'product',
  'lender_relationship',
  'approval_status',
  'outreach_status',
  'aged_days',
  'min_opportunity_score',
  'max_opportunity_score',
  'min_rate_spread_bps',
  'max_rate_spread_bps',
] as const;

/** The allowlisted, non-empty params of `params`, in LEAD_COUNT_QUERY_KEYS order. */
export function leadCountQuery(params: URLSearchParams): string {
  const query = new URLSearchParams();
  for (const key of LEAD_COUNT_QUERY_KEYS) {
    const value = params.get(key)?.trim();
    if (value) query.set(key, value);
  }
  return query.toString();
}

export function fetchLeadCount(params: URLSearchParams, signal?: AbortSignal): Promise<LeadCountResponse> {
  return getJson<LeadCountResponse>(`/api/leads/count?${leadCountQuery(params)}`, signal);
}
