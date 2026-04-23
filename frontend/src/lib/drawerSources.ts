import type { DrawerSource } from '../components/AppContext';

/**
 * DRAWER_SOURCES — UI metadata describing each evidence-drawer entry
 * (title, description, UC lineage, signals).
 *
 * This file is UI contract metadata, not fake borrower data. It tells the
 * <EvidenceDrawer> what each source *means* — what tables feed it, what
 * signals it produces, when it last refreshed. None of the values here
 * are synthetic borrower attributes; they are human-written copy about
 * the Unity Catalog objects that power Module 0.
 *
 * Consumed by routes + components that render <EvidenceChip source={…}>.
 * Safe to import from production code (per CLAUDE.md — this is NOT a
 * mock fallback).
 */
export const DRAWER_SOURCES: Record<string, DrawerSource> = {
  population: {
    title: 'Marketable population',
    short: 'cotality.public_records',
    description:
      'Deed & mortgage records joined to voluntary liens and the Owner Link graph, filtered by the lender configuration.',
    lineage: [
      { layer: 'SOURCE', name: 'cotality.public_records.deed_and_mortgage', meta: 'Delta Share · 142M rows' },
      { layer: 'SOURCE', name: 'cotality.liens.voluntary_lien', meta: 'Delta Share · 98M rows' },
      { layer: 'ENTITY', name: 'entity.property_clip', meta: 'Mastered via CLIP' },
      { layer: 'ENTITY', name: 'entity.owner_link', meta: 'Mastered via Owner Link' },
      { layer: 'SEMANTIC', name: 'metrics.borrower_universe', meta: 'UC metric view' },
    ],
    signals: [
      { label: 'Owner-occupied SFR', source: 'property_clip.occupancy', value: '1.84M' },
      { label: 'Open first lien', source: 'voluntary_lien.status', value: '1.72M' },
      { label: 'After lender filter', source: 'filter.lender_config', value: '89,553' },
    ],
    updatedAt: '2026-04-20 06:12 UTC',
  },
  itm: {
    title: 'In-the-Money logic',
    short: 'rules.itm_v3',
    description:
      'Flags a borrower when lien rate ≥ par refi rate + 75 bps and equity ≥ 15% on the latest AVM. Ruleset stored in Unity Catalog.',
    lineage: [
      { layer: 'SOURCE', name: 'cotality.mma.origination_refi', meta: 'Par rate feed (daily)' },
      { layer: 'SOURCE', name: 'cotality.avm.current', meta: 'Property value (monthly)' },
      { layer: 'SOURCE', name: 'cotality.liens.voluntary_lien', meta: 'Current lien rate' },
      { layer: 'RULESET', name: 'rules.itm_v3', meta: 'reviewed 2026-03-15' },
      { layer: 'SEMANTIC', name: 'metrics.itm_flag', meta: 'UC metric view' },
    ],
    signals: [
      { label: 'Par refi rate (30y conf.)', source: 'mma.origination_refi', value: '6.250%' },
      { label: 'Example lien rate', source: 'voluntary_lien', value: '7.125%' },
      { label: 'Rate spread', source: 'derived', value: '+87.5 bps' },
      { label: 'Equity %', source: 'avm + lien balance', value: '56%' },
    ],
    updatedAt: '2026-04-20 06:12 UTC',
  },
  nbo: {
    title: 'Next-Best-Offer model',
    short: 'mlflow · mtg_nbo_v3',
    description:
      'Gradient-boosted tree (mtg_nbo_v3) that outputs a product ∈ {refi, heloc, cashout, purchase, retention} with calibrated propensity.',
    lineage: [
      { layer: 'FEATURES', name: 'features.borrower_360', meta: 'Owner Link + property + lien history' },
      { layer: 'MODEL', name: 'mlflow.mtg_nbo_v3', meta: 'AUROC 0.81 · brier 0.09' },
      { layer: 'GOVERNANCE', name: 'compliance.nbo_review_board', meta: 'Approved 2026-03-02' },
    ],
    signals: [
      { label: 'Top feature', source: 'SHAP', value: 'rate_spread_bps' },
      { label: '#2 feature', source: 'SHAP', value: 'avm_equity_pct' },
      { label: '#3 feature', source: 'SHAP', value: 'prior_heloc_flag' },
    ],
    updatedAt: '2026-04-20 06:12 UTC',
  },
  permit: {
    title: 'Permit signal',
    short: 'permits.building',
    description:
      'Building permit records joined to CLIP. Signal fires when permit value ≥ $25k within the last 180 days.',
    lineage: [
      { layer: 'SOURCE', name: 'cotality.permits.building', meta: '4.8M active records' },
      { layer: 'JOIN', name: 'join.permit_to_clip', meta: 'via address canonicalization' },
      { layer: 'SEMANTIC', name: 'metrics.permit_signal', meta: 'UC metric view' },
    ],
    signals: [
      { label: 'Permit type', source: 'permits.type', value: 'Kitchen remodel' },
      { label: 'Filed value', source: 'permits.value', value: '$48,000' },
      { label: 'Filed', source: 'permits.filed_at', value: '2026-03-17' },
    ],
    updatedAt: '2026-04-20 06:12 UTC',
  },
  config: {
    title: 'Campaign assumptions',
    short: 'config',
    description: 'Cost-per-contact and projected conversion assumptions, set per lender in campaign config.',
    lineage: [{ layer: 'CONFIG', name: 'lender.campaign_config' }],
    signals: [],
  },
};
