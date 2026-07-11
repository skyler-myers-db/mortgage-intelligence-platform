/** S4 "since your last login" summary (GET /api/home/summary). The
 * deterministic template is the source of truth: `display`/`value_token`
 * are the exact server-minted number strings, and a `genie` phrasing is
 * only ever a validated rephrasing of those same tokens. */
export interface HomeSummaryHighlight {
  measure: string;
  label: string;
  display: string;
  value_token: string;
  current: number;
  baseline: number | null;
  delta: number | null;
  delta_pct: number | null;
}

export interface HomeSummary {
  status: 'delta' | 'first_visit' | 'no_baseline';
  previous_visit_at: string | null;
  baseline_snapshot_at: string | null;
  headline: string;
  phrasing_source: 'deterministic' | 'genie';
  phrasing_fallback_reason: string | null;
  highlights: HomeSummaryHighlight[];
  current: Record<string, number | null>;
  baseline: Record<string, number | null> | null;
  deltas: Record<string, number | null> | null;
  current_source: string;
  baseline_source: string;
}
