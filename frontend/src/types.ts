export type SegmentCode = 'itm' | 'listed' | 'permit' | 'investor' | 'equity' | 'retention';
export type OfferType = 'refi' | 'heloc' | 'cash_out' | 'purchase' | 'retention' | 'recapture';
export type ApprovalStatus = 'pending' | 'approved' | 'rejected' | 'hold';

export interface EvidenceEvent {
  evidence_id: string;
  source_product: string;
  source_table: string;
  signal_type: string;
  signal_value: string;
  display_text: string;
  confidence: number;
  timestamp: string;
}

export interface SegmentSummary {
  code: SegmentCode;
  name: string;
  count: number;
  delta: string;
  avg_score: number;
  description: string;
  color: string;
}

export interface LeadSummary {
  borrower_id: string;
  display_name: string;
  city: string;
  state: string;
  zip: string;
  /** Real Cotality CLIP (2026-04-22 contract addition). Present on the
   *  lead-queue row and matches Borrower360.clip_id exactly. Empty string
   *  if the upstream gold row predates this projection; callers should
   *  prefer this field over deriving a fake CLIP from borrower_id. */
  clip: string;
  segment_codes: SegmentCode[];
  equity_estimate: number;
  rate_spread_bps: number;
  opportunity_score: number;
  confidence: number;
  recommended_offer: string;
  why_now: string;
  evidence_ids: string[];
  approval_status: ApprovalStatus;
  /** Secondary-filter fields (2026-04-23). Carried from gold.borrower_360
   *  through gold.lead_population so /segment-intelligence can run real
   *  client-side predicates against occupancy, owner-link, lien state,
   *  and purchase intent. All optional with safe defaults so older cached
   *  payloads still parse. `has_permit` / `listed_for_sale` are BLOCKED
   *  FALSE in gold until Cotality Building Permits + MLS Delta shares
   *  land — the UI surfaces a "data-dependency pending" note. */
  is_owner_occupied?: boolean;
  is_investor?: boolean;
  related_property_count?: number;
  current_lien_balance?: number;
  second_pos_amount?: number;
  has_permit?: boolean;
  listed_for_sale?: boolean;
}

/** Business-friendly label for a UC source (2026-04-22). `name` is the
 *  raw UC object name (drives drawer lineage); `display_label` is the
 *  human-readable chip text (e.g. "In-the-money rule"). */
export interface SourceLabel {
  name: string;
  display_label: string;
}

export interface WhyPanel {
  rate_spread_bps: number;
  market_rate: number;
  equity_pct: number;
  in_the_money: boolean;
  in_the_money_reason: string;
  min_spread_bps: number;
  min_equity_pct: number;
  sources: string[];
  /** Index-aligned with `sources`. Added 2026-04-22. */
  source_labels?: SourceLabel[];
}

export interface Borrower360 extends LeadSummary {
  clip_id: string;
  owner_link_id: string;
  subject_property: string;
  avm_value: number;
  current_lien_balance: number;
  current_rate: number;
  ltv: number;
  related_property_count: number;
  trigger_timeline: EvidenceEvent[];
  evidence_events: EvidenceEvent[];
  why_panel: WhyPanel;
}

export interface KpiTrend {
  series: number[];
  delta_pct: number | null;
  direction: 'up' | 'down' | 'flat';
}

export interface PortfolioPreview {
  marketable_population: number;
  high_intent_leads: number;
  top_tier_opportunities: number | null;
  offers_recommended: number | null;
  avg_score: number;
  data_refreshed_at: string | null; // ISO timestamp
  trends?: Record<string, KpiTrend>;
  // R5-20: server-authoritative day-zero flag. Optional so older servers
  // that don't emit it still parse; consumers must fall back to
  // `marketable_population === 0 && data_refreshed_at === null` when
  // absent.
  day_zero?: boolean;
  approved_count: number | null;
  in_outreach_count: number | null;
}

export interface OfferAlternative {
  offer_code: string;
  product_label: string;
  reason_not_chosen: string;
}

export interface OfferRecommendation {
  borrower_id: string;
  offer_code: string;
  offer_type: string;
  product_label: string;
  confidence: number;
  rationale: string;
  evidence_ids: string[];
  sources: string[];
  /** Index-aligned with `sources`. Added 2026-04-22 so chip text renders
   *  business-friendly labels (e.g. "In-the-money rule") instead of raw
   *  UC object names. Optional for back-compat with cached responses. */
  source_labels?: SourceLabel[];
  alternatives: OfferAlternative[];
  thresholds_applied: Record<string, number>;
}

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
  metric_value?: string | null;
  table_rows?: Record<string, unknown>[] | null;
  follow_up_questions?: string[];
}

/** Per-state aggregate row from `/api/geo/state-rollups` (see
 *  backend/schemas/geo.py). `state` is the uppercase USPS code. Consumed
 *  by the USChoroplethMap state level. `top_segment_code` was added in
 *  slice13-accuracy-validation so the map can drop the hardcoded
 *  STATE_FACTS[*].topSegment literal. */
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

/** Per-county aggregate row from `/api/geo/county-rollups?state=XX`. */
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
  /** Optional note from the backend explaining the geographic data scope
   *  (e.g. "Cotality evaluation share: 1 anchor county per state"). When
   *  present, the UI renders it verbatim as a scope chip. Added 2026-04-23
   *  to surface the Cotality eval-share single-county scope honestly. */
  scope_note?: string | null;
}

/** Per-ZIP aggregate row from `/api/geo/zip-rollups?fips=NNNNN`.
 *  `sample_borrower_id` is the stable-ranked top borrower for deep-link. */
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
