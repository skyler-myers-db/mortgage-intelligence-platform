/**
 * Audited Lead Queue CSV export: the receipt contract and the two digests the
 * browser computes before it asks for one (audit tables-08, wave 1a).
 *
 * The CSV bytes never leave the browser. What the server owns is the ledger
 * row: `POST /api/leads/export-receipt` writes a LEAD_EXPORT event and the
 * download waits for its answer. The client declares what the file holds;
 * the server recomputes the borrower-id digest from the ids it is sent and
 * refuses a declaration that does not describe them.
 */
export type LeadExportScope = 'selected' | 'loaded';

/** The shape of `planLeadCsvExport`'s result this module needs (no component import). */
export interface LeadExportPlanLike {
  scope: 'selected_rows' | 'loaded_rows';
  rows: ReadonlyArray<{ borrower_id: string }>;
}

export interface LeadExportReceiptRequest {
  scope: LeadExportScope;
  row_count: number;
  /** SHA-256 (hex) of the exact CSV text handed to the download. */
  csv_sha256: string;
  /** Masked borrower ids in file order. */
  borrower_ids: string[];
  /** SHA-256 (hex) of `JSON.stringify(borrower_ids)`; verified server-side. */
  borrower_ids_sha256: string;
  /** The queue's query parameters (the CSV's `# filters=` line); fingerprinted server-side. */
  filters: Record<string, string>;
}

export interface LeadExportReceipt {
  audit_event_id: string;
  event_type: 'LEAD_EXPORT';
  actor: string;
  scope: LeadExportScope;
  row_count: number;
  csv_sha256: string;
  borrower_ids_sha256: string;
  filter_fingerprint: string;
  recorded_at: string;
}

const FILTER_KEY_RE = /^[a-z][a-z0-9_]{0,63}$/;

/** Hex SHA-256 of a UTF-8 string through WebCrypto; throws where it is unavailable. */
export async function sha256Hex(text: string): Promise<string> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) {
    throw new Error('This browser cannot hash the export for its audit receipt.');
  }
  const digest = await subtle.digest('SHA-256', new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

/** The scope token the ledger records for an export plan. */
export function leadExportScope(plan: Pick<LeadExportPlanLike, 'scope'>): LeadExportScope {
  return plan.scope === 'selected_rows' ? 'selected' : 'loaded';
}

/**
 * The queue's query string (`buildLeadQueueExportFilters`, or `none`) as the
 * flat map the receipt fingerprints. Only well-formed parameter names pass;
 * the first value of a repeated key wins, as the API reads it.
 */
export function leadExportFiltersFromQuery(filters: string | undefined | null): Record<string, string> {
  if (!filters || filters === 'none') return {};
  const out: Record<string, string> = {};
  for (const [key, value] of new URLSearchParams(filters)) {
    if (!FILTER_KEY_RE.test(key) || key in out) continue;
    out[key] = value;
  }
  return out;
}

/** The declaration for one planned export, digests included. */
export async function buildLeadExportDeclaration(
  csv: string,
  plan: LeadExportPlanLike,
  filters: string | undefined | null,
): Promise<LeadExportReceiptRequest> {
  const borrowerIds = plan.rows.map((row) => row.borrower_id);
  const [csvSha256, idsSha256] = await Promise.all([
    sha256Hex(csv),
    sha256Hex(JSON.stringify(borrowerIds)),
  ]);
  return {
    scope: leadExportScope(plan),
    row_count: borrowerIds.length,
    csv_sha256: csvSha256,
    borrower_ids: borrowerIds,
    borrower_ids_sha256: idsSha256,
    filters: leadExportFiltersFromQuery(filters),
  };
}
