import type { EvidenceEvent, LeadSummary } from '../types';
import type { ProofMargin } from './proofMargins';

export interface ProofEvidenceEvent {
  evidence_id: string;
  source_product: string;
  signal_type: string;
  signal_value: string;
  display_text: string;
  confidence: number;
  timestamp: string;
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
  source_refreshed_at?: string | null;
  clip_id: string;
  owner_link_id: string;
  subject_property: string;
  avm_value: number;
  current_lien_balance: number;
  current_lien_balance_low?: number;
  current_lien_balance_high?: number;
  current_rate: number;
  ltv: number | null; // wire is `int | None`: withheld when ltv_basis_is_unreliable
  ltv_basis_is_unreliable?: boolean;
  related_property_count: number;
  situs_cbsa_code?: string | null;
  first_pos_loan_type?: string | null;
  is_absentee?: boolean;
  is_corporate_owner?: boolean;
  has_first_party_relationship?: boolean;
  first_party_relationship_depth?: number;
  first_party_recent_interactions?: number;
  first_party_recent_application?: boolean;
  first_party_synthetic_demo?: boolean;
  trigger_timeline: EvidenceEvent[];
  evidence_events: EvidenceEvent[];
  why_panel: WhyPanel;
}

export interface ProofFormulaLine {
  label: string;
  expression: string;
  result: string;
  source?: string | null;
}

export type ProofScoreComponentKey =
  | 'economic_incentive'
  | 'intent_trigger'
  | 'fit'
  | 'relationship'
  | 'evidence';

export interface ProofScoreComponent {
  key: ProofScoreComponentKey;
  label: string;
  value: number;
  weight: number;
  weighted_points: number;
  explanation: string;
  source_fields: string[];
  fair_lending_note?: string | null;
}

export interface ProofOfferBranch {
  code: string;
  label: string;
  passed: boolean;
  selected: boolean;
  reason: string;
}

export interface ProofReproduceQuery {
  title: string;
  sql: string;
  sql_hash: string;
  note: string;
  databricks_sql_url?: string | null;
}

export interface BorrowerProof {
  borrower_id: string;
  trusted: boolean;
  known_data_gaps: string[];
  generated_from: string;
  source_refresh_at?: string | null;
  opportunity_score: number;
  signal_strength: number;
  signal_strength_note: string;
  evidence_confidence_note: string;
  score_components: ProofScoreComponent[];
  score_formula: ProofFormulaLine;
  signal_strength_formula: ProofFormulaLine;
  rate_spread_formula: ProofFormulaLine;
  equity_formula: ProofFormulaLine;
  ltv_formula: ProofFormulaLine;
  offer_code: string;
  offer_label: string;
  offer_branches: ProofOfferBranch[];
  evidence_rows: ProofEvidenceEvent[];
  source_assets: string[];
  reproduce: ProofReproduceQuery[];
  margins?: ProofMargin[];
}
