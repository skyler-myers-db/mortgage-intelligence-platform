/**
 * Genie answer / proof / action contracts.
 *
 * Extracted verbatim from types.ts (2026-07-07) to keep that module under
 * the file-size gate; re-exported from ../types so no call site changes.
 */

/**
 * GenieAnswer — the widened response shape from /api/genie/message.
 * `answer` + `source` + `trusted_assets` are the original fields; the
 * optional ones (metric_value, table_rows, follow_up_questions) arrived in
 * slice 8 and drive the richer presenter UX.
 */
export interface GenieAnswer {
  answer: string;
  source?: string;
  trusted_assets?: string[];
  conversation_id?: string;
  message_id?: string | null;
  elapsed_ms?: number | null;
  question_hash?: string | null;
  sql_query?: string | null;
  row_count?: number | null;
  proof?: GenieProof | null;
  visualization?: GenieVisualization | null;
  actions?: GenieActionSuggestion[];
  metric_value?: string | null;
  table_rows?: Record<string, unknown>[] | null;
  follow_up_questions?: string[];
  /**
   * Databricks-native visualization descriptor. Non-null when Genie
   * generated a native chart attachment for this answer. We NEVER render a
   * chart from this — in-app native rendering only activates once the
   * workspace enables the Beta download API. Presence drives the neutral
   * "Native chart · Beta" marker chip; the heuristic table/chart layer is
   * unchanged and remains the display surface.
   */
  native_visualization?: GenieNativeVisualization | null;
  /**
   * Top-level Genie reasoning trace (Public Preview). Distinct from
   * `proof.reasoning_trace` (the trusted "query trace"). Rendered as a
   * collapsed-by-default plain-text section in the proof drawer. No PII
   * expectation, but always rendered as escaped plain text.
   */
  reasoning_trace?: GenieReasoningStep[];
  /**
   * Terminal Genie message status (e.g. FILTERING_CONTEXT, ASKING_AI). Maps
   * to human-readable staged progress copy. Unknown/absent → generic state.
   */
  genie_status?: string | null;
}

export interface GenieNativeVisualization {
  attachment_id: string;
  query_attachment_id?: string | null;
  title?: string | null;
}

export interface GenieReasoningStep {
  kind: string;
  content: string;
}

export interface GenieFreshness {
  asset: string;
  refreshed_at?: string | null;
  status: string;
  note?: string | null;
}

export interface GenieProof {
  sql_query?: string | null;
  source_assets?: string[];
  data_freshness?: GenieFreshness[];
  row_count?: number | null;
  filters?: string[];
  trusted?: boolean;
  reasoning_trace?: GenieReasoningStep[];
  known_data_gaps?: string[];
  conversation_id?: string | null;
  message_id?: string | null;
  elapsed_ms?: number | null;
  generated_at?: string | null;
}

export interface GenieVisualization {
  kind: 'metric' | 'bar' | 'line' | 'funnel' | 'scatter' | 'map' | 'table' | 'borrower_list' | 'strategy_board' | string;
  title?: string | null;
  x?: string | null;
  y?: string | null;
  series?: string | null;
  reason?: string | null;
}

export interface GenieStartResult {
  conversation_id?: string | null;
  trusted_assets?: string[];
  sample_questions?: string[];
}

export interface GenieActionSuggestion {
  id: string;
  label: string;
  action_type: string;
  description: string;
  requires_confirmation?: boolean;
  route?: string | null;
  borrower_ids?: string[];
  criteria?: Record<string, unknown>;
  request_id?: string | null;
  confirmation_token?: string | null;
}

export interface GenieActionResult {
  ok: boolean;
  action_type: string;
  audit_event_id?: string | null;
  route?: string | null;
  saved_count?: number;
  campaign_id?: string | null;
  message: string;
}

