import { ApiError } from '../../lib/apiTransport';
import { csvEscape, downloadCsvText } from '../../lib/csv';
import { sha256Hex } from '../../lib/apiClients/leadExport';
import {
  postGenieExportReceipt,
  type GenieAnswerExportReceipt,
  type GenieAnswerExportReceiptRequest,
  type GenieExportScope,
} from '../../lib/apiClients/genieExport';
import { formatCell, formatIdentifier, isIdentifierColumn, isSegmentColumn } from './GenieAnswer.logic';

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
 * needs no try statement (the React Compiler cannot compile one).
 */

/** Parity with the Lead Queue export cap (MAX_LEAD_LIMIT) and the server. */
export const GENIE_EXPORT_MAX_ROWS = 5_000;

export const GENIE_EXPORT_DOWNLOADED = 'CSV downloaded. The export is recorded in the audit log.';
export const GENIE_EXPORT_NOT_IN_HISTORY = "This answer can't be exported: it is not in your Genie history.";
export const GENIE_EXPORT_NOT_RECORDED = 'Export not recorded, so nothing was downloaded.';
export const GENIE_EXPORT_BLOCKED_DOWNLOAD = 'The export is recorded, but the browser blocked the download.';

/** Which answer, or which section of it, the rows belong to. */
export interface GenieRowsExportTarget {
  conversationId: string;
  messageId: string;
  source: string;
  trustedAssets: readonly string[];
  scope: GenieExportScope;
  /** 1-based section number; null for the answer's own rows. */
  sectionIndex: number | null;
}

/** The answer-level part of a target; each rows block adds its scope. */
export type GenieAnswerExportBase = Omit<GenieRowsExportTarget, 'scope' | 'sectionIndex'>;

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

export type GenieExportOutcome =
  | { kind: 'downloaded'; message: string; receipt: GenieAnswerExportReceipt }
  | { kind: 'refused'; message: string };

export interface GenieExportDeps {
  hash: (text: string) => Promise<string>;
  post: (declaration: GenieAnswerExportReceiptRequest) => Promise<GenieAnswerExportReceipt>;
  download: (csv: string, filename: string) => void;
  now: () => Date;
}

const DEFAULT_DEPS: GenieExportDeps = {
  hash: sha256Hex,
  post: (declaration) => postGenieExportReceipt(declaration),
  download: downloadCsvText,
  now: () => new Date(),
};

/** Build, declare, wait for the receipt, then download. Never rejects. */
export async function exportGenieAnswerCsv(
  request: GenieAnswerCsvRequest,
  deps: GenieExportDeps = DEFAULT_DEPS,
): Promise<GenieExportOutcome> {
  if (request.rows.length === 0 || request.rows.length > GENIE_EXPORT_MAX_ROWS) {
    return { kind: 'refused', message: GENIE_EXPORT_NOT_RECORDED };
  }
  const generatedAt = deps.now().toISOString();
  const csv = buildGenieAnswerCsv(request, generatedAt);
  let receipt: GenieAnswerExportReceipt;
  try {
    const [csvSha256, columnsSha256] = await Promise.all([
      deps.hash(csv),
      deps.hash(JSON.stringify(request.columns)),
    ]);
    receipt = await deps.post({
      conversation_id: request.target.conversationId,
      message_id: request.target.messageId,
      scope: request.target.scope,
      row_count: request.rows.length,
      answer_row_count: request.reportedRowCount,
      csv_sha256: csvSha256,
      columns_sha256: columnsSha256,
    });
  } catch (error) {
    const notInHistory = error instanceof ApiError && error.status === 404;
    return { kind: 'refused', message: notInHistory ? GENIE_EXPORT_NOT_IN_HISTORY : GENIE_EXPORT_NOT_RECORDED };
  }
  try {
    deps.download(csv, genieCsvFilename(request.target, generatedAt));
  } catch {
    return { kind: 'refused', message: GENIE_EXPORT_BLOCKED_DOWNLOAD };
  }
  return { kind: 'downloaded', message: GENIE_EXPORT_DOWNLOADED, receipt };
}
