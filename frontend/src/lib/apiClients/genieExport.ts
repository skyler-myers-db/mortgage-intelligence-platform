import { postJson } from '../apiTransport';

/**
 * Audited Genie answer CSV export: the receipt contract (audit 2026-09-21
 * `genie-06`, slice 2).
 *
 * The CSV is built in the browser from rows the answer already holds; the
 * server owns the ledger row. `POST /api/genie/export-receipt` checks that
 * the answer is a trusted message of the caller's own conversation, then
 * writes one GENIE_ANSWER_EXPORT event, and the download waits for its
 * answer. The declaration carries ids, counts and two digests only; the
 * server refuses any other field.
 *
 * Deliberately NOT spread into `api` (lib/api.ts is in the initial closure):
 * only the Genie answer chunk imports this module.
 * Types mirror backend/schemas/genie_export.py by hand until the generated
 * client covers them (then they become type-identical aliases).
 */

export type GenieExportScope = 'answer' | 'section';

export interface GenieAnswerExportReceiptRequest {
  conversation_id: string;
  message_id: string;
  scope: GenieExportScope;
  /** Rows the file holds. */
  row_count: number;
  /** The row count the answer (or section) reports, when it reports one. */
  answer_row_count: number | null;
  /** SHA-256 (hex) of the exact CSV text handed to the download. */
  csv_sha256: string;
  /** SHA-256 (hex) of `JSON.stringify(columns)`, the file's column keys. */
  columns_sha256: string;
}

export interface GenieAnswerExportReceipt {
  audit_event_id: string;
  event_type: 'GENIE_ANSWER_EXPORT';
  actor: string;
  scope: GenieExportScope;
  row_count: number;
  csv_sha256: string;
  columns_sha256: string;
  recorded_at: string;
}

/** The constant 404 detail (GENIE_EXPORT_NOT_FOUND_DETAIL, genie_feedback_routes.py). */
export const GENIE_EXPORT_NOT_FOUND_DETAIL = 'Genie answer not found';

export function postGenieExportReceipt(
  declaration: GenieAnswerExportReceiptRequest,
  signal?: AbortSignal,
): Promise<GenieAnswerExportReceipt> {
  return postJson<GenieAnswerExportReceipt, GenieAnswerExportReceiptRequest>(
    '/api/genie/export-receipt',
    declaration,
    signal,
  );
}
