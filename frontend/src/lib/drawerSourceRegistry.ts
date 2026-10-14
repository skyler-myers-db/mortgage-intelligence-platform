import type { DrawerSource } from '../components/AppContext';
import {
  ADDRESSABLE_POPULATION_KPI_LABEL,
  MARKETABLE_POPULATION_KPI_LABEL,
} from './populationLabels';

/**
 * What a chip and the drawer frame need at once: its title, short line,
 * asset and lineage family. The 2026-07-13 rule stands: a chip resolves
 * immediately, so this index stays in the shell.
 */
type DrawerSourceIndexEntry = Pick<DrawerSource, 'title' | 'short' | 'assetKey' | 'assetPath' | 'lineageFamily'> & {
  description?: never;
  signals?: never;
  usedIn?: never;
  definition?: never;
};

function defineDrawerSources<T extends Record<string, DrawerSourceIndexEntry>>(
  sources: T,
): { [K in keyof T]: DrawerSource & T[K] & { registryKey: K & string } } {
  const indexed: Record<string, DrawerSource> = {};
  for (const [registryKey, entry] of Object.entries(sources)) indexed[registryKey] = { registryKey, ...entry };
  return indexed as { [K in keyof T]: DrawerSource & T[K] & { registryKey: K & string } };
}

/**
 * The evidence-drawer registry's slim index (audit 2026-09-21 `bundle-04`
 * item 3). UI metadata describing each evidence-drawer entry: not borrower
 * data, the product-truth contract for what each clickable source means.
 *
 * Every entry carries its own key as `registryKey`; its description, signals
 * and "used in" list live in drawerSourceRegistry.prose.ts, which ships with
 * the drawer's lazy body (never statically imported from the shell) and is
 * resolved by key when the drawer renders (resolveDrawerProse).
 */
export const DRAWER_SOURCES = defineDrawerSources({
  population: {
    title: ADDRESSABLE_POPULATION_KPI_LABEL,
    lineageFamily: 'marketable_population',
    short: ADDRESSABLE_POPULATION_KPI_LABEL,
    // Governed anchor (2026-06-11): the marketable-population KPI is
    // COUNT(*) over mip.gold.borrower_360, so this drawer reads that
    // asset's governed metadata. Without an anchor the hero KPI's drawer
    // showed "Freshness unavailable", implying a data gap that wasn't real.
    assetKey: 'borrower_360',
    assetPath: 'mip.gold.borrower_360',
  },

  // Sibling of `population` for surfaces whose count HAS the contactability
  // gate pushed down (Portfolio Builder's default CONTACTABILITY = "Eligible
  // only"). Same asset, different predicate — and a materially different
  // number, so the chip must name the predicate it applied rather than
  // borrowing the addressable chip's copy.
  populationMarketable: {
    title: MARKETABLE_POPULATION_KPI_LABEL,
    lineageFamily: 'marketable_population',
    short: `${MARKETABLE_POPULATION_KPI_LABEL} — contact-eligible subset`,
    assetKey: 'borrower_360',
    assetPath: 'mip.gold.borrower_360',
  },

  equitySpreadPoints: {
    title: 'Equity × rate-spread scatter',
    lineageFamily: 'equity_spread_scatter',
    short: 'Equity × spread points',
    assetKey: 'equity_spread_points',
    assetPath: 'mip.gold.equity_spread_points',
  },

  leadPopulation: {
    title: 'Ranked lead population',
    lineageFamily: 'lead_queue_rank',
    short: 'Ranked lead population',
    assetKey: 'lead_population',
    assetPath: 'mip.gold.lead_population',
  },

  segmentPopulation: {
    title: 'Segment population',
    lineageFamily: 'segment_population',
    short: 'Segment population',
    assetKey: 'segment_population',
    assetPath: 'mip.gold.segment_population',
  },

  ownerGraph: {
    title: 'Property + owner graph',
    lineageFamily: 'property_identity',
    short: 'Property + owner graph',
    assetKey: 'property_owner_bridge',
    assetPath: 'mip.gold.property_owner_bridge',
  },

  householdRollup: {
    title: 'Household rollup',
    lineageFamily: 'property_identity',
    short: 'Household rollup',
    assetKey: 'household_rollup',
    assetPath: 'mip.gold.household_rollup',
  },

  propertyProfile: {
    title: 'Property profile',
    lineageFamily: 'property_identity',
    short: 'Property profile',
    assetKey: 'property_master',
    assetPath: 'mip.silver.property_master',
  },

  borrower360: {
    title: 'Borrower 360 feature set',
    lineageFamily: 'property_identity',
    short: 'Borrower 360 feature set',
    assetKey: 'borrower_360',
    assetPath: 'mip.gold.borrower_360',
  },

  borrowerDossier: {
    title: 'Borrower dossier proof table',
    lineageFamily: 'borrower_proof',
    short: 'Borrower dossier proof table',
    assetKey: 'borrower_dossier',
    assetPath: 'mip.gold.borrower_dossier',
  },

  lien: {
    title: 'Voluntary lien',
    lineageFamily: 'lien_economics',
    short: 'Voluntary lien',
    assetKey: 'lien_current',
    assetPath: 'mip.silver.lien_current',
  },

  rateSpread: {
    title: 'Rate spread evidence',
    lineageFamily: 'rate_spread',
    short: 'Rate spread evidence',
    assetKey: 'evidence_events',
    assetPath: 'mip.gold.evidence_events',
  },

  mortgageDomain: {
    title: 'Mortgage Domain events',
    lineageFamily: 'lifecycle_evidence',
    short: 'Mortgage Domain events',
    assetKey: 'mortgage_events',
    assetPath: 'mip.silver.mortgage_events',
  },

  ownerTransfer: {
    title: 'Owner Transfer events',
    lineageFamily: 'lifecycle_evidence',
    short: 'Owner Transfer events',
    assetKey: 'owner_transfer_events',
    assetPath: 'mip.silver.owner_transfer_events',
  },

  evidenceStream: {
    title: 'Evidence stream',
    lineageFamily: 'lifecycle_evidence',
    short: 'Evidence stream',
    assetKey: 'evidence_events',
    assetPath: 'mip.gold.evidence_events',
  },

  sourceReadiness: {
    title: 'Source readiness',
    lineageFamily: 'source_readiness',
    short: 'Source readiness',
    assetKey: 'source_readiness',
    assetPath: 'mip.gold.source_readiness',
  },

  avm: {
    title: 'AVM equity',
    lineageFamily: 'lien_economics',
    short: 'AVM equity',
    assetKey: 'lien_current',
    assetPath: 'mip.silver.lien_current',
  },

  itm: {
    title: 'Refinance economics screen',
    lineageFamily: 'in_the_money',
    short: 'Rate + equity screen',
    // Governed anchor (2026-06-11): the high-intent KPI sums the
    // in_the_money column on mip.gold.borrower_360 — same rationale as
    // the population entry above.
    assetKey: 'borrower_360',
    assetPath: 'mip.gold.borrower_360',
  },

  marketRate: {
    title: 'Market rate comparison',
    lineageFamily: 'rate_spread',
    short: 'Market rate comparison',
    assetKey: 'market_rates_weekly',
    assetPath: 'mip.silver.market_rates_weekly',
  },

  // "Why now" rate window (2026-09-21 audit, dataviz-08): the weekly FRED
  // print against the CURRENT fixed-rate book. Anchored on the gold table the
  // endpoint reads; the signals cite the silver series it is joined from and
  // the primitives that decide in-the-money, so the chart's evidence names
  // both source tables, not just the derived one.
  rateWindow: {
    title: 'Rate window: market rate against the book',
    lineageFamily: 'rate_spread',
    short: "Weekly 30-year fixed rate against today's fixed-rate note-rate band",
    assetKey: 'rate_window_weekly',
    assetPath: 'mip.gold.rate_window_weekly',
  },

  // Rate Lever (audit wow-stage-1): the map's rate-scenario colouring. The
  // grid is precomputed per refresh; only the contactable subset is live.
  rateSensitivity: {
    title: 'Rate scenario: in the money if par moved',
    lineageFamily: 'rate_spread',
    short: 'Rate scenario grid, per state',
    assetKey: 'rate_sensitivity_rollup',
    assetPath: 'mip.gold.rate_sensitivity_rollup',
  },

  portfolioHeadlineView: {
    title: 'Portfolio headline metric view',
    lineageFamily: 'marketable_population',
    short: 'Portfolio headline metric view',
    assetKey: 'portfolio_headline_metric_view',
    assetPath: 'mip.semantics.portfolio_headline_metric_view',
  },

  leadGenerationView: {
    title: 'Lead-generation metric view',
    lineageFamily: 'lead_queue_rank',
    short: 'Lead-generation metric view',
    assetKey: 'lead_generation_metric_view',
    assetPath: 'mip.semantics.lead_generation_metric_view',
  },

  segmentPerformanceView: {
    title: 'Segment performance metric view',
    lineageFamily: 'segment_population',
    short: 'Segment performance metric view',
    assetKey: 'segment_performance_metric_view',
    assetPath: 'mip.semantics.segment_performance_metric_view',
  },

  borrowerOpportunityView: {
    title: 'Borrower opportunity metric view',
    lineageFamily: 'borrower_proof',
    short: 'Borrower opportunity metric view',
    assetKey: 'borrower_opportunity_metric_view',
    assetPath: 'mip.semantics.borrower_opportunity_metric_view',
  },

  leadScore: {
    title: 'Opportunity score',
    lineageFamily: 'opportunity_score',
    short: 'Opportunity score',
    assetKey: 'lead_scores',
    assetPath: 'mip.gold.lead_scores',
  },

  lockinCohort: {
    title: 'Lock-in cohort',
    lineageFamily: 'lockin_cohort',
    short: 'Lock-in cohort',
    assetKey: 'lockin_cohort',
    assetPath: 'mip.gold.lockin_cohort',
  },

  funnelSnapshot: {
    title: 'Daily funnel snapshot',
    lineageFamily: 'funnel_snapshot',
    short: 'Daily funnel snapshot',
    assetKey: 'funnel_snapshot_daily',
    assetPath: 'mip.gold.funnel_snapshot_daily',
  },

  countyRollup: {
    title: 'County opportunity rollup',
    lineageFamily: 'geographic_rollups',
    short: 'County rollup',
    assetKey: 'county_rollup',
    assetPath: 'mip.gold.county_rollup',
  },

  zipRollup: {
    title: 'ZIP opportunity rollup',
    lineageFamily: 'geographic_rollups',
    short: 'ZIP rollup',
    assetKey: 'zip_rollup',
    assetPath: 'mip.gold.zip_rollup',
  },

  nbo: {
    title: 'Primary offer rules',
    lineageFamily: 'next_best_offer',
    short: 'How the offer path was selected',
    assetKey: 'borrower_360',
    assetPath: 'mip.gold.borrower_360',
  },

  helocPropensity: {
    title: 'HELOC propensity signal',
    lineageFamily: 'propensity_signals',
    short: 'HELOC propensity',
    assetKey: 'heloc_propensity',
    assetPath: 'mip.silver.heloc_propensity',
  },

  refiPropensity: {
    title: 'Refi propensity signal',
    lineageFamily: 'propensity_signals',
    short: 'Refi propensity',
    assetKey: 'refi_propensity',
    assetPath: 'mip.silver.refi_propensity',
  },

  loanProductType: {
    title: 'Loan product type evidence',
    lineageFamily: 'loan_dimensions',
    short: 'Product type',
    assetKey: 'borrower_360',
    assetPath: 'mip.gold.borrower_360',
  },

  originationChannel: {
    title: 'Origination channel evidence',
    lineageFamily: 'loan_dimensions',
    short: 'Origination channel',
    assetKey: 'loan_applications',
    assetPath: 'mip.first_party.loan_applications',
  },

  permit: {
    title: 'Building permit signal',
    lineageFamily: 'permit_readiness',
    short: 'Building Permits - pending',
    assetKey: 'source_readiness',
    assetPath: 'mip.gold.source_readiness',
  },

  mls: {
    title: 'MLS listing signal',
    lineageFamily: 'listing_activity',
    short: 'MLS listing',
    assetKey: 'listing_activity',
    assetPath: 'mip.silver.listing_activity',
  },

  assignmentOverlay: {
    title: 'Assigned vs. unattended coverage',
    lineageFamily: 'assignment_overlay_uc_input',
    short: 'assignment_overlay',
  },

  callDispositions: {
    title: 'Campaign contact dispositions',
    short: 'call_dispositions',
    assetPath: 'mip_app.call_dispositions',
  },

  leadOutcomes: {
    title: 'Closed-loop lead outcomes',
    short: 'lead_outcomes',
    assetPath: 'mip_app.lead_outcomes',
  },

  config: {
    title: 'Campaign assumptions',
    short: 'config',
  },
});

export type DrawerSourceKey = keyof typeof DRAWER_SOURCES;

/** A registry key, checked against the index (a source's `registryKey` is a plain string). */
export function isDrawerSourceKey(key: string | undefined): key is DrawerSourceKey {
  return key !== undefined && Object.prototype.hasOwnProperty.call(DRAWER_SOURCES, key);
}
