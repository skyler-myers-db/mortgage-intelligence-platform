/**
 * Wire types of the audit-free Lead Queue aggregates and the user-saved
 * Lead Queue views (W5a, audit tables-06 / tables-09 phase 2). Each name is
 * the Pydantic schema it mirrors: backend/schemas/lead_facets.py and
 * backend/schemas/saved_views.py.
 */

export type LeadFacetDimension = 'state' | 'segment' | 'product' | 'approval';

/** GET /api/leads/count. */
export interface LeadCountResponse {
  total_matching: number;
}

export interface LeadFacetBucket {
  /** A USPS code, a segment code, a product label or an approval state. */
  value: string;
  count: number;
}

/** GET /api/leads/facets?dimension=… */
export interface LeadFacetsResponse {
  dimension: LeadFacetDimension;
  total_matching: number;
  buckets: LeadFacetBucket[];
}

/** One saved Lead Queue view; `params` is the canonical query, no leading `?`. */
export interface SavedView {
  view_id: string;
  name: string;
  params: string;
  created_at: string;
  updated_at: string;
}

/** GET /api/workspace/saved-views. */
export interface SavedViewListResponse {
  saved_views: SavedView[];
}

/** POST /api/workspace/saved-views. */
export interface SavedViewCreateRequest {
  name: string;
  params: string;
}

/** POST and DELETE /api/workspace/saved-views. */
export interface SavedViewMutationResponse {
  ok: boolean;
  view_id: string;
  audit_event_id: string | null;
}
