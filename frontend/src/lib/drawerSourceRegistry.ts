import type { DrawerSource } from '../components/AppContext';
import {
  ADDRESSABLE_POPULATION_KPI_LABEL,
  MARKETABLE_POPULATION_KPI_LABEL,
} from './populationLabels';

function defineDrawerSources<T extends Record<string, DrawerSource>>(
  sources: T,
): { [K in keyof T]: DrawerSource & T[K] } {
  return sources;
}

/**
 * UI metadata describing each evidence-drawer entry. This is not borrower data;
 * it is the product-truth contract for what each clickable source means.
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
    description: 'Deed, lien, and Owner Link records.',
    signals: [
      { label: 'KPI measure', source: 'portfolio_headline_metric_view', value: 'COUNT(*)' },
      { label: 'Underlying rows', source: 'mip.gold.borrower_360', value: 'borrower grain' },
      { label: 'Ownership graph', source: 'entity.owner_link', value: 'CLIP-grain' },
      { label: 'Tenant lens', source: 'mip.ref.lender_dictionary', value: 'gold refresh' },
    ],
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
    description:
      'COUNT(*) over the headline metric view with the build criteria AND the governed contactability gate pushed down: opt-in consent, no suppression reason, not DNC, past the recontact date, and outside the frequency cap. That gate is what separates this count from the addressable population.',
    signals: [
      { label: 'KPI measure', source: 'portfolio_headline_metric_view', value: 'COUNT(*)' },
      { label: 'Eligibility gate', source: 'borrower_360.marketing_eligible', value: 'TRUE' },
      { label: 'Consent', source: 'borrower_360.consent_status', value: 'opt_in' },
      { label: 'Suppression', source: 'borrower_360.suppression_reason', value: 'IS NULL' },
      { label: 'Do-not-contact', source: 'borrower_360.dnc', value: 'FALSE' },
    ],
  },

  equitySpreadPoints: {
    title: 'Equity × rate-spread scatter',
    lineageFamily: 'equity_spread_scatter',
    short: 'Equity × spread points',
    assetKey: 'equity_spread_points',
    assetPath: 'mip.gold.equity_spread_points',
    description:
      'Precomputed per-borrower equity and rate-spread coordinates with canonical score bands. The overview shows server-side density bins; zooming loads real borrowers capped at the server limit.',
    signals: [
      { label: 'X axis', source: 'equity_spread_points.equity_pct', value: 'AVM equity %' },
      { label: 'Y axis', source: 'equity_spread_points.rate_spread_bps', value: 'fn_rate_spread' },
      { label: 'Band', source: 'equity_spread_points.score_band', value: 'fn_score_band' },
      { label: 'Bins', source: 'equity_spread_points.equity_bin_pct', value: 'precomputed' },
    ],
  },

  leadPopulation: {
    title: 'Ranked lead population',
    lineageFamily: 'lead_queue_rank',
    short: 'Ranked lead population',
    assetKey: 'lead_population',
    assetPath: 'mip.gold.lead_population',
    description: 'Gold Lead Queue rows: score >= 50, safe fields, segments, evidence, offer, rank, and approval.',
    signals: [
      { label: 'Floor', source: 'opportunity_score', value: '>= 50' },
      { label: 'Rank', source: 'lead_population.rank', value: 'score + CLIP' },
      { label: 'Evidence', source: 'lead_population.evidence_ids', value: 'audited' },
      { label: 'Approval', source: 'lead_population.approval_status', value: 'Lakebase' },
    ],
  },

  segmentPopulation: {
    title: 'Segment population',
    lineageFamily: 'segment_population',
    short: 'Segment population',
    assetKey: 'segment_population',
    assetPath: 'mip.gold.segment_population',
    description:
      'Gold segment rollup: predicate, mode, geography, count, and average score.',
    signals: [
      { label: 'Segment count', source: 'segment_population.count', value: 'matching borrowers' },
      { label: 'Average score', source: 'segment_population.avg_score', value: 'average' },
      { label: 'Listing segment', source: 'borrower_360.listed_for_sale', value: 'live from Cotality MLS' },
      { label: 'HELOC intent', source: 'borrower_360.has_heloc_propensity_trigger', value: 'Cotality propensity' },
    ],
  },

  ownerGraph: {
    title: 'Property + owner graph',
    lineageFamily: 'property_identity',
    short: 'Property + owner graph',
    assetKey: 'property_owner_bridge',
    assetPath: 'mip.gold.property_owner_bridge',
    description: 'CLIP property records with Owner Link, occupancy, and investor flags.',
    signals: [
      { label: 'Property identity', source: 'mip.silver.property_master.clip', value: 'masked CLIP ref' },
      { label: 'Owner link', source: 'property_owner_bridge.owner_link_id', value: 'masked ref' },
      { label: 'Investor', source: 'borrower_360.related_property_count', value: '2+ properties' },
    ],
  },

  householdRollup: {
    title: 'Household rollup',
    lineageFamily: 'property_identity',
    short: 'Household rollup',
    assetKey: 'household_rollup',
    assetPath: 'mip.gold.household_rollup',
    description:
      'Opt-in household grouping. Borrower remains default; dedup selects one eligible primary.',
    signals: [
      { label: 'Default unit', source: 'campaign.household_dedup.enabled', value: 'borrower' },
      { label: 'Primary contact', source: 'household_rollup.household_rank', value: 'eligible, score, id' },
      { label: 'Co-owners', source: 'suppressed_by_household_dedup', value: 'campaign summary' },
      { label: 'Derivation', source: 'household_rollup.derivation_source_tables', value: 'UC lineage' },
    ],
  },

  propertyProfile: {
    title: 'Property profile',
    lineageFamily: 'property_identity',
    short: 'Property profile',
    assetKey: 'property_master',
    assetPath: 'mip.silver.property_master',
    description:
      'CLIP geography, occupancy, owner type, mailing/situs, and foreclosure features.',
    signals: [
      { label: 'Property identity', source: 'property_master.clip', value: 'masked CLIP ref' },
      { label: 'Occupancy / mailing', source: 'property_master', value: 'safe flags' },
      { label: 'Distress', source: 'property_master.foreclosure_stage_code', value: 'when present' },
    ],
  },

  borrower360: {
    title: 'Borrower 360 feature set',
    lineageFamily: 'property_identity',
    short: 'Borrower 360 feature set',
    assetKey: 'borrower_360',
    assetPath: 'mip.gold.borrower_360',
    description:
      'Canonical borrower table for dossier, offers, Lead Queue, and Genie.',
    signals: [
      { label: 'Property ref', source: 'property_master.clip', value: 'masked CLIP ref' },
      { label: 'Owner ref', source: 'property_owner_bridge.owner_link_id', value: 'masked ref' },
      { label: 'Economics', source: 'lien_current + market_rates_weekly', value: 'rate, LTV, equity' },
      { label: 'First-party', source: 'demo first-party feeds when configured', value: 'relationship' },
    ],
  },

  borrowerDossier: {
    title: 'Borrower dossier proof table',
    lineageFamily: 'borrower_proof',
    short: 'Borrower dossier proof table',
    assetKey: 'borrower_dossier',
    assetPath: 'mip.gold.borrower_dossier',
    description:
      'Governed borrower-proof table for dossier, score, decisions, evidence, and SQL proof.',
    signals: [
      { label: 'Dossier', source: 'borrower_dossier.borrower_id', value: 'masked id' },
      { label: 'Score', source: 'borrower_dossier.opportunity_score', value: 'vs fn_lead_score' },
      { label: 'Decision inputs', source: 'rate_spread_bps / equity_pct', value: 'checked' },
      { label: 'Evidence rows', source: 'borrower_dossier.evidence_events', value: 'redacted' },
    ],
  },

  lien: {
    title: 'Voluntary lien',
    lineageFamily: 'lien_economics',
    short: 'Voluntary lien',
    assetKey: 'lien_current',
    assetPath: 'mip.silver.lien_current',
    description: 'Current lien status, balance, lender ref, and rate.',
    signals: [
      { label: 'Original UPB', source: 'lien_current.first_pos_amount', value: 'borrower' },
      { label: 'Lien rate', source: 'lien_current.first_pos_rate', value: 'borrower' },
      { label: 'Elapsed months', source: 'months_between(refresh_at, first_pos_date)', value: 'refresh' },
      { label: 'Estimated UPB', source: 'fn_estimated_upb', value: 'amortized' },
      { label: 'Confidence band', source: 'fn_estimated_upb_confidence_band', value: '1%-15%' },
      { label: 'Lender relationship', source: 'mip.ref.lender_dictionary', value: 'tenant/competitor/other' },
    ],
  },

  rateSpread: {
    title: 'Rate spread evidence',
    lineageFamily: 'rate_spread',
    short: 'Rate spread evidence',
    assetKey: 'evidence_events',
    assetPath: 'mip.gold.evidence_events',
    description:
      'Compares Cotality lien rate with the market-rate reference.',
    signals: [
      { label: 'Borrower rate', source: 'lien_current.first_pos_rate', value: 'borrower' },
      { label: 'Market rate', source: 'market_rates_weekly.rate_fraction', value: 'latest' },
      { label: 'Spread', source: 'fn_rate_spread', value: 'bps vs par' },
    ],
  },

  mortgageDomain: {
    title: 'Mortgage Domain events',
    lineageFamily: 'lifecycle_evidence',
    short: 'Mortgage Domain events',
    assetKey: 'mortgage_events',
    assetPath: 'mip.silver.mortgage_events',
    description:
      'Cotality mortgage events for refi, payoff, release, and lifecycle signals.',
    signals: [
      { label: 'Recent refinance', source: 'evidence_events.signal_type', value: 'recent_refi' },
      { label: 'Recent payoff', source: 'evidence_events.signal_type', value: 'recent_payoff' },
      { label: 'Refi date', source: 'mortgage_events.event_date', value: 'event' },
      { label: 'Payoff release', source: 'mortgage_events.release_date', value: 'event' },
    ],
  },

  ownerTransfer: {
    title: 'Owner Transfer events',
    lineageFamily: 'lifecycle_evidence',
    short: 'Owner Transfer events',
    assetKey: 'owner_transfer_events',
    assetPath: 'mip.silver.owner_transfer_events',
    description: 'Cotality transfer/sale events for recent-sale signals.',
    signals: [
      { label: 'Recent sale', source: 'evidence_events.signal_type', value: 'recent_sale' },
      { label: 'Sale date', source: 'owner_transfer_events.sale_date', value: 'event' },
    ],
  },

  evidenceStream: {
    title: 'Evidence stream',
    lineageFamily: 'lifecycle_evidence',
    short: 'Evidence stream',
    assetKey: 'evidence_events',
    assetPath: 'mip.gold.evidence_events',
    description:
      'Borrower-safe evidence table with source, signal, text, confidence, and timestamp.',
    signals: [
      {
        label: 'Controlled vocab',
        source: 'evidence_events.signal_type',
        value: 'listing, HELOC/refi live; permit reserved',
      },
      { label: 'Display text', source: 'evidence_events.display_text', value: 'deterministic evidence summary' },
      { label: 'Confidence', source: 'evidence_events.confidence', value: '0..1' },
    ],
  },

  sourceReadiness: {
    title: 'Source readiness',
    lineageFamily: 'source_readiness',
    short: 'Source readiness',
    assetKey: 'source_readiness',
    assetPath: 'mip.gold.source_readiness',
    description:
      'Non-PII readiness ledger for feed status.',
    signals: [
      { label: 'Status', source: 'source_readiness.status', value: 'live / roadmap / blocked' },
      { label: 'Rows', source: 'source_readiness.row_count', value: 'row proof' },
      { label: 'Checked at', source: 'source_readiness.checked_at', value: 'refresh timestamp' },
    ],
  },

  avm: {
    title: 'AVM equity',
    lineageFamily: 'lien_economics',
    short: 'AVM equity',
    assetKey: 'lien_current',
    assetPath: 'mip.silver.lien_current',
    description: 'AVM value plus lien balance for equity, CLTV/LTV, and product fit.',
    signals: [
      { label: 'AVM value', source: 'lien_current.avm_value', value: 'borrower' },
      { label: 'Equity estimate', source: 'borrower_360.equity_estimate', value: 'AVM - lien' },
      { label: 'Equity %', source: 'borrower_360.equity_pct', value: 'AVM + lien' },
    ],
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
    description:
      'Refi screen: rate >= 75 bps above market and equity >= 15%; not the full score.',
    signals: [
      { label: 'KPI measure', source: 'portfolio_headline_metric_view.in_the_money', value: 'SUM' },
      { label: 'Par refi rate', source: 'market_rates_weekly.market_rate_fraction', value: 'latest' },
      { label: 'Lien rate', source: 'voluntary_lien.current_rate', value: 'borrower' },
      { label: 'Rate spread', source: 'derived', value: 'lien minus par' },
      { label: 'Equity %', source: 'avm + lien balance', value: 'borrower' },
    ],
  },

  marketRate: {
    title: 'Market rate comparison',
    lineageFamily: 'rate_spread',
    short: 'Market rate comparison',
    assetKey: 'market_rates_weekly',
    assetPath: 'mip.silver.market_rates_weekly',
    description:
      'Basis-point spread between lien rate and market refi reference.',
    signals: [
      { label: 'Market par rate', source: 'fred.MORTGAGE30US', value: 'latest' },
      { label: 'Borrower lien rate', source: 'voluntary_lien.current_rate', value: 'row' },
      { label: 'Spread (bps)', source: 'derived', value: 'lien - par x 100' },
    ],
  },

  portfolioHeadlineView: {
    title: 'Portfolio headline metric view',
    lineageFamily: 'marketable_population',
    short: 'Portfolio headline metric view',
    assetKey: 'portfolio_headline_metric_view',
    assetPath: 'mip.semantics.portfolio_headline_metric_view',
    description:
      'Borrower-grain semantic view defining every home headline KPI: marketable population, refi economics screen, high opportunity, offers available, and primary offer paths.',
    signals: [
      { label: 'Addressable population', source: 'portfolio_headline_metric_view', value: 'COUNT(*)' },
      { label: 'Refi economics screen', source: 'in_the_money', value: 'SUM' },
      { label: 'High opportunity', source: 'is_high_opportunity', value: 'fn_high_opportunity' },
      { label: 'Offers available', source: 'offer_available', value: 'non-null offer' },
      { label: 'Primary offer paths', source: 'offer_recommended', value: 'actionable lane' },
    ],
  },

  leadGenerationView: {
    title: 'Lead-generation metric view',
    lineageFamily: 'lead_queue_rank',
    short: 'Lead-generation metric view',
    assetKey: 'lead_generation_metric_view',
    assetPath: 'mip.semantics.lead_generation_metric_view',
    description:
      'Semantic view for lead-generation KPIs, ranks, geography, score bands, and offer funnel.',
    signals: [
      { label: 'Population', source: 'lead_population', value: 'ranked grain' },
      { label: 'Score', source: 'lead_scores.opportunity_score', value: '0-100' },
    ],
  },

  segmentPerformanceView: {
    title: 'Segment performance metric view',
    lineageFamily: 'segment_population',
    short: 'Segment performance metric view',
    assetKey: 'segment_performance_metric_view',
    assetPath: 'mip.semantics.segment_performance_metric_view',
    description:
      'Semantic view for segment comparisons, MLS listings, and HELOC propensity.',
    signals: [
      { label: 'Segment', source: 'segment_population.segment_code', value: 'controlled vocab' },
      { label: 'Borrowers', source: 'segment_population.count', value: 'predicate count' },
      { label: 'Listed for Sale', source: 'borrower_360.listed_for_sale', value: 'MLS row' },
      { label: 'HELOC Intent', source: 'borrower_360.has_heloc_propensity_trigger', value: 'score >= 700' },
    ],
  },

  borrowerOpportunityView: {
    title: 'Borrower opportunity metric view',
    lineageFamily: 'borrower_proof',
    short: 'Borrower opportunity metric view',
    assetKey: 'borrower_opportunity_metric_view',
    assetPath: 'mip.semantics.borrower_opportunity_metric_view',
    description: 'Borrower-level semantic view for Genie cohort, geography, score, and offer questions.',
    signals: [
      { label: 'Borrower grain', source: 'borrower_360.borrower_id', value: 'masked id' },
      { label: 'Primary offer', source: 'borrower_360.recommended_offer_code', value: 'offer path' },
      { label: 'Evidence ids', source: 'borrower_360.evidence_ids', value: 'evidence refs' },
    ],
  },

  leadScore: {
    title: 'Opportunity score',
    lineageFamily: 'opportunity_score',
    short: 'Opportunity score',
    assetKey: 'lead_scores',
    assetPath: 'mip.gold.lead_scores',
    description:
      '0-100 borrower score from economics, intent, fit, relationship, and evidence.',
    signals: [
      { label: 'KPI measure', source: 'portfolio_headline_metric_view.is_high_opportunity', value: 'fn_high_opportunity' },
      { label: 'Economic incentive', source: 'lead_scores.economic_incentive', value: '35% weight' },
      { label: 'Intent trigger', source: 'lead_scores.intent_trigger', value: '30% weight' },
      { label: 'Fit', source: 'lead_scores.fit', value: '15% weight' },
      { label: 'Relationship', source: 'lead_scores.relationship', value: '10% weight' },
      { label: 'Evidence', source: 'lead_scores.evidence', value: '10% weight' },
    ],
  },

  lockinCohort: {
    title: 'Lock-in cohort',
    lineageFamily: 'lockin_cohort',
    short: 'Lock-in cohort',
    assetKey: 'lockin_cohort',
    assetPath: 'mip.gold.lockin_cohort',
    description:
      'Gold cohort for rate-lock and refi-sensitivity questions.',
    signals: [
      { label: 'Current rate', source: 'borrower_360.current_rate', value: 'borrower' },
      { label: 'Market rate', source: 'market_rates_weekly.market_rate_fraction', value: 'latest' },
      { label: 'Rate spread', source: 'derived', value: 'basis points' },
    ],
  },

  funnelSnapshot: {
    title: 'Daily funnel snapshot',
    lineageFamily: 'funnel_snapshot',
    short: 'Daily funnel snapshot',
    assetKey: 'funnel_snapshot_daily',
    assetPath: 'mip.gold.funnel_snapshot_daily',
    description:
      'Daily state and segment funnel counts combining borrower economics with the scheduled UC mirror of operational lifecycle state.',
    signals: [
      { label: 'Population', source: 'mip.gold.borrower_360', value: 'state + segment' },
      { label: 'Decision state', source: 'mip.gold.borrower_lifecycle_state', value: 'scheduled UC mirror' },
      { label: 'Daily counts', source: 'mip.gold.funnel_snapshot_daily', value: 'snapshot grain' },
    ],
  },

  countyRollup: {
    title: 'County opportunity rollup',
    lineageFamily: 'geographic_rollups',
    short: 'County rollup',
    assetKey: 'county_rollup',
    assetPath: 'mip.gold.county_rollup',
    description:
      'County-grain addressable population, economics, score, and dominant-segment facts derived from Borrower 360.',
    signals: [
      { label: 'County key', source: 'mip.silver.property_master', value: '5-character FIPS' },
      { label: 'Population', source: 'mip.gold.borrower_360', value: 'addressable borrowers' },
      { label: 'Map grain', source: 'mip.gold.county_rollup', value: 'county + snapshot date' },
    ],
  },

  zipRollup: {
    title: 'ZIP opportunity rollup',
    lineageFamily: 'geographic_rollups',
    short: 'ZIP rollup',
    assetKey: 'zip_rollup',
    assetPath: 'mip.gold.zip_rollup',
    description:
      'ZIP-grain addressable population, score, dominant segment, and stable sample borrower derived from Borrower 360.',
    signals: [
      { label: 'ZIP key', source: 'mip.silver.property_master', value: '5-digit ZIP' },
      { label: 'Population', source: 'mip.gold.borrower_360', value: 'addressable borrowers' },
      { label: 'Map grain', source: 'mip.gold.zip_rollup', value: 'state + county + ZIP' },
    ],
  },

  nbo: {
    title: 'Primary offer rules',
    lineageFamily: 'next_best_offer',
    short: 'How the offer path was selected',
    assetKey: 'borrower_360',
    assetPath: 'mip.gold.borrower_360',
    description:
      'Chooses one offer path from current signals: purchase, refi/equity review, or nurture.',
    signals: [
      { label: 'KPI measure', source: 'portfolio_headline_metric_view.offer_recommended', value: 'SUM' },
      { label: 'Offers available', source: 'portfolio_headline_metric_view.offer_available', value: 'non-null offer' },
      { label: 'Rate spread', source: 'borrower_360.rate_spread_bps', value: 'refi economics' },
      { label: 'Equity', source: 'borrower_360.equity_pct', value: 'product fit' },
      { label: 'HELOC intent', source: 'borrower_360.has_heloc_propensity_trigger', value: 'trigger' },
      { label: 'Permit activity', source: 'borrower_360.has_permit', value: 'filed permit only' },
      { label: 'Listing activity', source: 'borrower_360.listed_for_sale', value: 'purchase path' },
      { label: 'Investor profile', source: 'borrower_360.is_investor', value: 'portfolio path' },
      { label: 'Current customer', source: 'borrower_360.is_current_customer', value: 'relationship' },
      { label: 'Competitor lien', source: 'borrower_360.is_competitor_lien', value: 'recapture' },
    ],
  },

  helocPropensity: {
    title: 'HELOC propensity signal',
    lineageFamily: 'propensity_signals',
    short: 'HELOC propensity',
    assetKey: 'heloc_propensity',
    assetPath: 'mip.silver.heloc_propensity',
    description:
      'Cotality HELOC propensity feed for HELOC Intent; not a filed permit.',
    signals: [
      { label: 'Score', source: 'heloc_propensity_score', value: '0-999' },
      { label: 'Trigger', source: 'has_heloc_propensity_trigger', value: '>= 700' },
      { label: 'Run date', source: 'heloc_propensity_run_date', value: 'model run' },
    ],
  },

  refiPropensity: {
    title: 'Refi propensity signal',
    lineageFamily: 'propensity_signals',
    short: 'Refi propensity',
    assetKey: 'refi_propensity',
    assetPath: 'mip.silver.refi_propensity',
    description:
      'Cotality refi propensity feed; supplements rate-spread economics.',
    signals: [
      { label: 'Score', source: 'refi_propensity_score', value: '0-999' },
      { label: 'Trigger', source: 'has_refi_propensity_trigger', value: '>= 700' },
      { label: 'Run date', source: 'refi_propensity_run_date', value: 'model run' },
    ],
  },

  loanProductType: {
    title: 'Loan product type evidence',
    lineageFamily: 'loan_dimensions',
    short: 'Product type',
    assetKey: 'borrower_360',
    assetPath: 'mip.gold.borrower_360',
    description: 'Derived from first-position loan type plus the governed jumbo limit.',
    signals: [
      { label: 'Loan type', source: 'lien_current.first_pos_loan_type', value: 'CNV / FHA / VA' },
      { label: 'Original amount', source: 'lien_current.first_pos_amount', value: 'vs limit' },
      { label: 'Conforming limit', source: 'mip.ref.offer_rules_config', value: 'mip_conforming_loan_limit_usd' },
    ],
  },

  originationChannel: {
    title: 'Origination channel evidence',
    lineageFamily: 'loan_dimensions',
    short: 'Origination channel',
    assetKey: 'loan_applications',
    assetPath: 'mip.first_party.loan_applications',
    description: 'Most recent funded LOS application channel.',
    signals: [
      { label: 'Channel', source: 'loan_applications.application_channel', value: 'funded' },
      { label: 'Unknown', source: 'borrower_360.origination_channel', value: 'NULL' },
    ],
  },

  permit: {
    title: 'Building permit signal',
    lineageFamily: 'permit_readiness',
    short: 'Building Permits - pending',
    assetKey: 'source_readiness',
    assetPath: 'mip.gold.source_readiness',
    description:
      'Filed permit rows are pending; has_permit stays false until a governed table exists.',
    signals: [
      { label: 'Readiness', source: 'mip.gold.source_readiness', value: 'roadmap' },
      { label: 'Permit flag', source: 'mip.gold.borrower_360.has_permit', value: 'filed only' },
      { label: 'HELOC intent', source: 'mip.silver.heloc_propensity', value: 'separate' },
    ],
  },

  mls: {
    title: 'MLS listing signal',
    lineageFamily: 'listing_activity',
    short: 'MLS listing',
    assetKey: 'listing_activity',
    assetPath: 'mip.silver.listing_activity',
    description:
      'Cotality MLS listings joined to CLIP; active/under-contract rows drive purchase intent.',
    signals: [
      { label: 'Readiness', source: 'mip.gold.source_readiness', value: 'live' },
      { label: 'listed_for_sale', source: 'mip.gold.borrower_360', value: 'active/contract' },
      { label: 'Listing evidence', source: 'mip.gold.evidence_events', value: 'signal_type = listing' },
    ],
  },

  assignmentOverlay: {
    title: 'Assigned vs. unattended coverage',
    lineageFamily: 'assignment_overlay_uc_input',
    short: 'assignment_overlay',
    description:
      'Per-geography difference between the live lead queue and active loan-officer assignments — the leads nobody is working.',
    signals: [
      { label: 'Leads', source: 'borrower_360.marketing_eligible', value: 'live queue population' },
      { label: 'Assigned', source: 'lead_assignments.released_at IS NULL', value: 'active hold' },
      { label: 'Unattended', source: 'lead_count - assigned_count', value: 'no active assignment' },
      { label: 'LO coverage', source: 'loan_officers.coverage_*', value: 'array membership' },
    ],
  },

  callDispositions: {
    title: 'Campaign contact dispositions',
    short: 'call_dispositions',
    assetPath: 'mip_app.call_dispositions',
    description:
      'Lakebase contact-attempt records used to qualify campaign-performance evidence without exposing borrower contact details.',
    signals: [
      { label: 'Contact result', source: 'call_dispositions.outcome', value: 'reviewed disposition' },
      { label: 'Attempt order', source: 'call_dispositions.attempt_number', value: 'positive integer' },
      { label: 'Event time', source: 'call_dispositions.occurred_at', value: 'timestamp' },
      { label: 'Owner', source: 'call_dispositions.lo_email', value: 'sales-team identity' },
    ],
    usedIn: ['Campaign performance qualification', 'Sales Ops analytics'],
  },

  leadOutcomes: {
    title: 'Closed-loop lead outcomes',
    short: 'lead_outcomes',
    assetPath: 'mip_app.lead_outcomes',
    description:
      'PII-safe Lakebase conversion outcomes imported from reviewed CRM, LOS, POS, servicing, webhook, or manual sources.',
    signals: [
      { label: 'Outcome', source: 'lead_outcomes.outcome_type', value: 'application through funded/lost' },
      { label: 'Origin', source: 'lead_outcomes.source_system', value: 'reviewed system enum' },
      { label: 'Campaign', source: 'lead_outcomes.campaign_id', value: 'optional campaign link' },
      { label: 'Event time', source: 'lead_outcomes.occurred_at', value: 'timestamp' },
    ],
    usedIn: ['Campaign conversion evidence', 'Sales Ops analytics'],
  },

  config: {
    title: 'Campaign assumptions',
    short: 'config',
    description: 'Cost-per-contact and projected conversion assumptions, set per lender in campaign config.',
    signals: [],
  },
});
