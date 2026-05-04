import type { DrawerSource } from '../components/AppContext';

/**
 * Route a raw UC source (e.g. `mip.gold.fn_in_the_money`, `cotality.permits.building`)
 * to the matching DRAWER_SOURCES entry. Falls back to a neutral descriptor that
 * still opens the drawer with the raw path so presenters see lineage rather
 * than a silently-mismatched chip.
 *
 * Keep this in sync with backend/services/scoring.source_display_label and
 * backend/api/offers._sources_for — the two must agree on which UC object
 * maps to which drawer entry.
 */
export function descriptorFor(rawSource: string): DrawerSource {
  const key = rawSource.toLowerCase();
  // 2026-05-04 fix (FIX E): `fn_rate_spread` was being swept into the ITM
  // drawer because the catch-all `rate_spread` substring matched both the
  // `fn_rate_spread` UC function (Market rate comparison evidence) AND
  // unrelated ITM-derived columns (`rate_spread_bps`). The result was
  // that clicking the "Market rate comparison" chip and the "In-the-money
  // rule" chip both opened the same drawer (In-the-Money logic). Now
  // `fn_rate_spread` routes to its own market-rate drawer, and ITM only
  // matches on `fn_in_the_money` / `itm`. Order matters: the more
  // specific UC-function checks come BEFORE the broad substring matches.
  if (key.includes('fn_rate_spread')) return DRAWER_SOURCES.marketRate;
  if (key.includes('fn_in_the_money') || key.includes('itm')) {
    return DRAWER_SOURCES.itm;
  }
  if (key.includes('fn_next_best_offer') || key.includes('fn_lead_score') || key.includes('nbo')) {
    return DRAWER_SOURCES.nbo;
  }
  if (key.includes('permit')) return DRAWER_SOURCES.permit;
  if (key.includes('population') || key.includes('public_records')) return DRAWER_SOURCES.population;
  return {
    title: rawSource,
    short: rawSource.split('.').pop() ?? rawSource,
    description: `Unity Catalog object: ${rawSource}. Click through for lineage once wired.`,
    lineage: [{ layer: 'UC', name: rawSource }],
    signals: [],
  };
}

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
 *
 * TODO (prototype-parity-audit P0-2 follow-up, 2026-05-04): when Cotality
 * MLS Delta Share lands in Unity Catalog, add a dedicated `mls` entry
 * here (lineage: `cotality.mls.listing` → `entity.property_clip` →
 * `metrics.listed_for_sale_flag`) and re-route the LeadTable RowPreview's
 * "MLS listing" chip from `population` to `mls`. The current reuse is an
 * honest placeholder — public-records lineage IS what the row currently
 * carries — but a dedicated entry will read more clearly to users.
 */
export const DRAWER_SOURCES: Record<string, DrawerSource> = {
  population: {
    title: 'Marketable population',
    short: 'cotality.public_records',
    description:
      'Deed & mortgage records joined to voluntary liens and the Owner Link graph, filtered by the lender configuration.',
    lineage: [
      { layer: 'SOURCE', name: 'cotality.public_records.deed_and_mortgage', meta: 'Delta Share · nationwide' },
      { layer: 'SOURCE', name: 'cotality.liens.voluntary_lien', meta: 'Delta Share · nationwide' },
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
    short: 'UC function · fn_in_the_money',
    description:
      'Flags a borrower when lien rate ≥ par refi rate + 75 bps and equity ≥ 15% on the latest AVM. Deterministic UC SQL function, parity-pinned to backend/services/scoring.py.',
    lineage: [
      { layer: 'SOURCE', name: 'cotality.mma.origination_refi', meta: 'Par rate feed (daily)' },
      { layer: 'SOURCE', name: 'cotality.avm.current', meta: 'Property value (monthly)' },
      { layer: 'SOURCE', name: 'cotality.liens.voluntary_lien', meta: 'Current lien rate' },
      { layer: 'PRIMITIVE', name: 'mip.gold.fn_in_the_money', meta: 'UC SQL · parity-pinned' },
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
  // 2026-05-04 fix (FIX E): split out so the "Market rate comparison"
  // chip opens its OWN drawer with the rate-feed lineage rather than
  // re-opening the In-the-Money rule explainer (the prior behavior
  // — both chips opened ITM — was misleading per user feedback).
  marketRate: {
    title: 'Market rate comparison',
    short: 'UC function · fn_rate_spread',
    description:
      'Computes the basis-point spread between a borrower’s current lien rate and the market par-refinance rate. Output feeds into the In-the-Money rule but is also surfaced standalone so loan officers can see the raw spread and the FRED par-rate snapshot it was compared against.',
    lineage: [
      { layer: 'SOURCE', name: 'fred.MORTGAGE30US', meta: 'FRED 30y conforming par-refi rate (weekly)' },
      { layer: 'SOURCE', name: 'cotality.mma.origination_refi', meta: 'Par rate feed (daily) — fallback' },
      { layer: 'SOURCE', name: 'cotality.liens.voluntary_lien', meta: 'Current lien rate, per CLIP' },
      { layer: 'PRIMITIVE', name: 'mip.gold.fn_rate_spread', meta: 'UC SQL · parity-pinned to scoring.py' },
      { layer: 'SEMANTIC', name: 'metrics.rate_spread_bps', meta: 'UC metric view' },
    ],
    signals: [
      { label: 'Market par rate (30y conf.)', source: 'fred.MORTGAGE30US', value: '6.250%' },
      { label: 'Borrower lien rate', source: 'voluntary_lien.current_rate', value: 'per row' },
      { label: 'Spread (bps)', source: 'derived', value: 'lien − par × 100' },
    ],
    updatedAt: '2026-04-20 06:12 UTC',
  },
  nbo: {
    title: 'Next-Best-Offer logic',
    short: 'UC function · fn_next_best_offer',
    description:
      'Deterministic decision tree over Cotality-derived signals. Output is a categorical product code (refi, heloc, cashout, purchase, retention, or nurture). No ML model — the logic is transparent and auditable in sql/uc_functions/fn_next_best_offer.sql, and parity-pinned to backend/services/scoring.py.',
    lineage: [
      { layer: 'FEATURES', name: 'mip.gold.borrower_360', meta: 'Cotality public records + Owner Link + lien history' },
      { layer: 'PRIMITIVE', name: 'mip.gold.fn_next_best_offer', meta: 'UC SQL decision tree' },
      { layer: 'PARITY', name: 'backend/services/scoring.py', meta: 'Pinned to 60+ golden cases' },
    ],
    signals: [
      { label: 'Input', source: 'borrower_360.rate_spread_bps', value: 'rate_spread_bps' },
      { label: 'Input', source: 'borrower_360.equity_pct', value: 'equity_pct' },
      { label: 'Input', source: 'borrower_360.has_permit', value: 'has_permit' },
      { label: 'Input', source: 'borrower_360.listed_for_sale', value: 'listed_for_sale' },
      { label: 'Input', source: 'borrower_360.is_investor', value: 'is_investor' },
      { label: 'Input', source: 'borrower_360.is_current_customer', value: 'is_current_customer' },
      { label: 'Input', source: 'borrower_360.is_competitor_lien', value: 'is_competitor_lien' },
    ],
    updatedAt: '2026-04-20 06:12 UTC',
  },
  permit: {
    title: 'Permit signal',
    short: 'permits.building',
    description:
      'Building permit records joined to CLIP. Signal fires when permit value ≥ $25k within the last 180 days.',
    lineage: [
      { layer: 'SOURCE', name: 'cotality.permits.building', meta: 'Delta Share · rolling 24 months' },
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
