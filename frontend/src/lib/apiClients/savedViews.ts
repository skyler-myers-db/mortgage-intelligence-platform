/**
 * User-saved Lead Queue views (audit tables-09 phase 2, flow-08 slice 1):
 * list, save and delete under /api/workspace/saved-views. The list is
 * audit-free; save and delete write SAVE_QUEUE_VIEW / DELETE_QUEUE_VIEW in
 * the same Lakebase statement as the change.
 *
 * Imported directly by the lazy Saved views panel, never spread into `api`:
 * api.ts sits in the initial closure, and this client is needed only after
 * the panel is first opened. The save is an unkeyed POST, so the transport
 * never re-sends it after the handler may have run.
 */
import type {
  SavedViewCreateRequest,
  SavedViewListResponse,
  SavedViewMutationResponse,
} from '../../types/leadFilters';
import { deleteJson, getJson, postJson } from '../apiTransport';

const SAVED_VIEWS_PATH = '/api/workspace/saved-views';

export function fetchSavedViews(signal?: AbortSignal): Promise<SavedViewListResponse> {
  return getJson<SavedViewListResponse>(SAVED_VIEWS_PATH, signal);
}

export function createSavedView(request: SavedViewCreateRequest, signal?: AbortSignal): Promise<SavedViewMutationResponse> {
  return postJson<SavedViewMutationResponse, SavedViewCreateRequest>(SAVED_VIEWS_PATH, request, signal);
}

export function deleteSavedView(viewId: string, signal?: AbortSignal): Promise<SavedViewMutationResponse> {
  return deleteJson<SavedViewMutationResponse>(`${SAVED_VIEWS_PATH}/${encodeURIComponent(viewId)}`, signal);
}
