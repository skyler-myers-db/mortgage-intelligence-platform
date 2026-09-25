/**
 * Rate Lever contracts (`GET /api/v1/geo/rate-sensitivity`, audit wow-stage-1).
 *
 * Mirrors backend/schemas/geo_rate_sensitivity.py. Per state and per step of
 * the par-rate grid (-100 .. +100 bps), the addressable borrowers that would
 * clear this refresh's refi screen if the 30-year par rate moved by that step:
 * read from `mip.gold.rate_sensitivity_rollup`, which the gold refresh builds
 * by re-running fn_rate_spread / fn_in_the_money at par + step. A scenario,
 * not a forecast.
 *
 * Every per-state list is ALIGNED to `steps_bps`. Rates are in PERCENT form
 * and come from the server: the client does no rate arithmetic. Imported
 * directly (not through src/types.ts, which another lane owns this wave).
 */

export interface RateSensitivityThresholds {
  min_spread_bps?: number | null;
  min_equity_pct?: number | null;
}

export interface RateSensitivityState {
  /** 2-char USPS state code (uppercase). */
  state: string;
  /** Addressable borrowers in the state (the same at every step). */
  addressable: number;
  /** Borrowers with an active, in-bounds note rate: the only ones a scenario can move. */
  rate_movable: number;
  /** In the money at each step, aligned to `steps_bps`. */
  in_the_money: number[];
  /** The contact-eligible subset at each step (live); null when not reported. */
  contactable_in_the_money?: number[] | null;
}

export interface RateSensitivityProvenance {
  gold_source: string;
  book_source: string;
  rule_source: string;
  contactable_source: string;
  book_as_of?: string | null;
  refreshed_at?: string | null;
  note: string;
}

export interface RateSensitivityResponse {
  /** False until the gold refresh has built the grid. */
  built: boolean;
  /** Par move per step in basis points (positive = par rises); empty when not built. */
  steps_bps: number[];
  /** The par rate each step describes, in percent, aligned to `steps_bps`. */
  scenario_market_rate_pct: number[];
  /** The par rate this refresh scored with, in percent (step 0). */
  base_market_rate_pct?: number | null;
  thresholds: RateSensitivityThresholds;
  states: RateSensitivityState[];
  provenance: RateSensitivityProvenance;
}
