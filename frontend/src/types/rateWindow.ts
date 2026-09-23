/**
 * Rate-window contracts (`GET /api/v1/analytics/rate-window`).
 *
 * Mirrors backend/schemas/analytics_rate_window.py. The "why now" series: the
 * weekly FRED MORTGAGE30US print against the CURRENT fixed-rate book's
 * note-rate band, plus the count of liens in the money at each week's rate.
 * Every row is read from `mip.gold.rate_window_weekly`, precomputed by the
 * gold refresh job with the canonical fn_rate_spread / fn_in_the_money
 * primitives; nothing is computed per request.
 *
 * Rates are in PERCENT form (6.30 == 6.30%). The book distribution is as-of
 * the refresh anchor (`book_as_of`) and applied to every week -- it is
 * today's book against the historical rate, not a portfolio history.
 */

export interface RateWindowWeek {
  /** Week-starting Monday, ISO date (YYYY-MM-DD). */
  week: string;
  /** FRED 30-year fixed rate for the week, in percent. */
  market_rate_pct: number;
  /** Median note rate of the current fixed-rate book, in percent; null when the book is empty. */
  book_median_pct?: number | null;
  book_p25_pct?: number | null;
  book_p75_pct?: number | null;
  /** Liens in the money at this week's market rate under the governed thresholds. */
  itm_count: number;
  /** True on the most recent week (the current market print). */
  is_latest?: boolean;
}

export interface RateWindowThresholds {
  /** Both null only when the fixed-rate book is empty. */
  min_spread_bps?: number | null;
  min_equity_pct?: number | null;
}

export interface RateWindowProvenance {
  market_rate_source: string;
  book_source: string;
  gold_source: string;
  rule_source: string;
  book_as_of?: string | null;
  refreshed_at?: string | null;
  note: string;
}

export interface RateWindowResponse {
  series_id: string;
  weeks: RateWindowWeek[];
  /** Active fixed-rate first liens in the current book (the same on every week). */
  book_lien_count: number;
  book_as_of?: string | null;
  thresholds: RateWindowThresholds;
  provenance: RateWindowProvenance;
}
