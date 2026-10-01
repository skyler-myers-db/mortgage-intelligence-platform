/**
 * The audit-free Lead Queue facet counts (audit tables-06): how many
 * borrowers each option of one filter menu holds, with every OTHER filter
 * applied. GET /api/leads/facets writes no audit row.
 *
 * Imported directly by the Lead Queue route chunk, never spread into `api`:
 * api.ts sits in the initial closure (Topbar, AppContext), and this client is
 * needed only after a menu is opened (precedent: rateScenario, genieJobs).
 */
import type { LeadFacetDimension, LeadFacetsResponse } from '../../types/leadFilters';
import type { LeadsRequest } from '../leadsQuery';
import { getJson } from '../apiTransport';
import { leadsQueryParams } from './leads';

/** The params a dimension's own filter lives in; the counts drop them. */
const OWN_PARAMS: Readonly<Record<LeadFacetDimension, readonly string[]>> = {
  state: ['state', 'states'],
  segment: ['segment', 'segment_codes', 'segment_mode'],
  product: ['product'],
  approval: ['approval_status'],
};

/**
 * `dimension=…` then the queue's own filter params for `request`, minus the
 * dimension's own filter, the page size and any Growth Agent proof (a count
 * binds no rows, and the server never reads a handoff here).
 */
export function leadFacetsQuery(dimension: LeadFacetDimension, request: LeadsRequest): string {
  const filters = leadsQueryParams(request.segment, request.geo, { ...request.opts, limit: undefined }, null);
  for (const key of OWN_PARAMS[dimension]) filters.delete(key);
  const params = new URLSearchParams({ dimension });
  for (const [key, value] of filters) params.append(key, value);
  return params.toString();
}

export function fetchLeadFacets(
  dimension: LeadFacetDimension,
  request: LeadsRequest,
  signal?: AbortSignal,
): Promise<LeadFacetsResponse> {
  return getJson<LeadFacetsResponse>(`/api/leads/facets?${leadFacetsQuery(dimension, request)}`, signal);
}
