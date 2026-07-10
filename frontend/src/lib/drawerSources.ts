import type { DrawerSource } from '../components/AppContext';
import type { SegmentSummary } from '../types';

const ASSET_KEYS_BY_SOURCE: Record<string, string> = {
  'mip.gold.lead_population': 'lead_population',
  'mip.gold.segment_population': 'segment_population',
  'mip.gold.lead_scores': 'lead_scores',
  'mip.gold.borrower_360': 'borrower_360',
  'mip.gold.borrower_dossier': 'borrower_dossier',
  'mip.gold.evidence_events': 'evidence_events',
  'mip.gold.household_rollup': 'household_rollup',
  'mip.gold.source_readiness': 'source_readiness',
  'mip.gold.lockin_cohort': 'lockin_cohort',
  'mip.gold.funnel_snapshot_daily': 'funnel_snapshot_daily',
  'mip.gold.county_rollup': 'county_rollup',
  'mip.gold.zip_rollup': 'zip_rollup',
  'mip.silver.listing_activity': 'listing_activity',
  'mip.silver.heloc_propensity': 'heloc_propensity',
  'mip.silver.refi_propensity': 'refi_propensity',
  'mip.semantics.lead_generation_metric_view': 'lead_generation_metric_view',
  'mip.semantics.segment_performance_metric_view': 'segment_performance_metric_view',
  'mip.semantics.borrower_opportunity_metric_view': 'borrower_opportunity_metric_view',
  'mip.ref.offer_rules_config': 'offer_rules_config',
  'mip.ref.lender_dictionary': 'lender_dictionary',
};

export function assetKeyForSource(rawSource?: string | null): string | null {
  if (!rawSource) return null;
  const key = rawSource.trim().replace(/`/g, '').toLowerCase();
  if (ASSET_KEYS_BY_SOURCE[key]) return ASSET_KEYS_BY_SOURCE[key];
  const parts = key.split('.');
  const tail = parts[parts.length - 1];
  return ASSET_KEYS_BY_SOURCE[`mip.gold.${tail}`] ??
    ASSET_KEYS_BY_SOURCE[`mip.semantics.${tail}`] ??
    ASSET_KEYS_BY_SOURCE[`mip.ref.${tail}`] ??
    null;
}

export function assetHrefForSource(rawSource?: string | null): string | null {
  const assetKey = assetKeyForSource(rawSource);
  return assetKey ? assetDetailHref(assetKey) : null;
}

export function assetDetailHref(assetKey: string): string {
  return `/data-estate/assets/${encodeURIComponent(assetKey)}`;
}

function enrichAsset(source: DrawerSource): DrawerSource {
  if (source.assetKey) return source;
  const assetKey = source.assetKey ?? assetKeyForSource(source.assetPath ?? source.short);
  return assetKey ? { ...source, assetKey } : source;
}

/**
 * Route a raw UC source (e.g. `mip.gold.fn_in_the_money`, `cotality.permits.building`)
 * to the matching DRAWER_SOURCES entry. Falls back to a neutral descriptor that
 * still opens the drawer with the raw path so presenters see lineage rather
 * than a silently-mismatched chip.
 *
 * Keep this in sync with backend/services/scoring.source_display_label and
 * backend/api/offers._sources_for: the app should not tell a different lineage
 * story from the API payload that supplied the evidence.
 */
export function descriptorFor(rawSource: string): DrawerSource {
  return drawerForAsset(rawSource) ?? {
    title: rawSource,
    short: rawSource.split('.').pop() ?? rawSource,
    description:
      `Unity Catalog object: ${rawSource}. No curated drawer mapping exists yet; showing the exact source rather than routing to a mismatched drawer.`,
    lineage: [{ layer: 'UC', name: rawSource }],
    signals: [],
  };
}

export function drawerForAsset(rawSource: string): DrawerSource | null {
  const key = rawSource.toLowerCase();

  if (key.includes('fn_rate_spread')) return enrichAsset(DRAWER_SOURCES.marketRate);
  if (key.includes('fn_estimated_upb')) return enrichAsset(DRAWER_SOURCES.lien);
  if (key.includes('rate_spread')) return enrichAsset(DRAWER_SOURCES.rateSpread);
  if (key.includes('fn_in_the_money') || key.includes('itm')) return enrichAsset(DRAWER_SOURCES.itm);
  if (key.includes('fn_lead_score') || key.includes('lead_scores')) return enrichAsset(DRAWER_SOURCES.leadScore);
  if (key.includes('fn_next_best_offer') || key.includes('nbo')) return enrichAsset(DRAWER_SOURCES.nbo);
  if (key.includes('segment_performance_metric_view')) return enrichAsset(DRAWER_SOURCES.segmentPerformanceView);
  if (key.includes('borrower_opportunity_metric_view')) return enrichAsset(DRAWER_SOURCES.borrowerOpportunityView);
  if (key.includes('lead_generation_metric_view')) return enrichAsset(DRAWER_SOURCES.leadGenerationView);
  if (key.includes('lead_population')) return enrichAsset(DRAWER_SOURCES.leadPopulation);
  if (key.includes('segment_population')) return enrichAsset(DRAWER_SOURCES.segmentPopulation);
  if (key.includes('borrower_dossier')) return enrichAsset(DRAWER_SOURCES.borrowerDossier);
  if (key.includes('household_rollup')) return enrichAsset(DRAWER_SOURCES.householdRollup);
  if (key.includes('borrower_360')) return enrichAsset(DRAWER_SOURCES.borrower360);
  if (key.includes('evidence_events')) return enrichAsset(DRAWER_SOURCES.evidenceStream);
  if (key.includes('source_readiness')) return enrichAsset(DRAWER_SOURCES.sourceReadiness);
  if (key.includes('lockin_cohort')) return enrichAsset(DRAWER_SOURCES.lockinCohort);
  if (key.includes('property_owner_bridge') || key.includes('owner_link')) return DRAWER_SOURCES.ownerGraph;
  if (key.includes('property_master')) return DRAWER_SOURCES.propertyProfile;
  if (key.includes('mortgage_events') || key.includes('mortgage_domain')) return DRAWER_SOURCES.mortgageDomain;
  if (key.includes('owner_transfer_events') || key.includes('owner_transfer')) return DRAWER_SOURCES.ownerTransfer;
  if (key.includes('avm')) return DRAWER_SOURCES.avm;
  if (key.includes('lien_current') || key.includes('voluntary_lien')) return DRAWER_SOURCES.lien;
  if (key.includes('heloc_propensity')) return enrichAsset(DRAWER_SOURCES.helocPropensity);
  if (key.includes('refi_propensity')) return enrichAsset(DRAWER_SOURCES.refiPropensity);
  if (key.includes('mls') || key.includes('listing')) return DRAWER_SOURCES.mls;
  if (key.includes('permit')) return DRAWER_SOURCES.permit;
  if (key.includes('population') || key.includes('public_records')) return DRAWER_SOURCES.population;
  return null;
}

function withEventDate(source: DrawerSource, eventDate?: string): DrawerSource {
  return eventDate ? { ...source, eventDate } : source;
}

/**
 * Evidence events carry both a source table and a source product. The table can
 * be shared by multiple primitives (for example, `mip.silver.lien_current`
 * feeds both Voluntary Lien and AVM equity), so source-table-only routing can
 * collapse distinct evidence chips into one drawer. Prefer product/signal when
 * available and fall back to the raw UC object for unknown sources.
 */
export function descriptorForEvidence(event: {
  source_product?: string;
  source_table?: string;
  signal_type?: string;
  timestamp?: string;
}): DrawerSource {
  const product = (event.source_product ?? '').toLowerCase();
  const signal = (event.signal_type ?? '').toLowerCase();
  const table = event.source_table ?? '';

  if (signal.includes('rate_spread')) {
    return withEventDate(DRAWER_SOURCES.rateSpread, event.timestamp);
  }
  if (product.includes('mortgage market') || signal.includes('market_trend')) {
    return withEventDate(DRAWER_SOURCES.marketRate, event.timestamp);
  }
  if (product.includes('avm') || signal.includes('equity')) {
    return withEventDate(DRAWER_SOURCES.avm, event.timestamp);
  }
  if (
    product.includes('voluntary lien') ||
    product.includes('lien') ||
    signal.includes('lien')
  ) {
    return withEventDate(DRAWER_SOURCES.lien, event.timestamp);
  }
  if (
    product.includes('owner link') ||
    signal.includes('multi_property') ||
    signal.includes('owner_link') ||
    signal.includes('related_property')
  ) {
    return withEventDate(DRAWER_SOURCES.ownerGraph, event.timestamp);
  }
  if (
    product === 'property' ||
    signal.includes('absentee') ||
    signal.includes('corporate_owner') ||
    signal.includes('foreclosure')
  ) {
    return withEventDate(DRAWER_SOURCES.propertyProfile, event.timestamp);
  }
  if (product.includes('mortgage domain') || signal.includes('recent_refi') || signal.includes('recent_payoff')) {
    return withEventDate(DRAWER_SOURCES.mortgageDomain, event.timestamp);
  }
  if (product.includes('owner transfer') || signal.includes('recent_sale')) {
    return withEventDate(DRAWER_SOURCES.ownerTransfer, event.timestamp);
  }
  if (signal.includes('heloc_propensity') || product.includes('heloc propensity')) {
    return withEventDate(DRAWER_SOURCES.helocPropensity, event.timestamp);
  }
  if (signal.includes('refi_propensity') || product.includes('refi propensity')) {
    return withEventDate(DRAWER_SOURCES.refiPropensity, event.timestamp);
  }
  if (signal.includes('listing') || product.includes('mls')) return withEventDate(DRAWER_SOURCES.mls, event.timestamp);
  if (signal.includes('permit') || product.includes('permit')) return withEventDate(DRAWER_SOURCES.permit, event.timestamp);

  return withEventDate(descriptorFor(table), event.timestamp);
}

/**
 * UI metadata describing each evidence-drawer entry. This is not borrower data;
 * it is the product-truth contract for what each clickable source means.
 */
export const DRAWER_SOURCES: Record<string, DrawerSource> = {
  population: {
    title: 'Marketable population',
    short: 'Marketable population',
    // Governed anchor (2026-06-11): the marketable-population KPI is
    // COUNT(*) over mip.gold.borrower_360, so this drawer reads that
    // asset's governed metadata. Without an anchor the hero KPI's drawer
    // showed "Freshness unavailable", implying a data gap that wasn't real.
    assetKey: 'borrower_360',
    assetPath: 'mip.gold.borrower_360',
    description:
      'Deed and mortgage records joined to voluntary liens and the Owner Link graph, filtered by the lender configuration.',
    lineage: [
      { layer: 'SOURCE', name: 'cotality.public_records.deed_and_mortgage', meta: 'Delta Share - nationwide' },
      { layer: 'SOURCE', name: 'cotality.liens.voluntary_lien', meta: 'Delta Share - nationwide' },
      { layer: 'ENTITY', name: 'entity.property_clip', meta: 'Mastered via CLIP' },
      { layer: 'ENTITY', name: 'entity.owner_link', meta: 'Mastered via Owner Link' },
      { layer: 'SEMANTIC', name: 'metrics.borrower_universe', meta: 'UC metric view' },
    ],
    signals: [
      { label: 'Borrower universe', source: 'metrics.borrower_universe', value: 'live count' },
      { label: 'Ownership graph', source: 'entity.owner_link', value: 'CLIP-grain' },
      { label: 'Configured tenant lens', source: 'mip.ref.lender_dictionary', value: 'applied during gold refresh' },
    ],
  },

  leadPopulation: {
    title: 'Ranked lead population',
    short: 'Ranked lead population',
    assetKey: 'lead_population',
    assetPath: 'mip.gold.lead_population',
    usedIn: ['Lead Queue', 'Borrower 360', 'Offers', 'Analytics'],
    notExposed: 'Raw CLIP, owner names, street addresses, source paths, and raw lender strings.',
    description:
      'Gold Lead Queue population: borrower_360 rows with score >= 50, safe fields, segment codes, evidence IDs, offer path, rank, and approval state.',
    lineage: [
      { layer: 'FEATURES', name: 'mip.gold.borrower_360', meta: 'borrower-grain features' },
      { layer: 'SCORE', name: 'mip.gold.lead_scores', meta: 'opportunity_score components' },
      { layer: 'GOLD', name: 'mip.gold.lead_population', meta: 'opportunity_score >= 50, ranked by score' },
      { layer: 'APP', name: 'Lead Queue / approval workflow', meta: 'evidence_ids and approval_status preserved' },
    ],
    signals: [
      { label: 'Floor', source: 'opportunity_score', value: '>= 50' },
      { label: 'Rank', source: 'lead_population.rank', value: 'score + CLIP' },
      { label: 'Evidence', source: 'lead_population.evidence_ids', value: 'audited' },
      { label: 'Approval', source: 'lead_population.approval_status', value: 'Lakebase' },
    ],
  },

  segmentPopulation: {
    title: 'Segment population',
    short: 'Segment population',
    assetKey: 'segment_population',
    assetPath: 'mip.gold.segment_population',
    usedIn: ['Segments', 'Analytics', 'Genie'],
    description:
      'Gold segment rollup: predicate, mode, geography, count, and average score.',
    lineage: [
      { layer: 'FEATURES', name: 'mip.gold.borrower_360', meta: 'borrower-grain segment flags' },
      { layer: 'GOLD', name: 'mip.gold.segment_population', meta: 'count and average score' },
      { layer: 'SEMANTIC', name: 'mip.semantics.segment_performance_metric_view', meta: 'Genie/reporting surface' },
    ],
    signals: [
      { label: 'Segment count', source: 'segment_population.count', value: 'matching borrowers' },
      { label: 'Average score', source: 'segment_population.avg_score', value: 'average' },
      { label: 'Listing segment', source: 'borrower_360.listed_for_sale', value: 'live from Cotality MLS' },
      { label: 'HELOC intent segment', source: 'borrower_360.has_heloc_propensity_trigger', value: 'live from Cotality propensity' },
    ],
  },

  ownerGraph: {
    title: 'Property + owner graph',
    short: 'Property + owner graph',
    description:
      'Property records mastered to CLIP and connected through Owner Link so the app can identify ownership relationships, related properties, occupancy posture, and investor patterns.',
    lineage: [
      { layer: 'SOURCE', name: 'cotality.public_records.deed_and_mortgage', meta: 'Delta Share - property and ownership attributes' },
      { layer: 'ENTITY', name: 'mip.silver.property_master', meta: 'CLIP-grain property master' },
      { layer: 'ENTITY', name: 'mip.gold.property_owner_bridge', meta: 'Owner Link related-property rollup' },
      { layer: 'FEATURES', name: 'mip.gold.borrower_360', meta: 'related_property_count, occupancy, investor flags' },
    ],
    signals: [
      { label: 'Property identity', source: 'mip.silver.property_master.clip', value: 'masked CLIP ref' },
      { label: 'Owner relationship', source: 'mip.gold.property_owner_bridge.owner_link_id', value: 'masked Owner Link ref' },
      { label: 'Investor signal', source: 'borrower_360.related_property_count', value: '2+ related properties' },
    ],
  },

  householdRollup: {
    title: 'Household rollup',
    short: 'Household rollup',
    assetKey: 'household_rollup',
    assetPath: 'mip.gold.household_rollup',
    usedIn: ['Portfolio Builder campaign summary'],
    notExposed: 'Raw CLIPs, Owner Links, owner names, mailing street addresses, emails, phones, and destination send paths.',
    description:
      'Opt-in campaign-time household grouping. Borrower remains the default unit; this rollup supplies one eligible primary contact per household only when the campaign builder enables household dedup.',
    lineage: [
      { layer: 'SILVER', name: 'mip.silver.property_owners', meta: 'S1.1 owner slots and shared Owner Links' },
      { layer: 'SILVER', name: 'mip.silver.property_master', meta: 'mailing city/state heuristic; no mailing street address' },
      { layer: 'FEATURES', name: 'mip.gold.borrower_360', meta: 'synthetic borrower ids, score, marketing eligibility' },
      { layer: 'GOLD', name: 'mip.gold.household_rollup', meta: 'household id, eligible primary rank, suppression count source' },
    ],
    signals: [
      { label: 'Default unit', source: 'campaign.household_dedup.enabled', value: 'borrower unless explicitly enabled' },
      { label: 'Primary contact', source: 'household_rollup.household_rank', value: 'eligible first, score desc, borrower id asc' },
      { label: 'Suppressed co-owners', source: 'household_rollup.suppressed_by_household_dedup', value: 'counted in campaign summary' },
      { label: 'Derivation evidence', source: 'household_rollup.derivation_source_tables', value: 'UC row lineage' },
    ],
  },

  propertyProfile: {
    title: 'Property profile',
    short: 'Property profile',
    description:
      'CLIP-grain property attributes used for geography, occupancy posture, corporate-owner signals, mailing/situs mismatch, and foreclosure-stage snapshots. Raw owner and address identifiers are masked before API response.',
    lineage: [
      { layer: 'SOURCE', name: 'cotality.public_records.property', meta: 'property and ownership attributes' },
      { layer: 'SILVER', name: 'mip.silver.property_master', meta: 'CLIP-grain property profile' },
      { layer: 'FEATURES', name: 'mip.gold.borrower_360', meta: 'occupancy, geography, fit and distress flags' },
      { layer: 'SEMANTIC', name: 'mip.gold.evidence_events', meta: 'absentee_mailing, corporate_owner, foreclosure_stage evidence rows' },
    ],
    signals: [
      { label: 'Property identity', source: 'property_master.clip', value: 'masked CLIP ref' },
      { label: 'Occupancy / mailing', source: 'property_master', value: 'display-safe flags' },
      { label: 'Distress snapshot', source: 'property_master.foreclosure_stage_code', value: 'when present' },
    ],
  },

  borrower360: {
    title: 'Borrower 360 feature set',
    short: 'Borrower 360 feature set',
    assetKey: 'borrower_360',
    assetPath: 'mip.gold.borrower_360',
    usedIn: ['Borrower 360', 'Offer workflow', 'Analytics', 'Proof layer'],
    notExposed: 'Raw CLIP, owner identity, street address, raw lender values, and upstream source paths.',
    description:
      'Canonical borrower-level feature table used by the dossier, offer logic, lead queue, and Genie trusted assets. It combines property, Owner Link, lien, AVM, market-rate, and first-party relationship fields at borrower grain.',
    lineage: [
      { layer: 'SILVER', name: 'mip.silver.property_master', meta: 'property and geography attributes' },
      { layer: 'SILVER', name: 'mip.silver.lien_current', meta: 'current-lien and AVM attributes' },
      { layer: 'GOLD', name: 'mip.gold.property_owner_bridge', meta: 'related property and owner graph features' },
      { layer: 'SOURCE', name: 'mip.silver.market_rates_weekly', meta: 'market-rate comparison feed' },
      { layer: 'FEATURES', name: 'mip.gold.borrower_360', meta: 'borrower-grain feature projection' },
    ],
    signals: [
      { label: 'Property ref', source: 'property_master.clip', value: 'masked CLIP ref' },
      { label: 'Owner graph ref', source: 'property_owner_bridge.owner_link_id', value: 'masked Owner Link ref' },
      { label: 'Borrower economics', source: 'lien_current + market_rates_weekly', value: 'rate spread, LTV, equity' },
      { label: 'First-party lens', source: 'demo first-party feeds when configured', value: 'relationship flags' },
    ],
  },

  borrowerDossier: {
    title: 'Borrower dossier proof table',
    short: 'Borrower dossier proof table',
    assetKey: 'borrower_dossier',
    assetPath: 'mip.gold.borrower_dossier',
    usedIn: ['Borrower 360 proof drawer', 'Offer workflow', 'Reproduce SQL'],
    notExposed: 'Raw CLIP, owner identity, street address, raw lender values, upstream source paths, and nested source-table names.',
    description:
      'Governed borrower-proof table used to reconcile the displayed dossier, score math, decision inputs, evidence rows, and reproduce-SQL templates for a single borrower.',
    lineage: [
      { layer: 'FEATURES', name: 'mip.gold.borrower_360', meta: 'borrower-grain feature projection' },
      { layer: 'SCORE', name: 'mip.gold.lead_scores', meta: 'opportunity score and signal-strength components' },
      { layer: 'EVIDENCE', name: 'mip.gold.evidence_events', meta: 'display-safe evidence rows' },
      { layer: 'GOLD', name: 'mip.gold.borrower_dossier', meta: 'proof-ready borrower dossier projection' },
    ],
    signals: [
      { label: 'Displayed dossier', source: 'borrower_dossier.borrower_id', value: 'masked borrower id' },
      { label: 'Score reconciliation', source: 'borrower_dossier.opportunity_score', value: 'compared with fn_lead_score' },
      { label: 'Decision inputs', source: 'borrower_dossier.rate_spread_bps / equity_pct', value: 'recomputed in proof' },
      { label: 'Evidence rows', source: 'borrower_dossier.evidence_events', value: 'nested fields redacted where sensitive' },
    ],
  },

  lien: {
    title: 'Voluntary lien',
    short: 'Voluntary lien',
    description:
      'Current open-lien status, lien balance, lender reference, lien rate, and lien-derived relationship flags. This is the lien evidence behind rate spread, current-customer, competitor-lien, and open-lien filters.',
    lineage: [
      {
        layer: 'SOURCE',
        name: 'cotality.voluntary_lien_status_marketing',
        meta: 'Cotality Delta Share',
      },
      { layer: 'SILVER', name: 'mip.silver.lien_current', meta: 'current lien snapshot' },
      { layer: 'PRIMITIVE', name: 'mip.gold.fn_estimated_upb', meta: 'UC SQL parity-pinned to scoring.py' },
      { layer: 'FEATURES', name: 'mip.gold.borrower_360', meta: 'current_lien_balance, current_rate, lender relationship' },
      { layer: 'SEMANTIC', name: 'mip.gold.evidence_events', meta: 'rate_spread and competitor_lien evidence rows' },
    ],
    signals: [
      { label: 'Lien rate', source: 'lien_current.first_pos_rate', value: 'per borrower' },
      { label: 'Estimated UPB', source: 'fn_estimated_upb', value: 'amortized from original UPB' },
      { label: 'Lender relationship', source: 'mip.ref.lender_dictionary', value: 'tenant / competitor / other' },
    ],
  },

  rateSpread: {
    title: 'Rate spread evidence',
    short: 'Rate spread evidence',
    description:
      'Derived evidence comparing the borrower current lien rate from Cotality voluntary lien data with the current market-rate reference. This chip is not Cotality-only; it combines lien data with the market-rate feed.',
    lineage: [
      {
        layer: 'SOURCE',
        name: 'cotality.voluntary_lien_status_marketing',
        meta: 'borrower current lien rate',
      },
      {
        layer: 'SOURCE',
        name: 'MORTGAGE30US market-rate feed',
        meta: 'market-rate comparison input',
      },
      { layer: 'SILVER', name: 'mip.silver.lien_current', meta: 'first_pos_rate' },
      { layer: 'SILVER', name: 'mip.silver.market_rates_weekly', meta: 'latest par-rate observation' },
      { layer: 'SEMANTIC', name: 'mip.gold.evidence_events', meta: 'rate_spread evidence rows' },
    ],
    signals: [
      { label: 'Borrower rate', source: 'lien_current.first_pos_rate', value: 'per borrower' },
      { label: 'Market rate', source: 'market_rates_weekly.rate_fraction', value: 'latest MORTGAGE30US' },
      { label: 'Spread', source: 'fn_rate_spread', value: 'basis points vs par' },
    ],
  },

  mortgageDomain: {
    title: 'Mortgage Domain events',
    short: 'Mortgage Domain events',
    description:
      'Cotality mortgage transaction history used for recent refinance, payoff, release, and lifecycle signals. These are event-backed signals and do not expose borrower names or raw property identifiers in the app.',
    lineage: [
      { layer: 'SOURCE', name: 'cotality.mortgage_domain', meta: 'Cotality Delta Share transaction events' },
      { layer: 'SILVER', name: 'mip.silver.mortgage_events', meta: 'event-grain mortgage history' },
      { layer: 'SEMANTIC', name: 'mip.gold.evidence_events', meta: 'recent_refi and recent_payoff evidence rows' },
      { layer: 'FEATURES', name: 'mip.gold.borrower_360', meta: 'relationship and intent-trigger features' },
    ],
    signals: [
      { label: 'Recent refinance', source: 'evidence_events.signal_type', value: 'recent_refi' },
      { label: 'Recent payoff', source: 'evidence_events.signal_type', value: 'recent_payoff' },
      { label: 'Refi event date', source: 'mortgage_events.event_date', value: 'per event' },
      { label: 'Payoff release date', source: 'mortgage_events.release_date', value: 'per event' },
    ],
  },

  ownerTransfer: {
    title: 'Owner Transfer events',
    short: 'Owner Transfer events',
    description:
      'Cotality owner-transfer and sale events used to identify recent sale activity and ownership lifecycle changes. The app surfaces sanitized event evidence only.',
    lineage: [
      { layer: 'SOURCE', name: 'cotality.owner_transfer_and_sales', meta: 'Cotality Delta Share transfer events' },
      { layer: 'SILVER', name: 'mip.silver.owner_transfer_events', meta: 'event-grain owner transfer history' },
      { layer: 'SEMANTIC', name: 'mip.gold.evidence_events', meta: 'recent_sale evidence rows' },
      { layer: 'FEATURES', name: 'mip.gold.borrower_360', meta: 'purchase-intent and lifecycle context' },
    ],
    signals: [
      { label: 'Recent sale', source: 'evidence_events.signal_type', value: 'recent_sale' },
      { label: 'Sale date', source: 'owner_transfer_events.sale_date', value: 'per event' },
    ],
  },

  evidenceStream: {
    title: 'Evidence stream',
    short: 'Evidence stream',
    assetKey: 'evidence_events',
    assetPath: 'mip.gold.evidence_events',
    usedIn: ['Borrower proof', 'Lineage drawers', 'Genie citations', 'Audit review'],
    notExposed: 'Raw CLIP and source table internals are removed at the API boundary.',
    description:
      'Borrower-safe evidence-event table. Each row is a source-backed signal with source product, UC table, signal type, display text, evidence confidence, and timestamp. Raw CLIP is used only for joins and is removed at the API boundary.',
    lineage: [
      { layer: 'SILVER', name: 'mip.silver.lien_current', meta: 'rate, lien and AVM evidence' },
      { layer: 'SILVER', name: 'mip.silver.market_rates_weekly', meta: 'FRED market-rate observation for market_trend evidence' },
      { layer: 'SILVER', name: 'mip.silver.mortgage_events', meta: 'mortgage lifecycle events' },
      { layer: 'SILVER', name: 'mip.silver.owner_transfer_events', meta: 'sale / transfer events' },
      { layer: 'GOLD', name: 'mip.gold.property_owner_bridge', meta: 'Owner Link evidence' },
      { layer: 'GOLD', name: 'mip.gold.evidence_events', meta: 'sanitized evidence stream' },
    ],
    signals: [
      {
        label: 'Controlled vocab',
        source: 'evidence_events.signal_type',
        value: 'listing, HELOC propensity, refi propensity live; permit reserved',
      },
      { label: 'Display text', source: 'evidence_events.display_text', value: 'deterministic, no PII' },
      { label: 'Evidence confidence', source: 'evidence_events.confidence', value: '0..1 per signal' },
    ],
  },

  sourceReadiness: {
    title: 'Source readiness',
    short: 'Source readiness',
    assetKey: 'source_readiness',
    assetPath: 'mip.gold.source_readiness',
    usedIn: ['Data estate', 'Admin source readiness', 'Genie data-gap answers'],
    description:
      'Non-PII readiness ledger showing which Cotality, FRED, first-party, and gold assets are live, synthetic-demo, pending, empty, or blocked. Used for governed data-gap answers so missing feeds are not treated as zero demand.',
    lineage: [
      { layer: 'GOLD', name: 'mip.gold.source_readiness', meta: 'source status summary' },
      { layer: 'SOURCE', name: 'Cotality MLS/Listings', meta: 'live Delta Share' },
      { layer: 'SOURCE', name: 'Cotality HELOC Propensity', meta: 'live model score feed' },
      { layer: 'SOURCE', name: 'Cotality Refi Propensity', meta: 'live model score feed' },
      { layer: 'SOURCE', name: 'Cotality Building Permits', meta: 'roadmap: true filed permits not yet present' },
      { layer: 'SOURCE', name: 'FRED MORTGAGE30US', meta: 'weekly market-rate feed' },
      { layer: 'FIRST PARTY', name: 'Summit demo feeds', meta: 'synthetic-demo lender data' },
    ],
    signals: [
      { label: 'Status', source: 'source_readiness.status', value: 'live / roadmap / demo_synthetic / blocked' },
      { label: 'Rows', source: 'source_readiness.row_count', value: 'source-specific row proof' },
      { label: 'Checked at', source: 'source_readiness.checked_at', value: 'refresh timestamp' },
    ],
  },

  avm: {
    title: 'AVM equity',
    short: 'AVM equity',
    description:
      'AVM-backed property value and lien-balance math used to estimate borrower equity, CLTV/LTV, and the equity leg of refinance and HELOC eligibility.',
    lineage: [
      { layer: 'SOURCE', name: 'cotality.lien_status_marketing.avm_fields', meta: 'estimated value and confidence fields' },
      { layer: 'SILVER', name: 'mip.silver.lien_current', meta: 'avm_value, confidence, original UPB, note rate' },
      { layer: 'PRIMITIVE', name: 'mip.gold.fn_estimated_upb', meta: 'amortized current lien estimate' },
      { layer: 'FEATURES', name: 'mip.gold.borrower_360', meta: 'equity_estimate, equity_pct, ltv' },
      { layer: 'SEMANTIC', name: 'mip.gold.evidence_events', meta: 'equity evidence rows' },
    ],
    signals: [
      { label: 'AVM value', source: 'lien_current.avm_value', value: 'per borrower' },
      { label: 'Equity estimate', source: 'borrower_360.equity_estimate', value: 'AVM minus estimated current lien' },
      { label: 'Equity %', source: 'borrower_360.equity_pct', value: 'AVM and estimated current lien' },
    ],
  },

  itm: {
    title: 'Refinance economics screen',
    short: 'Rate + equity screen',
    // Governed anchor (2026-06-11): the high-intent KPI sums the
    // in_the_money column on mip.gold.borrower_360 — same rationale as
    // the population entry above.
    assetKey: 'borrower_360',
    assetPath: 'mip.gold.borrower_360',
    description:
      'Checks whether a borrower appears to have enough refinance incentive: current lien rate at least 75 bps above the market reference rate and equity at least 15%. This is only the refi-economics screen, not the full lead score.',
    lineage: [
      { layer: 'SOURCE', name: 'mip.silver.market_rates_weekly', meta: 'FRED MORTGAGE30US weekly snapshot' },
      { layer: 'SOURCE', name: 'cotality.avm.current', meta: 'Property value snapshot' },
      { layer: 'SOURCE', name: 'cotality.liens.voluntary_lien', meta: 'Current lien rate' },
      { layer: 'PRIMITIVE', name: 'mip.gold.fn_in_the_money', meta: 'UC SQL parity-pinned' },
      { layer: 'SEMANTIC', name: 'metrics.itm_flag', meta: 'UC metric view' },
    ],
    signals: [
      { label: 'Par refi rate', source: 'mip.silver.market_rates_weekly.market_rate_fraction', value: 'latest available feed' },
      { label: 'Lien rate', source: 'voluntary_lien.current_rate', value: 'per borrower' },
      { label: 'Rate spread', source: 'derived', value: 'lien minus par' },
      { label: 'Equity %', source: 'avm + lien balance', value: 'per borrower' },
    ],
  },

  marketRate: {
    title: 'Market rate comparison',
    short: 'Market rate comparison',
    description:
      "Computes the basis-point spread between a borrower's current lien rate and the market refinance reference rate. Positive spread means the current rate appears above market.",
    lineage: [
      { layer: 'SOURCE', name: 'fred.MORTGAGE30US', meta: 'FRED 30y conforming par-refi rate' },
      { layer: 'SOURCE', name: 'mip.silver.market_rates_weekly', meta: 'FRED MORTGAGE30US weekly snapshot' },
      { layer: 'SOURCE', name: 'cotality.liens.voluntary_lien', meta: 'Current lien rate, per CLIP' },
      { layer: 'PRIMITIVE', name: 'mip.gold.fn_rate_spread', meta: 'UC SQL parity-pinned to scoring.py' },
      { layer: 'SEMANTIC', name: 'metrics.rate_spread_bps', meta: 'UC metric view' },
    ],
    signals: [
      { label: 'Market par rate', source: 'fred.MORTGAGE30US', value: 'latest available snapshot' },
      { label: 'Borrower lien rate', source: 'voluntary_lien.current_rate', value: 'per row' },
      { label: 'Spread (bps)', source: 'derived', value: 'lien - par x 100' },
    ],
  },

  leadGenerationView: {
    title: 'Lead-generation metric view',
    short: 'Lead-generation metric view',
    assetKey: 'lead_generation_metric_view',
    assetPath: 'mip.semantics.lead_generation_metric_view',
    usedIn: ['Home', 'Genie', 'Executive analytics'],
    description:
      'Curated semantic view for executive lead-generation questions: marketable population, ranked borrowers, top geographies, score bands, and offer-ready funnel measures.',
    lineage: [
      { layer: 'GOLD', name: 'mip.gold.lead_population', meta: 'ranked lead queue population' },
      { layer: 'GOLD', name: 'mip.gold.lead_scores', meta: 'opportunity score components' },
      { layer: 'SEMANTIC', name: 'mip.semantics.lead_generation_metric_view', meta: 'Genie trusted asset' },
    ],
    signals: [
      { label: 'Population', source: 'lead_population', value: 'ranked borrower grain' },
      { label: 'Score', source: 'lead_scores.opportunity_score', value: '0-100' },
    ],
  },

  segmentPerformanceView: {
    title: 'Segment performance metric view',
    short: 'Segment performance metric view',
    assetKey: 'segment_performance_metric_view',
    assetPath: 'mip.semantics.segment_performance_metric_view',
    usedIn: ['Analytics segments', 'Genie'],
    description:
      'Curated semantic view for segment comparison questions. It uses the same segment predicates as the app, including live MLS listing rows and Cotality HELOC propensity for intent.',
    lineage: [
      { layer: 'GOLD', name: 'mip.gold.segment_population', meta: 'segment counts and averages' },
      { layer: 'GOLD', name: 'mip.gold.borrower_360', meta: 'segment predicate source' },
      { layer: 'SEMANTIC', name: 'mip.semantics.segment_performance_metric_view', meta: 'Genie trusted asset' },
    ],
    signals: [
      { label: 'Segment', source: 'segment_population.segment_code', value: 'controlled Module 0 vocab' },
      { label: 'Borrowers', source: 'segment_population.count', value: 'predicate count' },
      { label: 'Listed for Sale', source: 'borrower_360.listed_for_sale', value: 'current MLS row' },
      { label: 'HELOC Intent', source: 'borrower_360.has_heloc_propensity_trigger', value: 'score >= 700' },
    ],
  },

  borrowerOpportunityView: {
    title: 'Borrower opportunity metric view',
    short: 'Borrower opportunity metric view',
    assetKey: 'borrower_opportunity_metric_view',
    assetPath: 'mip.semantics.borrower_opportunity_metric_view',
    usedIn: ['Analytics economics', 'Geography drilldowns', 'Genie'],
    description:
      'Curated borrower-level semantic view used by Genie for drill-down questions about cohorts, geographies, scores, and selected offer rationale.',
    lineage: [
      { layer: 'FEATURES', name: 'mip.gold.borrower_360', meta: 'borrower-level feature set' },
      { layer: 'GOLD', name: 'mip.gold.lead_scores', meta: 'score components and offer code' },
      { layer: 'SEMANTIC', name: 'mip.semantics.borrower_opportunity_metric_view', meta: 'Genie trusted asset' },
    ],
    signals: [
      { label: 'Borrower grain', source: 'borrower_360.borrower_id', value: 'masked demo-safe id' },
      { label: 'Primary offer', source: 'borrower_360.recommended_offer_code', value: 'selected offer path' },
      { label: 'Evidence ids', source: 'borrower_360.evidence_ids', value: 'ordered evidence refs' },
    ],
  },

  leadScore: {
    title: 'Opportunity score',
    short: 'Opportunity score',
    assetKey: 'lead_scores',
    assetPath: 'mip.gold.lead_scores',
    usedIn: ['Opportunity score', 'Signal strength', 'Borrower proof'],
    description:
      'Ranks how strong a borrower is for review on a 0-100 scale. The score blends refinance economics, intent, product fit, relationship, and evidence quality.',
    lineage: [
      { layer: 'FEATURES', name: 'mip.gold.lead_scores', meta: 'CLIP-grain component scores + final opportunity_score' },
      { layer: 'PRIMITIVE', name: 'mip.gold.fn_lead_score', meta: 'UC SQL weighted blend' },
      { layer: 'PARITY', name: 'backend/services/scoring.py', meta: 'Pinned to tests/fixtures/lead_score_golden.json' },
      { layer: 'SEMANTIC', name: 'mip.semantics.lead_generation_metric_view', meta: 'Genie + reporting surface' },
    ],
    signals: [
      { label: 'Economic incentive', source: 'lead_scores.economic_incentive', value: '35% weight' },
      { label: 'Intent trigger', source: 'lead_scores.intent_trigger', value: '30% weight' },
      { label: 'Fit', source: 'lead_scores.fit', value: '15% weight' },
      { label: 'Relationship', source: 'lead_scores.relationship', value: '10% weight' },
      { label: 'Evidence', source: 'lead_scores.evidence', value: '10% weight' },
    ],
  },

  lockinCohort: {
    title: 'Lock-in cohort',
    short: 'Lock-in cohort',
    assetKey: 'lockin_cohort',
    assetPath: 'mip.gold.lockin_cohort',
    usedIn: ['Genie', 'Rate-lock analysis'],
    description:
      'Gold cohort table used for rate-lock and refi-sensitivity questions. It is derived from borrower economics and market-rate comparisons, not from synthetic workflow counters.',
    lineage: [
      { layer: 'FEATURES', name: 'mip.gold.borrower_360', meta: 'current rate, equity, LTV' },
      { layer: 'SOURCE', name: 'mip.silver.market_rates_weekly', meta: 'FRED MORTGAGE30US weekly snapshot' },
      { layer: 'GOLD', name: 'mip.gold.lockin_cohort', meta: 'rate lock-in cohort' },
    ],
    signals: [
      { label: 'Current rate', source: 'borrower_360.current_rate', value: 'per borrower' },
      { label: 'Market rate', source: 'market_rates_weekly.market_rate_fraction', value: 'latest snapshot' },
      { label: 'Rate spread', source: 'derived', value: 'basis points' },
    ],
  },

  nbo: {
    title: 'Primary offer rules',
    short: 'How the offer path was selected',
    assetKey: 'borrower_360',
    assetPath: 'mip.gold.borrower_360',
    usedIn: ['Primary offer', 'Outreach approvals', 'Borrower proof'],
    description:
      'Chooses one offer path from the strongest current signals. Listings route to next-home purchase financing; refinance economics route to refi or refi plus equity review; weak signals stay in nurture.',
    lineage: [
      { layer: 'FEATURES', name: 'mip.gold.borrower_360', meta: 'Cotality public records + Owner Link + lien history' },
      { layer: 'PRIMITIVE', name: 'mip.gold.fn_next_best_offer', meta: 'UC SQL decision tree' },
      { layer: 'PARITY', name: 'backend/services/scoring.py', meta: 'Pinned to golden cases' },
    ],
    signals: [
      { label: 'Rate spread', source: 'borrower_360.rate_spread_bps', value: 'refi economics' },
      { label: 'Equity', source: 'borrower_360.equity_pct', value: 'equity product fit' },
      { label: 'HELOC intent', source: 'borrower_360.has_heloc_propensity_trigger', value: 'propensity trigger' },
      { label: 'Permit activity', source: 'borrower_360.has_permit', value: 'filed permit only' },
      { label: 'Listing activity', source: 'borrower_360.listed_for_sale', value: 'next-home purchase path' },
      { label: 'Investor profile', source: 'borrower_360.is_investor', value: 'portfolio lending path' },
      { label: 'Current customer', source: 'borrower_360.is_current_customer', value: 'relationship path' },
      { label: 'Competitor lien', source: 'borrower_360.is_competitor_lien', value: 'recapture path' },
    ],
  },

  helocPropensity: {
    title: 'HELOC propensity signal',
    short: 'HELOC propensity',
    assetKey: 'heloc_propensity',
    assetPath: 'mip.silver.heloc_propensity',
    description:
      'Cotality HELOC propensity score feed used as the live HELOC-intent overlay. This is a model propensity signal, not a filed building permit.',
    lineage: [
      { layer: 'SOURCE', name: 'cotality_mortgage_data.corelogic.entrada_eval_heloc_propensity_score_v1', meta: 'Cotality HELOC propensity' },
      { layer: 'SILVER', name: 'mip.silver.heloc_propensity', meta: 'latest score by CLIP' },
      { layer: 'GOLD', name: 'mip.gold.borrower_360', meta: 'has_heloc_propensity_trigger = score >= 700' },
      { layer: 'GOLD', name: 'mip.gold.evidence_events', meta: 'heloc_propensity evidence rows' },
    ],
    signals: [
      { label: 'Score', source: 'heloc_propensity_score', value: '0-999' },
      { label: 'Trigger', source: 'has_heloc_propensity_trigger', value: '>= 700' },
      { label: 'Run date', source: 'heloc_propensity_run_date', value: 'model run date' },
    ],
  },

  refiPropensity: {
    title: 'Refi propensity signal',
    short: 'Refi propensity',
    assetKey: 'refi_propensity',
    assetPath: 'mip.silver.refi_propensity',
    description:
      'Cotality refinance propensity score feed. It supplements the deterministic refinance-economics screen without replacing the rate-spread threshold.',
    lineage: [
      { layer: 'SOURCE', name: 'cotality_mortgage_data.corelogic.entrada_eval_refi_propensity_score_v1', meta: 'Cotality refinance propensity' },
      { layer: 'SILVER', name: 'mip.silver.refi_propensity', meta: 'latest score by CLIP' },
      { layer: 'GOLD', name: 'mip.gold.borrower_360', meta: 'has_refi_propensity_trigger = score >= 700' },
      { layer: 'GOLD', name: 'mip.gold.evidence_events', meta: 'refi_propensity evidence rows' },
    ],
    signals: [
      { label: 'Score', source: 'refi_propensity_score', value: '0-999' },
      { label: 'Trigger', source: 'has_refi_propensity_trigger', value: '>= 700' },
      { label: 'Run date', source: 'refi_propensity_run_date', value: 'model run date' },
    ],
  },

  permit: {
    title: 'Building permit signal',
    short: 'Building Permits - pending',
    description:
      'True filed building-permit rows are still pending. The app keeps has_permit false until a governed permit table with filing date, type, value, and source record ID is present.',
    lineage: [
      { layer: 'SOURCE', name: 'cotality.permits.building', meta: 'Delta Share - pending' },
      { layer: 'JOIN', name: 'join.permit_to_clip', meta: 'pending true permit source' },
      { layer: 'GOLD', name: 'mip.gold.borrower_360.has_permit', meta: 'false until filed-permit evidence exists' },
    ],
    signals: [
      { label: 'Readiness', source: 'mip.gold.source_readiness', value: 'roadmap' },
      { label: 'Filed permit flag', source: 'mip.gold.borrower_360.has_permit', value: 'filed permit only' },
      { label: 'HELOC intent substitute', source: 'mip.silver.heloc_propensity', value: 'separate signal' },
    ],
  },

  mls: {
    title: 'MLS listing signal',
    short: 'MLS listing',
    assetKey: 'listing_activity',
    assetPath: 'mip.silver.listing_activity',
    description:
      'Cotality MLS listing activity joined to CLIP. Active and under-contract current rows drive listed_for_sale, purchase-intent segmentation, and listing evidence.',
    lineage: [
      { layer: 'SOURCE', name: 'cotality_mortgage_data.corelogic.entrada_eval_mls_listing_v1', meta: 'Cotality MLS listing feed' },
      { layer: 'SILVER', name: 'mip.silver.listing_activity', meta: 'current listing row by CLIP' },
      { layer: 'GOLD', name: 'mip.gold.borrower_360', meta: 'listed_for_sale flag and listing attributes' },
      { layer: 'GOLD', name: 'mip.gold.evidence_events', meta: 'listing evidence rows' },
    ],
    signals: [
      { label: 'Readiness', source: 'mip.gold.source_readiness', value: 'live' },
      { label: 'listed_for_sale', source: 'mip.gold.borrower_360', value: 'active or under contract' },
      { label: 'Listing evidence', source: 'mip.gold.evidence_events', value: 'signal_type = listing' },
    ],
  },

  config: {
    title: 'Campaign assumptions',
    short: 'config',
    description: 'Cost-per-contact and projected conversion assumptions, set per lender in campaign config.',
    lineage: [{ layer: 'CONFIG', name: 'lender.campaign_config' }],
    signals: [],
  },
};

// ---------------------------------------------------------------------------
// S1.3 per-segment evidence sources.
//
// Every segment count opens the EvidenceDrawer with the segment's exact
// membership predicate, the borrower_360 flag column that computes it, and
// the live Cotality silver rows it reads. `assetKey: 'segment_population'`
// wires the drawer to governed UC metadata (row counts, freshness, observed
// lineage, Catalog Explorer link) for the rollup the card count comes from.
// ---------------------------------------------------------------------------

interface SegmentEvidenceSpec {
  /** gold.borrower_360 membership predicate, verbatim. */
  predicate: string;
  /** Source layers under borrower_360, most-upstream first. */
  sources: Array<readonly [layer: string, name: string, meta?: string]>;
}

const SEGMENT_EVIDENCE_SPECS: Record<string, SegmentEvidenceSpec> = {
  itm: {
    predicate: 'fn_in_the_money(rate_spread_bps, equity_pct)',
    sources: [
      ['SILVER', 'mip.silver.lien_current', 'rates, AVM, equity'],
      ['SILVER', 'mip.silver.market_rates_weekly', 'par-rate reference'],
    ],
  },
  listed: {
    predicate: 'listed_for_sale = TRUE (current active/under-contract MLS row)',
    sources: [
      ['SILVER', 'mip.silver.listing_activity', 'MLS rows joined to CLIP'],
    ],
  },
  permit: {
    predicate: 'has_permit OR has_heloc_propensity_trigger',
    sources: [
      ['SILVER', 'mip.silver.heloc_propensity', 'HELOC propensity score'],
    ],
  },
  investor: {
    predicate: 'related_property_count >= 2 OR is_corporate_owner OR is_absentee',
    sources: [
      ['SILVER', 'mip.silver.owner_property_bridge', 'Owner Link rollup'],
    ],
  },
  equity: {
    predicate: 'equity_pct >= heloc threshold AND COALESCE(second_pos_amount, 0) = 0',
    sources: [
      ['SILVER', 'mip.silver.lien_current', 'AVM + open-lien equity'],
    ],
  },
  retention: {
    predicate: 'is_current_customer AND (spread >= retention threshold OR competitor lien OR listed)',
    sources: [
      ['REF', 'mip.ref.lender_dictionary', 'tenant vs competitor mapping'],
      ['SILVER', 'mip.silver.lien_current', 'servicer + spread'],
    ],
  },
  second_lien_itm: {
    predicate: 'second_pos_amount > 0 AND fn_in_the_money(second_pos_rate_spread_bps, equity_pct)',
    sources: [
      ['SILVER', 'mip.silver.lien_current', 'second lien rate/balance'],
      ['SILVER', 'mip.silver.market_rates_weekly', 'par-rate reference'],
    ],
  },
  heloc_draw_to_payback: {
    predicate: 'open equity-loan lien originated 102-126 months ago',
    sources: [
      ['SILVER', 'mip.silver.mortgage_events', 'equity-loan timeline'],
    ],
  },
  home_equity_history: {
    predicate: 'appreciation >= 40% since purchase AND tenure >= 36 months AND equity_pct >= 20',
    sources: [
      ['SILVER', 'mip.silver.lien_current', 'purchase basis + AVM'],
    ],
  },
  refi_propensity: {
    predicate: 'fn_refi_propensity_heuristic(...) >= 60',
    sources: [
      ['UDF', 'mip.gold.fn_refi_propensity_heuristic', 'published heuristic'],
      ['SILVER', 'mip.silver.lien_current', 'spread, seasoning, equity, balance'],
    ],
  },
  itm_on_related_property: {
    predicate: 'Owner Link also holds a different in-the-money CLIP',
    sources: [
      ['SILVER', 'mip.silver.property_owners', 'Owner Link ids'],
      ['SILVER', 'mip.silver.lien_current', 'related-property economics'],
    ],
  },
  payoff_loss_leads: {
    predicate: 'tenant lien released within 24 months AND current competitor lien',
    sources: [
      ['SILVER', 'mip.silver.mortgage_events', 'release/payoff events'],
      ['REF', 'mip.ref.lender_dictionary', 'tenant vs competitor mapping'],
    ],
  },
  permit_activity: {
    predicate: 'has_permit = TRUE (filed-permit source pending)',
    sources: [
      ['GOLD', 'mip.gold.source_readiness', 'permit source status'],
    ],
  },
};

const SEGMENT_GATE_COPY: Record<string, string> = {
  not_connected:
    'Source not connected; count is gated.',
  not_licensed:
    'Source not licensed; count is gated.',
};

export function segmentEvidenceSource(
  segment: Pick<
    SegmentSummary,
    'code' | 'name' | 'count' | 'avg_score' | 'description' | 'source_status' | 'source_name'
  >,
): DrawerSource {
  const spec = SEGMENT_EVIDENCE_SPECS[segment.code];
  const gated = segment.source_status === 'not_connected' || segment.source_status === 'not_licensed';
  const gateCopy = gated ? SEGMENT_GATE_COPY[segment.source_status ?? ''] : null;
  return enrichAsset({
    title: `${segment.name} evidence`,
    short: `segment_population.${segment.code}`,
    assetKey: 'segment_population',
    assetPath: 'mip.gold.segment_population',
    usedIn: ['Segments', 'Lead Queue', 'Map'],
    description: gateCopy
      ? `${segment.description} ${gateCopy}`
      : `${segment.description} Live total from mip.gold.segment_population.`,
    lineage: [
      ...(spec?.sources.map(([layer, name, meta]) => ({ layer, name, meta })) ?? []),
      { layer: 'GOLD', name: 'mip.gold.borrower_360', meta: spec?.predicate ?? 'segment membership flag' },
      { layer: 'GOLD', name: 'mip.gold.segment_population', meta: 'per-state + national member rollup' },
    ],
    signals: gated
      ? [
          {
            label: 'Source status',
            source: 'gold.source_readiness',
            value: `${segment.source_name ?? 'source'}: ${segment.source_status === 'not_licensed' ? 'not licensed' : 'not connected'}`,
          },
        ]
      : [
          { label: 'Members', source: `count['${segment.code}']`, value: segment.count.toLocaleString() },
          { label: 'Average score', source: 'avg_score', value: String(segment.avg_score) },
        ],
  });
}
