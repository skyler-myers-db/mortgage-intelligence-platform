/**
 * Asset freshness client (audit 2026-09-21 `critic-03`, D-audit-reads-c1).
 *
 * Lazy-only, NOT spread into `api`: imported by EvidenceFreshness, which the
 * evidence drawer's lazy body loads. The GET is audit-free and issued only
 * while a drawer is open on a mapped asset, for every authenticated user,
 * never on hover, prefetch, idle or poll. Schema internals stay on the admin
 * metadata read (`api.assetMetadata`).
 */
import type { AssetFreshnessResponse } from '../apiTypes';
import { getJson } from '../apiTransport';

export const assetsApi = {
  assetFreshness: (assetKey: string, signal?: AbortSignal) =>
    getJson<AssetFreshnessResponse>(`/api/assets/${encodeURIComponent(assetKey)}/freshness`, signal),
};
