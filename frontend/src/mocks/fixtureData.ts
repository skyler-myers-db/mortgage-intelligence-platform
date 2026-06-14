import type { Borrower360, PortfolioPreview, SegmentSummary } from '../types';
import type { DrawerSource } from '../components/AppContext';

export const mockPortfolio: PortfolioPreview = {
  marketable_population: 89553,
  high_intent_leads: 12840,
  top_tier_opportunities: 4120,
  offers_recommended: 6250,
  avg_score: 81,
  approved_count: 432,
  in_outreach_count: 318,
  data_refreshed_at: '2026-04-23T12:00:00Z',
};

export const mockSegments: SegmentSummary[] = [
  { code: 'itm', name: 'In the Money', count: 12840, delta: '+18%', avg_score: 82, description: 'Lien rate ≥ 75 bps above par and equity ≥ 15%.', color: 'var(--seg-itm)' },
  { code: 'listed', name: 'Listed for Sale', count: 1840, delta: '+9%', avg_score: 77, description: 'Current active or under-contract Cotality MLS listing.', color: 'var(--seg-listed)' },
  { code: 'permit', name: 'HELOC Intent', count: 2405, delta: '+11%', avg_score: 80, description: 'Cotality HELOC propensity score indicates equity-credit demand.', color: 'var(--seg-permit)' },
  { code: 'investor', name: 'Investor / Multi-Property', count: 1892, delta: '+6%', avg_score: 79, description: 'Owner Link shows 2+ properties or repeat behavior.', color: 'var(--seg-investor)' },
  { code: 'equity', name: 'Home Equity Candidate', count: 6320, delta: '+14%', avg_score: 76, description: 'Strong equity and prior cash-out/HELOC propensity.', color: 'var(--seg-equity)' },
  { code: 'retention', name: 'Retention Risk', count: 3471, delta: '+4%', avg_score: 88, description: 'Current customer with rate spread above the retention threshold; listing and competitor overlays join only when live evidence exists.', color: 'var(--seg-retention)' }
];

// icon keyed by segment code — matches the prototype's SEGMENTS icon column
export const SEGMENT_ICONS: Record<string, string> = {
  itm: 'money',
  listed: 'tag',
  permit: 'permit',
  investor: 'investor',
  equity: 'equity',
  retention: 'shield',
};

const evidence = [
  { evidence_id: 'ev-001', source_product: 'Voluntary Lien', source_table: 'cotality.liens.voluntary_lien', signal_type: 'rate_spread', signal_value: '+88 bps', display_text: 'Current lien rate is 87.5 bps above par.', confidence: 0.92, timestamp: '2026-04-20T06:12:00Z' },
  { evidence_id: 'ev-002', source_product: 'AVM', source_table: 'cotality.avm.current', signal_type: 'equity', signal_value: '$285K', display_text: 'Estimated equity is above HELOC threshold.', confidence: 0.88, timestamp: '2026-04-20T06:12:00Z' },
  { evidence_id: 'ev-003', source_product: 'Mortgage Market Analytics', source_table: 'cotality.mma.refi_activity', signal_type: 'market_trend', signal_value: '+28% QoQ', display_text: 'Local refi activity is up 28% quarter over quarter.', confidence: 0.84, timestamp: '2026-04-20T06:12:00Z' }
];

// Slice 9 re-anchored the sample trio to Chicago/IL so this Storybook /
// frontend-only fixture agrees with tests/fixtures/mock_population.py
// (the Python fixture set) and docs/data-contract-module0.md §10. This
// file is test-only per CLAUDE.md; production routes never import it.
export const mockBorrowers: Borrower360[] = [
  {
    borrower_id: 'B-48291', clip: 'clip_demo_48291', display_name: 'Owner 48291', city: 'Chicago', state: 'IL', zip: '60611',
    segment_codes: ['itm', 'equity'], equity_estimate: 285000, rate_spread_bps: 88, opportunity_score: 94, confidence: 88,
    recommended_offer: 'Refinance + HELOC', why_now: 'Lien matures in 4 months, strong equity, and local refi activity is rising.', evidence_ids: ['ev-001', 'ev-002', 'ev-003'], approval_status: 'pending',
    clip_id: 'clip_demo_48291', owner_link_id: 'ol_demo_48291', subject_property: 'Synthetic property · Chicago, IL 60611', avm_value: 625000, current_lien_balance: 340000, current_rate: 5.75, ltv: 54, related_property_count: 1,
    trigger_timeline: evidence, evidence_events: evidence,
    why_panel: { rate_spread_bps: 88, market_rate: 0.04875, equity_pct: 46, in_the_money: true, in_the_money_reason: '+88 bps spread (>= 75) AND 46% equity (>= 15%)', min_spread_bps: 75, min_equity_pct: 15, sources: ['mip.gold.fn_rate_spread', 'mip.gold.fn_in_the_money'] }
  },
  {
    borrower_id: 'B-48294', clip: 'clip_demo_48294', display_name: 'Owner 48294', city: 'Chicago', state: 'IL', zip: '60647',
    segment_codes: ['permit', 'equity'], equity_estimate: 218000, rate_spread_bps: 188, opportunity_score: 87, confidence: 82,
    recommended_offer: 'HELOC', why_now: 'Cotality HELOC propensity and strong equity position indicate equity-credit demand.', evidence_ids: ['ev-002'], approval_status: 'pending',
    clip_id: 'clip_demo_48294', owner_link_id: 'ol_demo_48294', subject_property: 'Synthetic property · Chicago, IL 60647', avm_value: 560000, current_lien_balance: 342000, current_rate: 6.75, ltv: 61, related_property_count: 1,
    has_heloc_propensity_trigger: true, heloc_propensity_score: 812, heloc_propensity_run_date: '2026-06-09',
    trigger_timeline: evidence.slice(1), evidence_events: evidence.slice(1),
    why_panel: { rate_spread_bps: 188, market_rate: 0.04875, equity_pct: 39, in_the_money: true, in_the_money_reason: '+188 bps spread (>= 75) AND 39% equity (>= 15%)', min_spread_bps: 75, min_equity_pct: 15, sources: ['mip.gold.fn_rate_spread', 'mip.gold.fn_in_the_money'] }
  },
  {
    borrower_id: 'B-48295', clip: 'clip_demo_48295', display_name: 'Owner 48295', city: 'Chicago', state: 'IL', zip: '60613',
    segment_codes: ['listed', 'retention'], equity_estimate: 405000, rate_spread_bps: 162, opportunity_score: 82, confidence: 79,
    recommended_offer: 'Purchase Mortgage', why_now: 'Listed-for-sale trigger suggests a purchase mortgage opportunity.', evidence_ids: ['ev-003'], approval_status: 'pending',
    clip_id: 'clip_demo_48295', owner_link_id: 'ol_demo_48295', subject_property: 'Synthetic property · Chicago, IL 60613', avm_value: 725000, current_lien_balance: 320000, current_rate: 6.50, ltv: 44, related_property_count: 1,
    listed_for_sale: true, listing_status_category: 'A', listing_status_description: 'Active', listing_price: 725000, listing_days_on_market: 18,
    trigger_timeline: evidence.slice(2), evidence_events: evidence.slice(2),
    why_panel: { rate_spread_bps: 162, market_rate: 0.04875, equity_pct: 56, in_the_money: true, in_the_money_reason: '+162 bps spread (>= 75) AND 56% equity (>= 15%)', min_spread_bps: 75, min_equity_pct: 15, sources: ['mip.gold.fn_rate_spread', 'mip.gold.fn_in_the_money'] }
  }
];

/** Trigger timeline events used by Borrower-360 dossier + table expand panel. */
export const triggerTimeline = [
  { when: '2d ago',  what: 'Voluntary lien rate ≥ 75 bps above par',    why: 'Current lien 7.125% vs. par 6.250% on AVM-backed value $712k',  source: 'Mortgage Market Analytics' },
  { when: '11d ago', what: 'Equity threshold crossed (≥ 15%)',           why: 'AVM refresh brought LTV to 52%',                                source: 'CLIP-AVM v2026.03' },
  { when: '34d ago', what: 'HELOC propensity score crossed threshold', why: 'Signal strongly correlates with equity-credit demand',          source: 'Cotality HELOC Propensity' },
  { when: '90d ago', what: 'Owner Link match: second property identified', why: 'Investor / multi-property pattern',                        source: 'Owner Link' },
];

/** Evidence / lineage sources used by the DataSourceDrawer. Keyed by short id. */
export const DRAWER_SOURCES: Record<string, DrawerSource> = {
  population: {
    title: 'Marketable population',
    short: 'cotality.public_records',
    description: 'Joins Cotality Public Records (Deed & Mortgage), Voluntary Lien, and Owner Link under Entrada semantic models; filtered by lender configuration.',
    lineage: [
      { layer: 'SOURCE',   name: 'cotality.public_records.deed_and_mortgage', meta: 'Delta Share · 142M rows' },
      { layer: 'SOURCE',   name: 'cotality.liens.voluntary_lien', meta: 'Delta Share · 98M rows' },
      { layer: 'ENTITY',   name: 'entity.property_clip', meta: 'Mastered via CLIP' },
      { layer: 'ENTITY',   name: 'entity.owner_link', meta: 'Mastered via Owner Link' },
      { layer: 'SEMANTIC', name: 'metrics.borrower_universe', meta: 'UC metric view' },
    ],
    signals: [
      { label: 'Owner-occupied SFR', source: 'property_clip.occupancy', value: '1.84M' },
      { label: 'Open first lien',    source: 'voluntary_lien.status',   value: '1.72M' },
      { label: 'After lender filter',source: 'filter.lender_config',    value: '89,553' },
    ],
    updatedAt: '2026-04-20 06:12 UTC',
  },
  itm: {
    title: 'In-the-Money logic',
    short: 'rules.itm_v3',
    description: 'Lien rate ≥ (par refi rate + 75 bps) AND equity ≥ 15% on latest AVM. Rule set is version-controlled in Unity Catalog.',
    lineage: [
      { layer: 'SOURCE',   name: 'cotality.mma.origination_refi', meta: 'Par rate feed (daily)' },
      { layer: 'SOURCE',   name: 'cotality.avm.current',          meta: 'Property value (monthly)' },
      { layer: 'SOURCE',   name: 'cotality.liens.voluntary_lien', meta: 'Current lien rate' },
      { layer: 'RULESET',  name: 'rules.itm_v3',                  meta: 'reviewed 2026-03-15' },
      { layer: 'SEMANTIC', name: 'metrics.itm_flag',              meta: 'UC metric view' },
    ],
    signals: [
      { label: 'Par refi rate (30y conf.)', source: 'mma.origination_refi', value: '6.250%' },
      { label: 'Example lien rate',         source: 'voluntary_lien',       value: '7.125%' },
      { label: 'Rate spread',               source: 'derived',              value: '+87.5 bps' },
      { label: 'Equity %',                  source: 'avm + lien balance',   value: '56%' },
    ],
    updatedAt: '2026-04-20 06:12 UTC',
  },
  nbo: {
    title: 'Next-Best-Offer model',
    short: 'mlflow · mtg_nbo_v3',
    description: 'MLflow model mtg_nbo_v3 — gradient-boosted tree; output = product ∈ {refi, heloc, cashout, purchase, retention} with calibrated propensity.',
    lineage: [
      { layer: 'FEATURES',   name: 'features.borrower_360',        meta: 'Owner Link + property + lien history' },
      { layer: 'MODEL',      name: 'mlflow.mtg_nbo_v3',            meta: 'AUROC 0.81 · brier 0.09' },
      { layer: 'GOVERNANCE', name: 'compliance.nbo_review_board',  meta: 'Approved 2026-03-02' },
    ],
    signals: [
      { label: 'Top feature', source: 'SHAP', value: 'rate_spread_bps' },
      { label: '#2 feature',  source: 'SHAP', value: 'avm_equity_pct' },
      { label: '#3 feature',  source: 'SHAP', value: 'prior_heloc_flag' },
    ],
    updatedAt: '2026-04-20 06:12 UTC',
  },
  permit: {
    title: 'Building permit signal',
    short: 'Building Permits - pending',
    description: 'True filed building-permit rows remain pending. HELOC intent is represented separately by Cotality HELOC propensity.',
    lineage: [
      { layer: 'SOURCE',   name: 'cotality.permits.building', meta: 'Delta Share · pending' },
      { layer: 'JOIN',     name: 'join.permit_to_clip',       meta: 'pending true permit source' },
      { layer: 'GOLD',     name: 'borrower_360.has_permit',   meta: 'filed permit only' },
    ],
    signals: [
      { label: 'Readiness',     source: 'admin.sources',    value: 'roadmap' },
      { label: 'has_permit',    source: 'borrower_360',     value: 'filed permit only' },
      { label: 'Permit rows',   source: 'permits.building', value: 'pending share' },
    ],
    updatedAt: '2026-04-20 06:12 UTC',
  },
  config: {
    title: 'Campaign assumptions',
    short: 'config',
    description: 'Marketing ROI config, set per lender.',
    lineage: [{ layer: 'CONFIG', name: 'lender.campaign_config' }],
    signals: [],
  },
};
