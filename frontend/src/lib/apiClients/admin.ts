/**
 * Admin and data-estate endpoint clients: reviewed rule versions, source and
 * operation status, capability probes, the data estate and asset metadata,
 * the lineage manifest, config options, and the property lookup.
 *
 * Members moved verbatim out of `api.ts`, which spreads them back into the
 * exported `api` object in the original order. Consumers import `api` from
 * `../api` — never from this module.
 */
import type {
  ConfigOptions,
  DataEstateResponse,
  AssetMetadataResponse,
  LineageManifestResponse,
  PropertyLoanLookupRequest,
  PropertyLoanLookupResponse,
} from '../../types';
import { getJson, postJson } from '../apiTransport';

export const adminApi = {
  /**
   * Admin rules probe — used by the Administration route's "Offer rules"
   * tile. Routes through getJson so a 503 retryable turns into an
   * ApiError the useWarmingUpRetry hook can detect, matching every
   * other route's cold-start UX.
   */
  adminRules: <T>(signal?: AbortSignal) =>
    getJson<T>('/api/admin/rules', signal),

  /**
   * Admin data-source readiness probe — per-source rows with status,
   * row counts, and DESCRIBE DETAIL lastModified stamps. Same warming-up
   * semantics as adminRules.
   */
  adminSources: <T>(signal?: AbortSignal) =>
    getJson<T>('/api/admin/sources', signal),

  adminOperations: <T>(signal?: AbortSignal) =>
    getJson<T>('/api/admin/operations', signal),

  /**
   * DAIS-2026 capability snapshot — honest per-capability provisioning
   * status. Drives the admin "Agentic capability readiness" panel. Rows
   * that aren't `claimable` render as roadmap, never as integrated.
   */
  adminCapabilities: <T>(signal?: AbortSignal) =>
    getJson<T>('/api/admin/capabilities?live=1', signal),

  adminRunOperation: <T>(
    payload: {
      job_key: string;
      confirm: true;
      reason?: string | null;
      request_id: string;
    },
    signal?: AbortSignal,
  ) =>
    postJson<T, typeof payload>('/api/admin/operations/run', payload, signal),

  dataEstate: (signal?: AbortSignal) =>
    getJson<DataEstateResponse>('/api/data-estate', signal),

  assetMetadata: (assetKey: string, signal?: AbortSignal) =>
    getJson<AssetMetadataResponse>(
      `/api/admin/assets/${encodeURIComponent(assetKey)}/metadata`,
      signal,
    ),

  /**
   * Governed lineage manifest for the EvidenceDrawer Lineage tab. The
   * payload is repo-committed product truth (backend/resources/
   * lineage_manifest.json) resolved with this deployment's catalog and
   * Catalog Explorer deep links — static per deploy, so callers cache it.
   */
  lineageManifest: (signal?: AbortSignal) =>
    getJson<LineageManifestResponse>('/api/lineage/manifest', signal),

  configOptions: (signal?: AbortSignal) =>
    getJson<ConfigOptions>('/api/config/options', signal),

  /**
   * Governed property loan lookup. Resolves a caller-supplied street
   * address + ZIP to a masked CLIP and its loan facts, deep-linking to the
   * governed borrower dossier when the property maps to a scored borrower.
   *
   * Boundary: SHARE-SCOPED, EXACT-after-canonicalization — NOT Cotality's
   * fuzzy CLIP mastering. The response NEVER echoes `address_line`; the
   * caller must not persist the address either (component state only). A
   * 422 carries a fixed sanitized detail; a 503 flows through the standard
   * dependency-down path so callers render the degraded-state UI.
   */
  propertyLookup: (
    payload: PropertyLoanLookupRequest,
    signal?: AbortSignal,
  ) =>
    postJson<PropertyLoanLookupResponse, PropertyLoanLookupRequest>(
      '/api/lookup/property-loan',
      payload,
      signal,
    ),
};
