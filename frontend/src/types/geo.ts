export interface ConfigOptions {
  lender_name: string;
  rum_enabled?: boolean;
  geographies: string[];
  geographies_status?: string;
  geography_scope?: {
    state_count: number;
    county_count: number;
    zip_count?: number | null;
    snapshot_date?: string | null;
    source_table?: string | null;
    scope_label: string;
    counties: Array<{
      state: string;
      fips_5: string;
      county_name?: string | null;
      addressable_borrowers: number;
    }>;
  } | null;
  occupancy: string[];
  lien_status: string[];
  lender_relationships: string[];
  products: string[];
  equity_thresholds: string[];
  target_lender_refs?: string[];
  target_lender_refs_status?: string;
}

export interface StateRollup {
  state: string;
  addressable: number;
  /** Contact-eligible subset of `addressable` — what the Lead Queue this
   *  tile links to actually shows. Live 2026-08-11 IL is 76,711 of
   *  1,851,040, so a tile that states only the larger number sends the
   *  reader to a queue 24x smaller. Undefined/null means "not reported"
   *  (an older payload), never "nobody is contactable". */
  contactable?: number | null;
  in_the_money: number;
  top_tier_opportunities: number;
  avg_score: number;
  /** How many of `addressable` the ZIP drill will NOT show, because the
   *  share carries no usable 5-digit ZIP for them. The state tile and the
   *  sum of its ZIP tiles disagree by exactly this much, so the UI has to
   *  say so rather than let a reader find the gap themselves. */
  zip_unassigned_count?: number | null;
  top_segment_code?: string | null;
}

export interface StateRollupResponse {
  rollups: StateRollup[];
  snapshot_date?: string | null;
}

export interface CountyRollup {
  fips_5: string;
  state: string;
  county_name?: string | null;
  addressable_borrowers: number;
  in_the_money_borrowers: number;
  high_opportunity_borrowers: number;
  avg_opportunity_score: number;
  top_segment_code?: string | null;
}

export interface CountyRollupResponse {
  state: string;
  rollups: CountyRollup[];
  snapshot_date?: string | null;
  scope_note?: string | null;
}

export interface ZipRollup {
  zip: string;
  state: string;
  county_fips_5?: string | null;
  addressable_borrowers: number;
  avg_opportunity_score: number;
  top_segment_code?: string | null;
  sample_borrower_id?: string | null;
}

export interface ZipRollupResponse {
  /** Echoes whichever key answered — exactly one is populated. */
  fips_5?: string | null;
  state?: string | null;
  rollups: ZipRollup[];
  snapshot_date?: string | null;
}
