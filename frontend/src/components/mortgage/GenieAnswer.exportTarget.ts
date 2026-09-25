import type { GenieExportScope } from '../../lib/apiClients/genieExport';

/**
 * What the Genie answer chunk needs to OFFER the audited CSV (audit
 * 2026-09-21 `genie-06`, slice 2): the target shape, the row cap and the
 * fixed copy. The flow itself (GenieAnswer.export.ts, the receipt client and
 * the hashing) is loaded only when the reader clicks "Download CSV".
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
