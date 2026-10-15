import type { ApiRequest, ApiResponse } from './api.gen';
/**
 * Wire types of the audit-free Lead Queue aggregates and the user-saved
 * Lead Queue views (W5a, audit tables-06 / tables-09 phase 2). Each name is
 * the Pydantic schema it mirrors: backend/schemas/lead_facets.py and
 * backend/schemas/saved_views.py.
 */

export type LeadFacetDimension = 'state' | 'segment' | 'product' | 'approval';

/** GET /api/leads/count. */
export type LeadCountResponse = ApiResponse<'LeadCountResponse'>;

export type LeadFacetBucket = ApiResponse<'LeadFacetBucket'>;

/** GET /api/leads/facets?dimension=… */
export type LeadFacetsResponse = ApiResponse<'LeadFacetsResponse'>;

/** One saved Lead Queue view; `params` is the canonical query, no leading `?`. */
export type SavedView = ApiResponse<'SavedView'>;

/** GET /api/workspace/saved-views. */
export type SavedViewListResponse = ApiResponse<'SavedViewListResponse'>;

/** POST /api/workspace/saved-views. */
export type SavedViewCreateRequest = ApiRequest<'SavedViewCreateRequest'>;

/** POST and DELETE /api/workspace/saved-views. */
export type SavedViewMutationResponse = ApiResponse<'SavedViewMutationResponse'>;
