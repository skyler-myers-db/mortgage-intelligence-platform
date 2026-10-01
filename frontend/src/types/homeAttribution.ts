/**
 * Home Delta Explainer contracts (`GET /api/v1/home/summary/attribution`,
 * audit wow-ai-3). Mirrors backend/schemas/home_attribution.py, name for name
 * (the W5b wire contract): every count is a whole-book (addressable) funnel
 * snapshot count; the rate and rule facts COINCIDED with the change.
 */

export type HomeAttributionMeasure = 'refi_economics_screen' | 'high_opportunity' | 'offers_recommended' | 'listed_for_sale';

export interface HomeAttributionState {
  /** 2-char state code. */
  state: string;
  baseline_count: number | null;
  current_count: number | null;
  /** current - baseline; null where the state has no row on one of the two dates. */
  change: number | null;
}

export interface HomeAttributionRate {
  series_id: string;
  /** Week-starting Monday (YYYY-MM-DD) of the print at the baseline. */
  baseline_week: string | null;
  baseline_pct: number | null;
  latest_week: string | null;
  latest_pct: number | null;
}

export interface HomeSummaryAttributionResponse {
  measure: HomeAttributionMeasure;
  label: string;
  population: 'addressable';
  requested_baseline_date: string;
  baseline_snapshot_date: string | null;
  current_snapshot_date: string | null;
  /** True when the requested date had no snapshot and the nearest one is used. */
  nearest_snapshot: boolean;
  baseline_total: number | null;
  current_total: number | null;
  total_change: number | null;
  /** Largest change first, then by state. */
  states: HomeAttributionState[];
  /** The total change no state row accounts for. */
  unattributed_change: number | null;
  rate: HomeAttributionRate;
  offer_rules_last_updated: string | null;
  offer_rules_changed_since_baseline: boolean | null;
  sources: string[];
  note: string;
}
