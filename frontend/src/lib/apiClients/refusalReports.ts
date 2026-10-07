/**
 * Genie refusal reports for administrators and auditors (D-audit-reads-d).
 *
 * Lazy-only, NOT spread into `api`: imported by the RefusalReportsPanel chunk
 * the audit ledger loads on an explicit "Show refusal reports".
 *
 *  - `list` reads a page of report metadata (never question text). The
 *    server records each served page as one VIEW_AUDIT_LEDGER row, so the
 *    panel calls it only on open, a filter change, Load more or Retry.
 *  - `question` reads ONE consented question. The server writes a fail-closed
 *    VIEW_REFUSAL_REPORT_TEXT row before it answers, so the panel calls it
 *    only inside a click handler: never on hover, focus, prefetch or through
 *    the query cache, and the text lives only in component state.
 */
import type { GenieRefusalReason } from '../../types';
import { getJson } from '../apiTransport';

export interface RefusalReportItem {
  report_id: string;
  reported_at: string;
  refusal_reason: GenieRefusalReason;
  /** The reporting staff member (actor_email). */
  reporter: string;
  conversation_id: string | null;
  message_id: string | null;
  /** A consented question is held (unpurged and unexpired). */
  has_text: boolean;
  text_expires_at: string | null;
  audit_event_id: string | null;
}

export interface RefusalReportFamilyCount {
  refusal_reason: GenieRefusalReason;
  count: number;
}

export interface RefusalReportListResponse {
  items: RefusalReportItem[];
  family_counts: RefusalReportFamilyCount[];
  next_cursor: string | null;
}

export interface RefusalReportQuestionResponse {
  question_text: string;
  redacted: boolean;
  captured_at: string;
  expires_at: string;
}

/** The list's own filter state: the family filter and the keyset cursor. */
export interface RefusalReportFilters {
  family: GenieRefusalReason | null;
  cursor: string | null;
}

export const REFUSAL_REPORTS_PAGE_SIZE = 50;

export const refusalReportsApi = {
  list: (filters: RefusalReportFilters, signal?: AbortSignal) => {
    const params = new URLSearchParams();
    params.set('limit', String(REFUSAL_REPORTS_PAGE_SIZE));
    if (filters.family) params.set('family', filters.family);
    if (filters.cursor) params.set('cursor', filters.cursor);
    return getJson<RefusalReportListResponse>(`/api/audit/refusal-reports?${params.toString()}`, signal);
  },

  question: (reportId: string, signal?: AbortSignal) =>
    getJson<RefusalReportQuestionResponse>(
      `/api/audit/refusal-reports/${encodeURIComponent(reportId)}/question`,
      signal,
    ),
};
