export type SegmentCode = 'itm' | 'listed' | 'permit' | 'investor' | 'equity' | 'retention';
export type OfferType = 'refi' | 'heloc' | 'cash_out' | 'purchase' | 'retention' | 'recapture';
export type ApprovalStatus = 'pending' | 'approved' | 'rejected';

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
  segment_codes: SegmentCode[];
  equity_estimate: number;
  rate_spread_bps: number;
  opportunity_score: number;
  confidence: number;
  recommended_offer: string;
  why_now: string;
  evidence_ids: string[];
  approval_status: ApprovalStatus;
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

export interface PortfolioPreview {
  marketable_population: number;
  high_intent_leads: number;
  avg_score: number;
  projected_contact_to_app: number;
  cost_per_contact: number;
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
