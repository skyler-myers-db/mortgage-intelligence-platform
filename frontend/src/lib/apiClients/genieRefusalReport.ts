/**
 * The "This was legitimate" refusal-report client (audit 2026-09-21
 * `genie-05`, D-audit-reads-d).
 *
 * Lazy-only, NOT spread into `api`: imported by the refusal card. The body
 * carries the coarse family and the `refusal_report_hash` the refused turn
 * returned; `question_text` is present only when the reporter chose "Report
 * with my question", and is then the exact question string the client sent.
 * The server binds it to that hash and to a refusal in the reporter's own
 * ledger, scrubs it and keeps it 90 days; `question_captured` says whether
 * it was kept. The question never reaches a URL, browser storage, RUM or a
 * log line from here.
 */
import type { GenieRefusalReason } from '../../types';
import type { GenieRefusalReportResult } from '../apiTypes';
import { postJson } from '../apiTransport';

/** The `/api/genie/refusal-report` body. */
export interface GenieRefusalReportRequest {
  question_hash: string;
  refusal_reason: GenieRefusalReason;
  conversation_id: string | null;
  message_id: string | null;
  /** Only on "Report with my question": the question exactly as it was asked. */
  question_text?: string;
}

export const genieRefusalReportApi = {
  report: (body: GenieRefusalReportRequest, signal?: AbortSignal) =>
    postJson<GenieRefusalReportResult, GenieRefusalReportRequest>('/api/genie/refusal-report', body, signal),
};
