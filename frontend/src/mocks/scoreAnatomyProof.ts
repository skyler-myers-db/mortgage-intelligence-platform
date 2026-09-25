/**
 * Test-only proof payloads for the Score anatomy unit tests. src/mocks is
 * the test-fixture home the eslint import ban keeps out of production code
 * (CLAUDE.md: no mock fallback). Shaped like the backend's BorrowerProof:
 * real weights, weighted points and the five margins.
 */
import type { BorrowerProof, ProofMargin, ProofScoreComponent } from '../types';

const COMPONENTS: ProofScoreComponent[] = [
  { key: 'economic_incentive', label: 'Economic incentive', value: 98, weight: 0.35, weighted_points: 34.3, explanation: 'Rate spread and equity.', source_fields: ['rate_spread_bps'] },
  { key: 'intent_trigger', label: 'Intent trigger', value: 95, weight: 0.3, weighted_points: 28.5, explanation: 'Signals.', source_fields: ['has_permit'] },
  { key: 'fit', label: 'Product fit', value: 70, weight: 0.15, weighted_points: 10.5, explanation: 'Fit.', source_fields: ['is_owner_occupied'] },
  { key: 'relationship', label: 'Relationship', value: 80, weight: 0.1, weighted_points: 8, explanation: 'Relationship.', source_fields: ['is_current_customer'] },
  { key: 'evidence', label: 'Evidence coverage', value: 90, weight: 0.1, weighted_points: 9, explanation: 'Evidence.', source_fields: ['evidence_ids'] },
];

export const SAMPLE_MARGINS: ProofMargin[] = [
  { key: 'spread_screen', label: 'Refi screen', value_text: 'Clears the refi screen by 13 bps', threshold: 'spread 88 bps · screen 75 bps', direction: 'clears', source: 'mip.gold.fn_in_the_money' },
  { key: 'par_break_even', label: 'Par break-even', value_text: 'Drops below the refi screen if 30-year par rises 13 bps to 6.39%', threshold: 'par 6.26%', direction: 'flips', source: 'mip.gold.fn_rate_spread' },
  { key: 'equity_floor', label: 'Equity threshold', value_text: 'Clears the home-equity threshold by 11 pts', threshold: 'equity 46% · threshold 35%', direction: 'clears', source: 'mip.gold.fn_next_best_offer' },
  { key: 'offer_flip_equity', label: 'Offer change by equity', value_text: 'Becomes Refinance review at 34% equity', threshold: 'equity 46%', direction: 'flips', source: 'mip.gold.fn_next_best_offer' },
  { key: 'offer_flip_par', label: 'Offer change by par', value_text: 'Becomes Cash-out refinance review if par rises 13 bps to 6.39%', threshold: 'par 6.26%', direction: 'flips', source: 'mip.gold.fn_next_best_offer' },
];

export function sampleProof(overrides: Partial<BorrowerProof> = {}): BorrowerProof {
  const formula = { label: 'Opportunity score', expression: '0.35*98 + …', result: '90', source: 'mip.gold.fn_lead_score' };
  return {
    borrower_id: 'B-TEST000000001',
    trusted: true,
    known_data_gaps: [],
    generated_from: 'mip.gold.borrower_dossier + mip.gold.lead_scores',
    source_refresh_at: 'dossier 2026-07-14T06:12:00Z / lead_scores 2026-07-14T06:12:00Z',
    opportunity_score: 90,
    signal_strength: 87,
    signal_strength_note: 'Deterministic average of the five sub-scores.',
    evidence_confidence_note: 'Row-level source confidence.',
    score_components: COMPONENTS,
    score_formula: formula,
    signal_strength_formula: { ...formula, label: 'Signal strength' },
    rate_spread_formula: { ...formula, label: 'Rate spread' },
    equity_formula: { ...formula, label: 'Equity' },
    ltv_formula: { ...formula, label: 'LTV' },
    offer_code: 'refi_plus_heloc',
    offer_label: 'Refinance + home-equity review',
    offer_branches: [],
    evidence_rows: [],
    source_assets: ['mip.gold.borrower_dossier'],
    reproduce: [],
    margins: SAMPLE_MARGINS,
    ...overrides,
  };
}
