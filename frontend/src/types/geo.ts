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
  in_the_money: number;
  top_tier_opportunities: number;
  avg_score: number;
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
  fips_5: string;
  rollups: ZipRollup[];
  snapshot_date?: string | null;
}
