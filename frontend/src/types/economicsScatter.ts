export interface EquitySpreadPoint {
  borrower_id: string;
  display_name: string;
  segment: string;
  state: string;
  equity_pct: number;
  rate_spread_bps: number;
  opportunity_score: number;
  score_band?: 'high' | 'med' | 'low' | null;
  in_the_money?: boolean | null;
}

export interface EquitySpreadCoordinateGroup {
  key: string;
  equity_pct: number;
  rate_spread_bps: number;
  points: EquitySpreadPoint[];
}

export interface EquitySpreadViewport {
  equity_min: number;
  equity_max: number;
  spread_min: number;
  spread_max: number;
}

export interface EquitySpreadBin {
  equity_bin_pct: number;
  spread_bin_bps: number;
  borrower_count: number;
  mean_opportunity_score: number;
  in_the_money_borrowers: number;
}

export interface EquitySpreadOverview {
  bins: EquitySpreadBin[];
  total_borrowers: number;
  equity_bin_pct: number;
  spread_bin_bps: number;
  equity_domain_min: number;
  equity_domain_max: number;
  spread_domain_min: number;
  spread_domain_max: number;
  source_table: string;
  refreshed_at?: string | null;
}

export interface EquitySpreadPointsResponse {
  points: EquitySpreadPoint[];
  total_matching: number;
  showing: number;
  point_cap: number;
  truncated: boolean;
  viewport: EquitySpreadViewport;
  source_table: string;
  refreshed_at?: string | null;
}
