import { csvEscape, downloadCsvText } from '../../lib/csv';
import { sha256Hex } from '../../lib/apiClients/leadExport';
import type {
  GenieAnswerExportReceipt,
  GenieAnswerExportReceiptRequest,
} from '../../lib/apiClients/genieExport';
import { formatCell, formatIdentifier, isIdentifierColumn, isSegmentColumn } from './GenieAnswer.logic';
import {
  GENIE_EXPORT_BLOCKED_DOWNLOAD,
  GENIE_EXPORT_DOWNLOADED,
  GENIE_EXPORT_MAX_ROWS,
  GENIE_EXPORT_NOT_IN_HISTORY,
  GENIE_EXPORT_NOT_RECORDED,
  type GenieRowsExportTarget,
} from './GenieAnswer.exportTarget';

export {
  GENIE_EXPORT_BLOCKED_DOWNLOAD,
  GENIE_EXPORT_DOWNLOADED,
  GENIE_EXPORT_MAX_ROWS,
  GENIE_EXPORT_NOT_IN_HISTORY,
  GENIE_EXPORT_NOT_RECORDED,
};
export type { GenieAnswerExportBase, GenieRowsExportTarget } from './GenieAnswer.exportTarget';

/**
 * The audited Genie answer CSV (audit 2026-09-21 `genie-06`, slice 2).
 *
 * Order of operations is the contract:
 *   1. build the CSV text once, from the rows the answer ALREADY holds (no
 *      full-cohort or server-streamed export: an owner decision);
 *   2. hash the text and the column keys in the browser;
 *   3. POST the declaration (ids, counts, digests; never question text) and
 *      WAIT for the GENIE_ANSWER_EXPORT receipt;
 *   4. only then hand the same text to the download.
 * Any refusal or error means no download, with fixed copy. The flow is a
 * module-level promise that never rejects, so the component that starts it
 * needs no try statement (the React Compiler cannot compile one). This
 * module is an interaction chunk: GenieAnswerRowsActions imports it on the
 * click, so the answer chunk carries none of it.
 */

export interface GenieAnswerCsvRequest {
  rows: ReadonlyArray<Record<string, unknown>>;
  /** Raw column keys, first-seen union. */
  columns: readonly string[];
  target: GenieRowsExportTarget;
  /** The row count the answer (or section) reports, when it reports one. */
  reportedRowCount: number | null;
}

/**
 * One cell as the file holds it: raw values (numbers ungrouped), identifier
 * columns zero-padded the way the table shows them, segment columns as their
 * reviewed names, an empty string for null.
 */
export function genieCsvCell(column: string, value: unknown): string {
  if (value === null || value === undefined) return '';
  if (isSegmentColumn(column)) return formatCell(column, value);
  if (isIdentifierColumn(column)) return formatIdentifier(column, value);
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : '';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

/** The CSV text: `# key=value` provenance lines, the raw header, the rows. */
export function buildGenieAnswerCsv(request: GenieAnswerCsvRequest, generatedAt: string): string {
  const { rows, columns, target, reportedRowCount } = request;
  const provenance: Array<[string, string | number]> = [
    ['generated_at', generatedAt],
    ['source', target.source],
    ['trusted_assets', target.trustedAssets.length > 0 ? target.trustedAssets.join('|') : 'none'],
    ['export_scope', target.scope],
    ['section_index', target.sectionIndex ?? 'none'],
    ['exported_rows', rows.length],
    ['answer_row_count', reportedRowCount ?? 'unknown'],
    ['rows_complete', reportedRowCount === null ? 'unknown' : String(rows.length >= reportedRowCount)],
  ];
  const lines = provenance.map(([key, value]) => `# ${key}=${String(value).replace(/\r?\n/g, ' ')}`);
  const header = columns.map((column) => csvEscape(column)).join(',');
  const body = rows.map((row) => columns.map((column) => csvEscape(genieCsvCell(column, row[column]))).join(','));
  return [...lines, header, ...body].join('\n');
}

export function genieCsvFilename(target: GenieRowsExportTarget, generatedAt: string): string {
  const day = generatedAt.slice(0, 10);
  const part = target.sectionIndex === null ? '' : `-section-${target.sectionIndex}`;
  return `mip-genie-answer${part}-${day}.csv`;
}

/**
 * The HTTP status an ApiError carries, read structurally: importing the
 * transport (or lib/api) from this interaction chunk makes the bundler split
 * that module out of the entry chunk and grow the initial closure.
 */
function httpStatus(error: unknown): number | null {
  if (typeof error !== 'object' || error === null || !('status' in error)) return null;
  return typeof error.status === 'number' ? error.status : null;
}

export type GenieExportOutcome =
  | { kind: 'downloaded'; message: string; receipt: GenieAnswerExportReceipt }
  | { kind: 'refused'; message: string };

export interface GenieExportDeps {
  /** The receipt POST (lib/apiClients/genieExport postGenieExportReceipt),
   *  handed in by the caller so this interaction chunk imports no transport. */
  post: (declaration: GenieAnswerExportReceiptRequest) => Promise<GenieAnswerExportReceipt>;
  hash?: (text: string) => Promise<string>;
  download?: (csv: string, filename: string) => void;
  now?: () => Date;
}

/** Build, declare, wait for the receipt, then download. Never rejects. */
export async function exportGenieAnswerCsv(
  request: GenieAnswerCsvRequest,
  deps: GenieExportDeps,
): Promise<GenieExportOutcome> {
  const { post, hash = sha256Hex, download = downloadCsvText, now = () => new Date() } = deps;
  if (request.rows.length === 0 || request.rows.length > GENIE_EXPORT_MAX_ROWS) {
    return { kind: 'refused', message: GENIE_EXPORT_NOT_RECORDED };
  }
  const generatedAt = now().toISOString();
  const csv = buildGenieAnswerCsv(request, generatedAt);
  let receipt: GenieAnswerExportReceipt;
  try {
    const [csvSha256, columnsSha256] = await Promise.all([
      hash(csv),
      hash(JSON.stringify(request.columns)),
    ]);
    receipt = await post({
      conversation_id: request.target.conversationId,
      message_id: request.target.messageId,
      scope: request.target.scope,
      row_count: request.rows.length,
      answer_row_count: request.reportedRowCount,
      csv_sha256: csvSha256,
      columns_sha256: columnsSha256,
    });
  } catch (error) {
    return { kind: 'refused', message: httpStatus(error) === 404 ? GENIE_EXPORT_NOT_IN_HISTORY : GENIE_EXPORT_NOT_RECORDED };
  }
  try {
    download(csv, genieCsvFilename(request.target, generatedAt));
  } catch {
    return { kind: 'refused', message: GENIE_EXPORT_BLOCKED_DOWNLOAD };
  }
  return { kind: 'downloaded', message: GENIE_EXPORT_DOWNLOADED, receipt };
}
