/**
 * Audit explorer clients (W5d, tables-10): the explorer's page read with the
 * per-viewer exclusion, the facet and count reads, and the AUDIT_EXPORT
 * receipt its page CSV waits for.
 *
 * Every read here is a RECORDED ledger read: the server writes one
 * VIEW_AUDIT_LEDGER row per served request (D-audit-reads-c3). So the
 * explorer calls them only on a deliberate act: the page on a filter set or
 * page change, the facets on an explicit menu open, the count on a click.
 * Never on hover, prefetch or a poll. The CSV declaration (the hashing) is
 * built in AdminAuditExplorer.export.ts, a click-time chunk.
 */
import type { AuditEventPage } from '../apiTypes';
import { getJson, postJson } from '../apiTransport';

/** The /events/page and /count filters (the URL's applied set plus the exclusion). */
export interface AuditExplorerQuery {
  actor?: string | null;
  action?: string | null;
  entity_id?: string | null;
  borrower_id?: string | null;
  subject_clip?: string | null;
  event_type?: string | null;
  since?: string | null;
  until?: string | null;
  correlation_id?: string | null;
  event_id?: string | null;
  /** Drop the VIEW_AUDIT_LEDGER rows the signed-in reader wrote (server identity). */
  exclude_own_ledger_reads?: boolean;
}

export interface AuditFacetValue {
  value: string;
  count: number;
}

export interface AuditFacetsResponse {
  event_types: AuditFacetValue[];
  actions: AuditFacetValue[];
  actors: AuditFacetValue[];
  truncated: { event_types: boolean; actions: boolean; actors: boolean };
  since: string;
  until: string | null;
}

export interface AuditCountResponse {
  count: number;
  capped: boolean;
  cap: number;
}

export interface AuditExportReceiptRequest {
  row_count: number;
  /** SHA-256 (hex) of the exact CSV text handed to the download. */
  csv_sha256: string;
  /** Audit event ids in file order. */
  event_ids: string[];
  /** SHA-256 (hex) of `JSON.stringify(event_ids)`; verified server-side. */
  event_ids_sha256: string;
  /** The explorer query the rows were read with; only its keyed fingerprint is stored. */
  filters: Record<string, string>;
}

export interface AuditExportReceipt {
  audit_event_id: string;
  event_type: 'AUDIT_EXPORT';
  actor: string;
  row_count: number;
  csv_sha256: string;
  event_ids_sha256: string;
  filter_fingerprint: string;
  recorded_at: string;
}

function queryString(query: AuditExplorerQuery, extra: Record<string, string> = {}): string {
  const params = new URLSearchParams(extra);
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined || value === '' || value === false) continue;
    params.set(key, String(value));
  }
  return params.toString();
}

/** The query as the receipt's flat string map (only the set parameters). */
export function auditQueryFilters(query: AuditExplorerQuery): Record<string, string> {
  return Object.fromEntries(new URLSearchParams(queryString(query)));
}

export function auditEventPage(
  limit: number,
  signal: AbortSignal | undefined,
  query: AuditExplorerQuery & { cursor?: string | null },
): Promise<AuditEventPage> {
  const { cursor, ...filters } = query;
  const extra: Record<string, string> = { limit: String(limit) };
  if (cursor) extra.cursor = cursor;
  return getJson<AuditEventPage>(`/api/audit/events/page?${queryString(filters, extra)}`, signal);
}

export function auditFacets(
  window: { since: string | null; until: string | null },
  signal?: AbortSignal,
): Promise<AuditFacetsResponse> {
  const params = queryString({ since: window.since, until: window.until });
  return getJson<AuditFacetsResponse>(`/api/audit/facets?${params}`, signal);
}

export function auditCount(query: AuditExplorerQuery, signal?: AbortSignal): Promise<AuditCountResponse> {
  const params = queryString({ ...query, event_id: null });
  return getJson<AuditCountResponse>(`/api/audit/count?${params}`, signal);
}

/** The AUDIT_EXPORT ledger row the explorer's page CSV waits for (admin only). */
export function createAuditExportReceipt(body: AuditExportReceiptRequest): Promise<AuditExportReceipt> {
  return postJson<AuditExportReceipt, AuditExportReceiptRequest>('/api/audit/export-receipt', body);
}
