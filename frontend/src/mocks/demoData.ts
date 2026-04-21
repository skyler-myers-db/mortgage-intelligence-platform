import type { Borrower360, PortfolioPreview, SegmentSummary } from '../types';

export const mockPortfolio: PortfolioPreview = {
  marketable_population: 89553,
  high_intent_leads: 12840,
  avg_score: 81,
  projected_contact_to_app: 9.7,
  cost_per_contact: 2.18
};

export const mockSegments: SegmentSummary[] = [
  { code: 'itm', name: 'In the Money', count: 12840, delta: '+18%', avg_score: 82, description: 'Lien rate ≥ 75 bps above par and equity ≥ 15%.', color: '#5CE1E6' },
  { code: 'listed', name: 'Listed for Sale', count: 2614, delta: '+9%', avg_score: 74, description: 'Active listing, likely purchase mortgage opportunity.', color: '#F59E0B' },
  { code: 'permit', name: 'Permit Activity', count: 4108, delta: '+11%', avg_score: 71, description: 'Recent high-value permits indicate HELOC/cash-out demand.', color: '#A78BFA' },
  { code: 'investor', name: 'Investor / Multi-Property', count: 1892, delta: '+6%', avg_score: 79, description: 'Owner Link shows 2+ properties or repeat behavior.', color: '#F472B6' },
  { code: 'equity', name: 'Home Equity Candidate', count: 6320, delta: '+14%', avg_score: 76, description: 'Strong equity and prior cash-out/HELOC propensity.', color: '#66C5FF' },
  { code: 'retention', name: 'Retention Risk', count: 3471, delta: '+4%', avg_score: 88, description: 'Current customer showing refi/listing/competitor signals.', color: '#34D399' }
];

const evidence = [
  { evidence_id: 'ev-001', source_product: 'Voluntary Lien', source_table: 'cotality.liens.voluntary_lien', signal_type: 'rate_spread', signal_value: '+87.5 bps', display_text: 'Current lien rate is 87.5 bps above par.', confidence: 0.92, timestamp: '2026-04-20T06:12:00Z' },
  { evidence_id: 'ev-002', source_product: 'AVM', source_table: 'cotality.avm.current', signal_type: 'equity', signal_value: '$285K', display_text: 'Estimated equity is above HELOC threshold.', confidence: 0.88, timestamp: '2026-04-20T06:12:00Z' },
  { evidence_id: 'ev-003', source_product: 'Mortgage Market Analytics', source_table: 'cotality.mma.refi_activity', signal_type: 'market_trend', signal_value: '+28% QoQ', display_text: 'Local refi activity is up 28% quarter over quarter.', confidence: 0.84, timestamp: '2026-04-20T06:12:00Z' }
];

export const mockBorrowers: Borrower360[] = [
  {
    borrower_id: 'B-48291', display_name: 'James & Maria Rodriguez', city: 'Atlanta', state: 'GA', zip: '30309',
    segment_codes: ['itm', 'equity'], equity_estimate: 285000, rate_spread_bps: 875, opportunity_score: 94, confidence: 88,
    recommended_offer: 'Refinance + HELOC', why_now: 'Lien matures in 4 months, strong equity, and local refi activity is rising.', evidence_ids: ['ev-001', 'ev-002', 'ev-003'], approval_status: 'pending',
    clip_id: 'clip_demo_48291', owner_link_id: 'ol_demo_48291', subject_property: 'Synthetic property · Atlanta, GA 30309', avm_value: 625000, current_lien_balance: 340000, current_rate: 5.75, ltv: 54, related_property_count: 1,
    trigger_timeline: evidence, evidence_events: evidence
  },
  {
    borrower_id: 'B-48294', display_name: 'David Park', city: 'Atlanta', state: 'GA', zip: '30305',
    segment_codes: ['permit', 'equity'], equity_estimate: 218000, rate_spread_bps: 525, opportunity_score: 87, confidence: 82,
    recommended_offer: 'HELOC', why_now: 'Recent high-value permit and strong equity position indicate renovation financing need.', evidence_ids: ['ev-002'], approval_status: 'pending',
    clip_id: 'clip_demo_48294', owner_link_id: 'ol_demo_48294', subject_property: 'Synthetic property · Atlanta, GA 30305', avm_value: 560000, current_lien_balance: 342000, current_rate: 6.75, ltv: 61, related_property_count: 1,
    trigger_timeline: evidence.slice(1), evidence_events: evidence.slice(1)
  },
  {
    borrower_id: 'B-48295', display_name: 'Lisa Thompson', city: 'Atlanta', state: 'GA', zip: '30324',
    segment_codes: ['listed', 'retention'], equity_estimate: 405000, rate_spread_bps: 250, opportunity_score: 82, confidence: 79,
    recommended_offer: 'Purchase Mortgage', why_now: 'Listed-for-sale trigger suggests a purchase mortgage opportunity.', evidence_ids: ['ev-003'], approval_status: 'pending',
    clip_id: 'clip_demo_48295', owner_link_id: 'ol_demo_48295', subject_property: 'Synthetic property · Atlanta, GA 30324', avm_value: 725000, current_lien_balance: 320000, current_rate: 6.50, ltv: 44, related_property_count: 1,
    trigger_timeline: evidence.slice(2), evidence_events: evidence.slice(2)
  }
];
