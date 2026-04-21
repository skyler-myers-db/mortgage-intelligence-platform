import type { Borrower360, PortfolioPreview, SegmentSummary } from '../types';
import type { DrawerSource } from '../components/AppContext';

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

// Slice 9 re-anchored the demo trio to Chicago/IL so this Storybook /
// frontend-only fixture agrees with tests/fixtures/mock_population.py
// (the Python fixture set) and docs/data-contract-module0.md §10. This
// file is test-only per CLAUDE.md; production routes never import it.
export const mockBorrowers: Borrower360[] = [
  {
    borrower_id: 'B-48291', display_name: 'James & Maria Rodriguez', city: 'Chicago', state: 'IL', zip: '60611',
    segment_codes: ['itm', 'equity'], equity_estimate: 285000, rate_spread_bps: 88, opportunity_score: 94, confidence: 88,
    recommended_offer: 'Refinance + HELOC', why_now: 'Lien matures in 4 months, strong equity, and local refi activity is rising.', evidence_ids: ['ev-001', 'ev-002', 'ev-003'], approval_status: 'pending',
    clip_id: 'clip_demo_48291', owner_link_id: 'ol_demo_48291', subject_property: 'Synthetic property · Chicago, IL 60611', avm_value: 625000, current_lien_balance: 340000, current_rate: 5.75, ltv: 54, related_property_count: 1,
    trigger_timeline: evidence, evidence_events: evidence,
    why_panel: { rate_spread_bps: 88, market_rate: 0.04875, equity_pct: 46, in_the_money: true, in_the_money_reason: '+88 bps spread (>= 75) AND 46% equity (>= 15%)', min_spread_bps: 75, min_equity_pct: 15, sources: ['mip_demo.gold.fn_rate_spread', 'mip_demo.gold.fn_in_the_money'] }
  },
  {
    borrower_id: 'B-48294', display_name: 'David Park', city: 'Chicago', state: 'IL', zip: '60647',
    segment_codes: ['permit', 'equity'], equity_estimate: 218000, rate_spread_bps: 188, opportunity_score: 87, confidence: 82,
    recommended_offer: 'HELOC', why_now: 'Recent high-value permit and strong equity position indicate renovation financing need.', evidence_ids: ['ev-002'], approval_status: 'pending',
    clip_id: 'clip_demo_48294', owner_link_id: 'ol_demo_48294', subject_property: 'Synthetic property · Chicago, IL 60647', avm_value: 560000, current_lien_balance: 342000, current_rate: 6.75, ltv: 61, related_property_count: 1,
    trigger_timeline: evidence.slice(1), evidence_events: evidence.slice(1),
    why_panel: { rate_spread_bps: 188, market_rate: 0.04875, equity_pct: 39, in_the_money: true, in_the_money_reason: '+188 bps spread (>= 75) AND 39% equity (>= 15%)', min_spread_bps: 75, min_equity_pct: 15, sources: ['mip_demo.gold.fn_rate_spread', 'mip_demo.gold.fn_in_the_money'] }
  },
  {
    borrower_id: 'B-48295', display_name: 'Lisa Thompson', city: 'Chicago', state: 'IL', zip: '60613',
    segment_codes: ['listed', 'retention'], equity_estimate: 405000, rate_spread_bps: 162, opportunity_score: 82, confidence: 79,
    recommended_offer: 'Purchase Mortgage', why_now: 'Listed-for-sale trigger suggests a purchase mortgage opportunity.', evidence_ids: ['ev-003'], approval_status: 'pending',
    clip_id: 'clip_demo_48295', owner_link_id: 'ol_demo_48295', subject_property: 'Synthetic property · Chicago, IL 60613', avm_value: 725000, current_lien_balance: 320000, current_rate: 6.50, ltv: 44, related_property_count: 1,
    trigger_timeline: evidence.slice(2), evidence_events: evidence.slice(2),
    why_panel: { rate_spread_bps: 162, market_rate: 0.04875, equity_pct: 56, in_the_money: true, in_the_money_reason: '+162 bps spread (>= 75) AND 56% equity (>= 15%)', min_spread_bps: 75, min_equity_pct: 15, sources: ['mip_demo.gold.fn_rate_spread', 'mip_demo.gold.fn_in_the_money'] }
  }
];

/** Trigger timeline events used by Borrower-360 dossier + table expand panel. */
export const triggerTimeline = [
  { when: '2d ago',  what: 'Voluntary lien rate ≥ 75 bps above par',    why: 'Current lien 7.125% vs. par 6.250% on AVM-backed value $712k',  source: 'Mortgage Market Analytics' },
  { when: '11d ago', what: 'Equity threshold crossed (≥ 15%)',           why: 'AVM refresh brought LTV to 52%',                                source: 'CLIP-AVM v2026.03' },
  { when: '34d ago', what: 'Building permit filed ($48k kitchen remodel)', why: 'Signal strongly correlates with HELOC / cash-out demand',   source: 'Cotality Building Permits' },
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
    title: 'Permit signal',
    short: 'permits.building',
    description: 'Cotality Building Permits records tagged to CLIP; flag triggers on permit value ≥ $25k in last 180 days.',
    lineage: [
      { layer: 'SOURCE',   name: 'cotality.permits.building', meta: '4.8M active records' },
      { layer: 'JOIN',     name: 'join.permit_to_clip',       meta: 'via address canonicalization' },
      { layer: 'SEMANTIC', name: 'metrics.permit_signal',     meta: 'UC metric view' },
    ],
    signals: [
      { label: 'Permit type',   source: 'permits.type',     value: 'Kitchen remodel' },
      { label: 'Filed value',   source: 'permits.value',    value: '$48,000' },
      { label: 'Filed',         source: 'permits.filed_at', value: '2026-03-17' },
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

/** Fallback agent-activity events for Home when /api/audit/events is empty.
 *  TODO: wire to /api/audit/events (already exists) — this is only the demo
 *  starter feed so the Home page isn't blank before any action runs. */
export const demoAgentActivity = [
  { event_id: 'evt-start', actor: 'System',   action: 'Session started on Databricks One',       entity_type: 'session', entity_id: '—',   payload_json: {}, evidence_ids: [],                       created_at: '2026-04-20T10:24:07Z' },
  { event_id: 'evt-load',  actor: 'Pipeline', action: 'Loaded Cotality Public Records via Delta Share', entity_type: 'pipeline', entity_id: 'deed_and_mortgage', payload_json: {}, evidence_ids: [], created_at: '2026-04-20T10:24:31Z' },
  { event_id: 'evt-score', actor: 'Agent · Lead Portfolio', action: 'Scored 89,553 borrowers; 12,840 marked in-the-money', entity_type: 'scoring', entity_id: 'mip_demo.gold.lead_scores', payload_json: {}, evidence_ids: ['ev-001'], created_at: '2026-04-20T10:25:04Z' },
];
