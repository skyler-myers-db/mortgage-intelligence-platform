/**
 * Unity Catalog asset → workspace Catalog Explorer deep links.
 *
 * The backend already emits `catalog_explorer_url` for lineage/data-estate
 * nodes (see `backend/services/asset_metadata_utils.py::catalog_explorer_url`).
 * Genie answers do NOT carry that field — they carry bare dotted names
 * ("mip.gold.borrower_360") in the trailing "Source: …" line of the answer
 * markdown and in `proof.source_assets`. This module turns those bare names
 * into the same `{host}/explore/data/{catalog}/{schema}/{table}` URL the
 * backend builds, using the workspace host published on the health payload.
 *
 * Host validation deliberately mirrors the backend's `workspace_origin()`
 * guard (https only, no credentials/path/query/fragment, hostname must end in
 * a known Databricks workspace suffix). A Genie answer is model-influenced
 * text; an unvalidated host would let a bad value render an off-platform link
 * that looks like a governed Catalog Explorer destination.
 */

/** Hostname suffixes accepted as a Databricks workspace origin. Mirrors
 *  `_DATABRICKS_WORKSPACE_SUFFIXES` in backend/services/asset_metadata_utils.py. */
const WORKSPACE_HOST_SUFFIXES = ['.cloud.databricks.com', '.gcp.databricks.com', '.azuredatabricks.net'];

/** `catalog.schema.table` — UC identifiers are letters/digits/underscore. */
const UC_ASSET_RE = /^[A-Za-z0-9_]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+$/;

/**
 * Matches the trailing source disclosure Genie appends to an answer, e.g.
 * "Source: mip.gold.borrower_360". Case-insensitive on the "Source" label;
 * the asset itself must be a 3-part dotted UC name. A trailing period is
 * left OUT of the captured asset so sentence punctuation never leaks into
 * the URL.
 */
export const SOURCE_LINE_RE = /(Sources?:\s*)([A-Za-z0-9_]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+)/gi;

export interface UcAssetParts {
  catalog: string;
  schema: string;
  table: string;
}

/** Split a dotted UC name. Returns null for anything that is not exactly
 *  `catalog.schema.table` (wildcards, 2-part names, Lakebase refs, prose). */
export function parseUcAsset(asset: string | null | undefined): UcAssetParts | null {
  const raw = (asset ?? '').trim();
  if (!UC_ASSET_RE.test(raw)) return null;
  const [catalog, schema, table] = raw.split('.');
  return { catalog, schema, table };
}

/**
 * Normalize a workspace host into an https origin, or null when the value is
 * missing/untrustworthy. Accepts a bare hostname ("dbc-x.cloud.databricks.com")
 * or a full https origin; rejects http, credentials, paths, and any hostname
 * outside the Databricks workspace suffix allowlist.
 */
export function normalizeWorkspaceHost(host: string | null | undefined): string | null {
  const raw = (host ?? '').trim();
  if (!raw || /\s/.test(raw)) return null;
  const candidate = raw.includes('://') ? raw : `https://${raw}`;
  let url: URL;
  try {
    url = new URL(candidate);
  } catch {
    return null;
  }
  if (url.protocol !== 'https:') return null;
  if (url.username || url.password) return null;
  if (url.search || url.hash) return null;
  if (url.pathname !== '' && url.pathname !== '/') return null;
  const hostname = url.hostname.toLowerCase();
  if (!WORKSPACE_HOST_SUFFIXES.some((suffix) => hostname.endsWith(suffix))) return null;
  return `https://${url.host}`;
}

/**
 * Build the Catalog Explorer URL for a dotted UC asset name. Returns null
 * when the host is unknown/invalid or the asset is not a 3-part name — every
 * caller degrades to plain text on null.
 */
export function catalogExplorerUrl(
  workspaceHost: string | null | undefined,
  asset: string | null | undefined,
): string | null {
  const origin = normalizeWorkspaceHost(workspaceHost);
  const parts = parseUcAsset(asset);
  if (!origin || !parts) return null;
  return `${origin}/explore/data/${encodeURIComponent(parts.catalog)}/${encodeURIComponent(parts.schema)}/${encodeURIComponent(parts.table)}`;
}
