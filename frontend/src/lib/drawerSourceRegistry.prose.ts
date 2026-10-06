import type { DrawerSource } from '../components/AppContext';
import type { DrawerSourceKey } from './drawerSourceRegistry';

/**
 * The evidence-drawer registry's prose (audit 2026-09-21 `bundle-04` item 3):
 * each entry's description, sanitized signals and "used in" list, moved out
 * of the shell's slim index (drawerSourceRegistry.ts) byte for byte, plus a
 * one-sentence `definition` of what a KPI counts (audit `flow-06`).
 *
 * Never statically imported from the initial closure: EvidenceDrawerBody
 * imports it, so it ships with the drawer's lazy body, and
 * evidenceDrawerBodyLoader warms it on idle / chip intent (the chunk only).
 * Keyed exactly by DrawerSourceKey: `satisfies` refuses a missing or an
 * extra key, so the index and the prose cannot drift.
 */

export interface DrawerSourceProse {
  description?: string;
  signals?: NonNullable<DrawerSource['signals']>;
  usedIn?: string[];
  /** One sentence: what the number counts. Shown under "How we got N". */
  definition?: string;
}

export const DRAWER_SOURCE_PROSE = {
  population: {
    description: 'Deed, lien, and Owner Link records.',
    definition:
      'Every borrower in the refreshed book, counted once, before any contactability gate is applied.',
    signals: [
      { label: 'KPI measure', source: 'portfolio_headline_metric_view', value: 'COUNT(*)' },
      { label: 'Underlying rows', source: 'mip.gold.borrower_360', value: 'borrower grain' },
      { label: 'Ownership graph', source: 'entity.owner_link', value: 'CLIP-grain' },
      { label: 'Tenant lens', source: 'mip.ref.lender_dictionary', value: 'gold refresh' },
    ],
  },

  populationMarketable: {
    description:
      'COUNT(*) over the headline metric view with the build criteria AND the governed contactability gate pushed down: opt-in consent, no suppression reason, not DNC, past the recontact date, and outside the frequency cap. That gate is what separates this count from the addressable population.',
    definition:
      'Borrowers in the build who also pass the governed contactability gate: opted in, not suppressed, not on do-not-contact, past the recontact date and outside the frequency cap.',
    signals: [
      { label: 'KPI measure', source: 'portfolio_headline_metric_view', value: 'COUNT(*)' },
      { label: 'Eligibility gate', source: 'borrower_360.marketing_eligible', value: 'TRUE' },
      { label: 'Consent', source: 'borrower_360.consent_status', value: 'opt_in' },
      { label: 'Suppression', source: 'borrower_360.suppression_reason', value: 'IS NULL' },
      { label: 'Do-not-contact', source: 'borrower_360.dnc', value: 'FALSE' },
    ],
  },

  equitySpreadPoints: {
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
    description: 'Gold Lead Queue rows: score >= 50, safe fields, segments, evidence, offer, rank, and approval.',
    signals: [
      { label: 'Floor', source: 'opportunity_score', value: '>= 50' },
      { label: 'Rank', source: 'lead_population.rank', value: 'score + CLIP' },
      { label: 'Evidence', source: 'lead_population.evidence_ids', value: 'audited' },
      { label: 'Approval', source: 'lead_population.approval_status', value: 'Lakebase' },
    ],
  },

  segmentPopulation: {
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
    description: 'CLIP property records with Owner Link, occupancy, and investor flags.',
    signals: [
      { label: 'Property identity', source: 'mip.silver.property_master.clip', value: 'masked CLIP ref' },
      { label: 'Owner link', source: 'property_owner_bridge.owner_link_id', value: 'masked ref' },
      { label: 'Investor', source: 'borrower_360.related_property_count', value: '2+ properties' },
    ],
  },

  householdRollup: {
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
    description:
      'CLIP geography, occupancy, owner type, mailing/situs, and foreclosure features.',
    signals: [
      { label: 'Property identity', source: 'property_master.clip', value: 'masked CLIP ref' },
      { label: 'Occupancy / mailing', source: 'property_master', value: 'safe flags' },
      { label: 'Distress', source: 'property_master.foreclosure_stage_code', value: 'when present' },
    ],
  },

  borrower360: {
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
    description:
      'Compares Cotality lien rate with the market-rate reference.',
    signals: [
      { label: 'Borrower rate', source: 'lien_current.first_pos_rate', value: 'borrower' },
      { label: 'Market rate', source: 'market_rates_weekly.rate_fraction', value: 'latest' },
      { label: 'Spread', source: 'fn_rate_spread', value: 'bps vs par' },
    ],
  },

  mortgageDomain: {
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
    description: 'Cotality transfer/sale events for recent-sale signals.',
    signals: [
      { label: 'Recent sale', source: 'evidence_events.signal_type', value: 'recent_sale' },
      { label: 'Sale date', source: 'owner_transfer_events.sale_date', value: 'event' },
    ],
  },

  evidenceStream: {
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
    description:
      'Non-PII readiness ledger for feed status.',
    signals: [
      { label: 'Status', source: 'source_readiness.status', value: 'live / roadmap / blocked' },
      { label: 'Rows', source: 'source_readiness.row_count', value: 'row proof' },
      { label: 'Checked at', source: 'source_readiness.checked_at', value: 'refresh timestamp' },
    ],
  },

  avm: {
    description: 'AVM value plus lien balance for equity, CLTV/LTV, and product fit.',
    signals: [
      { label: 'AVM value', source: 'lien_current.avm_value', value: 'borrower' },
      { label: 'Equity estimate', source: 'borrower_360.equity_estimate', value: 'AVM - lien' },
      { label: 'Equity %', source: 'borrower_360.equity_pct', value: 'AVM + lien' },
    ],
  },

  itm: {
    description:
      'Refi screen: rate >= 75 bps above market and equity >= 15%; not the full score.',
    definition:
      'Borrowers who clear the refinance economics screen: their lien rate sits far enough above the current market rate, with enough equity, under the governed fn_in_the_money rule.',
    signals: [
      { label: 'KPI measure', source: 'portfolio_headline_metric_view.in_the_money', value: 'SUM' },
      { label: 'Par refi rate', source: 'market_rates_weekly.market_rate_fraction', value: 'latest' },
      { label: 'Lien rate', source: 'voluntary_lien.current_rate', value: 'borrower' },
      { label: 'Rate spread', source: 'derived', value: 'lien minus par' },
      { label: 'Equity %', source: 'avm + lien balance', value: 'borrower' },
    ],
  },

  marketRate: {
    description:
      'Basis-point spread between lien rate and market refi reference.',
    signals: [
      { label: 'Market par rate', source: 'fred.MORTGAGE30US', value: 'latest' },
      { label: 'Borrower lien rate', source: 'voluntary_lien.current_rate', value: 'row' },
      { label: 'Spread (bps)', source: 'derived', value: 'lien - par x 100' },
    ],
  },

  rateWindow: {
    description:
      "Weekly FRED MORTGAGE30US print joined to today's fixed-rate book: the p25 / median / p75 note rates and the count in the money at each week's rate, using the same fn_rate_spread and fn_in_the_money primitives as scoring. The book is as-of the current refresh and applied to every week; this is not a portfolio history. Built by the gold refresh job, never per request.",
    signals: [
      { label: 'Weekly series', source: 'mip.gold.rate_window_weekly', value: 'precomputed per refresh, read as-is' },
      { label: 'Market rate', source: 'mip.silver.market_rates_weekly', value: 'FRED MORTGAGE30US, weekly' },
      { label: 'Book note rates', source: 'mip.silver.lien_current', value: 'fixed-rate first liens: p25 / median / p75' },
      { label: 'Book gates', source: 'mip.gold.borrower_360', value: 'active lien, equity, lender thresholds' },
      { label: 'Rate spread', source: 'mip.gold.fn_rate_spread', value: "note rate vs that week's print" },
      { label: 'In the money', source: 'mip.gold.fn_in_the_money', value: 'spread and equity, per week' },
      { label: 'Book as of', source: 'mip.ref.refresh_run_state', value: 'current refresh' },
    ],
  },

  rateSensitivity: {
    description:
      "Per state and par-rate step, the borrowers that clear this refresh's refi screen with fn_rate_spread and fn_in_the_money re-run at par plus the step. A scenario, not a forecast; rebuilt by the gold refresh, not on a schedule.",
    signals: [
      { label: 'Scenario grid', source: 'mip.gold.rate_sensitivity_rollup', value: 'state x step, per refresh' },
      { label: 'Book', source: 'mip.gold.borrower_360', value: 'state, equity, thresholds, base par' },
      { label: 'Note rate', source: 'mip.silver.lien_current', value: 'bounded, active liens only' },
      { label: 'Rule', source: 'mip.gold.fn_rate_spread + mip.gold.fn_in_the_money', value: 'at par + step' },
      { label: 'Contactable', source: 'eligibility predicate', value: 'live, per request' },
      { label: 'Book as of', source: 'mip.ref.refresh_run_state', value: 'current refresh' },
    ],
  },

  portfolioHeadlineView: {
    description:
      'Borrower-grain semantic view defining every home headline KPI: marketable population, refi economics screen, high opportunity, offers available, and primary offer paths.',
    definition:
      'One row per borrower in the refreshed book; every headline count is a count or sum over this view.',
    signals: [
      { label: 'Addressable population', source: 'portfolio_headline_metric_view', value: 'COUNT(*)' },
      { label: 'Refi economics screen', source: 'in_the_money', value: 'SUM' },
      { label: 'High opportunity', source: 'is_high_opportunity', value: 'fn_high_opportunity' },
      { label: 'Offers available', source: 'offer_available', value: 'non-null offer' },
      { label: 'Primary offer paths', source: 'offer_recommended', value: 'actionable lane' },
    ],
  },

  leadGenerationView: {
    description:
      'Semantic view for lead-generation KPIs, ranks, geography, score bands, and offer funnel.',
    signals: [
      { label: 'Population', source: 'lead_population', value: 'ranked grain' },
      { label: 'Score', source: 'lead_scores.opportunity_score', value: '0-100' },
    ],
  },

  segmentPerformanceView: {
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
    description: 'Borrower-level semantic view for Genie cohort, geography, score, and offer questions.',
    signals: [
      { label: 'Borrower grain', source: 'borrower_360.borrower_id', value: 'masked id' },
      { label: 'Primary offer', source: 'borrower_360.recommended_offer_code', value: 'offer path' },
      { label: 'Evidence ids', source: 'borrower_360.evidence_ids', value: 'evidence refs' },
    ],
  },

  leadScore: {
    description:
      '0-100 borrower score from economics, intent, fit, relationship, and evidence.',
    definition:
      'Borrowers whose opportunity score clears the governed high-opportunity threshold (fn_high_opportunity).',
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
    description:
      'Gold cohort for rate-lock and refi-sensitivity questions.',
    signals: [
      { label: 'Current rate', source: 'borrower_360.current_rate', value: 'borrower' },
      { label: 'Market rate', source: 'market_rates_weekly.market_rate_fraction', value: 'latest' },
      { label: 'Rate spread', source: 'derived', value: 'basis points' },
    ],
  },

  funnelSnapshot: {
    description:
      'Daily state and segment funnel counts combining borrower economics with the scheduled UC mirror of operational lifecycle state.',
    signals: [
      { label: 'Population', source: 'mip.gold.borrower_360', value: 'state + segment' },
      { label: 'Decision state', source: 'mip.gold.borrower_lifecycle_state', value: 'scheduled UC mirror' },
      { label: 'Daily counts', source: 'mip.gold.funnel_snapshot_daily', value: 'snapshot grain' },
    ],
  },

  countyRollup: {
    description:
      'County-grain addressable population, economics, score, and dominant-segment facts derived from Borrower 360.',
    signals: [
      { label: 'County key', source: 'mip.silver.property_master', value: '5-character FIPS' },
      { label: 'Population', source: 'mip.gold.borrower_360', value: 'addressable borrowers' },
      { label: 'Map grain', source: 'mip.gold.county_rollup', value: 'county + snapshot date' },
    ],
  },

  zipRollup: {
    description:
      'ZIP-grain addressable population, score, dominant segment, and stable sample borrower derived from Borrower 360.',
    signals: [
      { label: 'ZIP key', source: 'mip.silver.property_master', value: '5-digit ZIP' },
      { label: 'Population', source: 'mip.gold.borrower_360', value: 'addressable borrowers' },
      { label: 'Map grain', source: 'mip.gold.zip_rollup', value: 'state + county + ZIP' },
    ],
  },

  nbo: {
    description:
      'Chooses one offer path from current signals: purchase, refi/equity review, or nurture.',
    definition:
      'Borrowers the reviewed offer rules (fn_next_best_offer) give an actionable primary offer path.',
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
    description:
      'Cotality HELOC propensity feed for HELOC Intent; not a filed permit.',
    signals: [
      { label: 'Score', source: 'heloc_propensity_score', value: '0-999' },
      { label: 'Trigger', source: 'has_heloc_propensity_trigger', value: '>= 700' },
      { label: 'Run date', source: 'heloc_propensity_run_date', value: 'model run' },
    ],
  },

  refiPropensity: {
    description:
      'Cotality refi propensity feed; supplements rate-spread economics.',
    signals: [
      { label: 'Score', source: 'refi_propensity_score', value: '0-999' },
      { label: 'Trigger', source: 'has_refi_propensity_trigger', value: '>= 700' },
      { label: 'Run date', source: 'refi_propensity_run_date', value: 'model run' },
    ],
  },

  loanProductType: {
    description: 'Derived from first-position loan type plus the governed jumbo limit.',
    signals: [
      { label: 'Loan type', source: 'lien_current.first_pos_loan_type', value: 'CNV / FHA / VA' },
      { label: 'Original amount', source: 'lien_current.first_pos_amount', value: 'vs limit' },
      { label: 'Conforming limit', source: 'mip.ref.offer_rules_config', value: 'mip_conforming_loan_limit_usd' },
    ],
  },

  originationChannel: {
    description: 'Most recent funded LOS application channel.',
    signals: [
      { label: 'Channel', source: 'loan_applications.application_channel', value: 'funded' },
      { label: 'Unknown', source: 'borrower_360.origination_channel', value: 'NULL' },
    ],
  },

  permit: {
    description:
      'Filed permit rows are pending; has_permit stays false until a governed table exists.',
    signals: [
      { label: 'Readiness', source: 'mip.gold.source_readiness', value: 'roadmap' },
      { label: 'Permit flag', source: 'mip.gold.borrower_360.has_permit', value: 'filed only' },
      { label: 'HELOC intent', source: 'mip.silver.heloc_propensity', value: 'separate' },
    ],
  },

  mls: {
    description:
      'Cotality MLS listings joined to CLIP; active/under-contract rows drive purchase intent.',
    signals: [
      { label: 'Readiness', source: 'mip.gold.source_readiness', value: 'live' },
      { label: 'listed_for_sale', source: 'mip.gold.borrower_360', value: 'active/contract' },
      { label: 'Listing evidence', source: 'mip.gold.evidence_events', value: 'signal_type = listing' },
    ],
  },

  assignmentOverlay: {
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
    description: 'Cost-per-contact and projected conversion assumptions, set per lender in campaign config.',
    signals: [],
  },
} satisfies Record<DrawerSourceKey, DrawerSourceProse>;

export type DrawerSourceProseTable = Readonly<Record<string, DrawerSourceProse>>;

const hasOwn = (target: object, key: string): boolean => Object.prototype.hasOwnProperty.call(target, key);

/**
 * A source with its registry prose resolved by `registryKey`:
 *  - description: the source's own when it HAS the key (an explicit
 *    `undefined` included), else the registry's;
 *  - signals: the source's own first, then the registry's;
 *  - usedIn / definition: the source's own, else the registry's.
 * A source without a (known) registry key is returned as is.
 */
export function resolveDrawerProse(
  source: DrawerSource,
  prose: DrawerSourceProseTable = DRAWER_SOURCE_PROSE,
): DrawerSource {
  const key = source.registryKey;
  const entry = key !== undefined && hasOwn(prose, key) ? prose[key] : undefined;
  if (!entry) return source;
  const resolved: DrawerSource = { ...source };
  const description = hasOwn(source, 'description') ? source.description : entry.description;
  if (description !== undefined || hasOwn(source, 'description')) resolved.description = description;
  if (source.signals !== undefined || entry.signals !== undefined) {
    resolved.signals = [...(source.signals ?? []), ...(entry.signals ?? [])];
  }
  const usedIn = source.usedIn ?? entry.usedIn;
  if (usedIn !== undefined) resolved.usedIn = usedIn;
  const definition = source.definition ?? entry.definition;
  if (definition !== undefined) resolved.definition = definition;
  return resolved;
}
