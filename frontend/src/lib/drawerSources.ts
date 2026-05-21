import type { DrawerSource } from '../components/AppContext';

const ASSET_KEYS_BY_SOURCE: Record<string, string> = {
  'mip.gold.lead_population': 'lead_population',
  'mip.gold.segment_population': 'segment_population',
  'mip.gold.lead_scores': 'lead_scores',
  'mip.gold.borrower_360': 'borrower_360',
  'mip.gold.borrower_dossier': 'borrower_dossier',
  'mip.gold.evidence_events': 'evidence_events',
  'mip.gold.source_readiness': 'source_readiness',
  'mip.gold.lockin_cohort': 'lockin_cohort',
  'mip.gold.funnel_snapshot_daily': 'funnel_snapshot_daily',
  'mip.gold.county_rollup': 'county_rollup',
  'mip.gold.zip_rollup': 'zip_rollup',
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
    usedIn: ['Lead Queue', 'Borrower 360', 'Offer workflow', 'Analytics'],
    notExposed: 'Raw CLIP, owner names, street addresses, source-table paths, and lender raw strings.',
    description:
      'Gold Lead Queue population: the ranked, quality-filtered subset of borrower_360 where opportunity_score is at least 50. It carries borrower-safe display fields, segment codes, evidence IDs, recommended offer, rank, and approval state for outreach workflows.',
    lineage: [
      { layer: 'FEATURES', name: 'mip.gold.borrower_360', meta: 'borrower-grain Cotality + first-party features' },
      { layer: 'SCORE', name: 'mip.gold.lead_scores', meta: 'opportunity_score and signal-strength components' },
      { layer: 'GOLD', name: 'mip.gold.lead_population', meta: 'opportunity_score >= 50, ranked by score' },
      { layer: 'APP', name: 'Lead Queue / approval workflow', meta: 'evidence_ids and approval_status preserved' },
    ],
    signals: [
      { label: 'Quality floor', source: 'lead_population.opportunity_score', value: '>= 50' },
      { label: 'Rank', source: 'lead_population.rank_overall / rank_within_state', value: 'deterministic by score + CLIP' },
      { label: 'Evidence refs', source: 'lead_population.evidence_ids', value: 'audit-forwarded' },
      { label: 'Approval state', source: 'lead_population.approval_status', value: 'Lakebase-synced' },
    ],
  },

  segmentPopulation: {
    title: 'Segment population',
    short: 'Segment population',
    assetKey: 'segment_population',
    assetPath: 'mip.gold.segment_population',
    usedIn: ['Segment Intelligence', 'Analytics', 'Genie'],
    description:
      'Gold segment rollup used by Segment Intelligence. It preserves the configured segment predicates, AND/OR mode, geography, and average score at the population-rollup grain.',
    lineage: [
      { layer: 'FEATURES', name: 'mip.gold.borrower_360', meta: 'borrower-grain segment flags' },
      { layer: 'GOLD', name: 'mip.gold.segment_population', meta: 'segment count and average-score rollup' },
      { layer: 'SEMANTIC', name: 'mip.semantics.segment_performance_metric_view', meta: 'Genie + reporting surface' },
    ],
    signals: [
      { label: 'Segment count', source: 'segment_population.count', value: 'borrowers matching predicate' },
      { label: 'Average score', source: 'segment_population.avg_score', value: 'population average' },
      { label: 'Blocked feeds', source: 'listed / permit', value: '0 until Cotality shares land' },
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
      { layer: 'FEATURES', name: 'mip.gold.borrower_360', meta: 'current_lien_balance, current_rate, lender relationship' },
      { layer: 'SEMANTIC', name: 'mip.gold.evidence_events', meta: 'rate_spread and competitor_lien evidence rows' },
    ],
    signals: [
      { label: 'Lien rate', source: 'lien_current.first_pos_rate', value: 'per borrower' },
      { label: 'Open lien balance', source: 'lien_current.total_open_lien_balance', value: 'per borrower' },
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
      { label: 'Controlled vocab', source: 'evidence_events.signal_type', value: 'no permit/listing until shares land' },
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
      { layer: 'SOURCE', name: 'Cotality MLS/Listings', meta: 'pending Delta Share' },
      { layer: 'SOURCE', name: 'Cotality Building Permits', meta: 'pending Delta Share' },
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
      'AVM-backed property value and lien-balance math used to estimate borrower equity, CLTV/LTV, and the equity leg of In-the-Money and HELOC eligibility.',
    lineage: [
      { layer: 'SOURCE', name: 'cotality.lien_status_marketing.avm_fields', meta: 'estimated value and confidence fields' },
      { layer: 'SILVER', name: 'mip.silver.lien_current', meta: 'avm_value, confidence, as-of date, open-lien balance' },
      { layer: 'FEATURES', name: 'mip.gold.borrower_360', meta: 'equity_estimate, equity_pct, ltv' },
      { layer: 'SEMANTIC', name: 'mip.gold.evidence_events', meta: 'equity evidence rows' },
    ],
    signals: [
      { label: 'AVM value', source: 'lien_current.avm_value', value: 'per borrower' },
      { label: 'Equity estimate', source: 'borrower_360.equity_estimate', value: 'AVM minus open liens' },
      { label: 'Equity %', source: 'borrower_360.equity_pct', value: 'CLTV preferred, AVM fallback' },
    ],
  },

  itm: {
    title: 'In-the-Money logic',
    short: 'In-the-Money logic',
    description:
      'Flags a borrower when lien rate is at least 75 bps above par refi rate and equity is at least 15%. Deterministic UC SQL function, parity-pinned to backend/services/scoring.py.',
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
      "Computes the basis-point spread between a borrower's current lien rate and the market par-refinance rate. Output feeds In-the-Money logic and is also surfaced standalone.",
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
      'Curated semantic view for segment comparison questions. It uses the same segment predicates as the app and preserves pending-feed behavior for MLS and permit segments.',
    lineage: [
      { layer: 'GOLD', name: 'mip.gold.segment_population', meta: 'segment counts and averages' },
      { layer: 'GOLD', name: 'mip.gold.borrower_360', meta: 'segment predicate source' },
      { layer: 'SEMANTIC', name: 'mip.semantics.segment_performance_metric_view', meta: 'Genie trusted asset' },
    ],
    signals: [
      { label: 'Segment', source: 'segment_population.segment_code', value: 'controlled Module 0 vocab' },
      { label: 'Borrowers', source: 'segment_population.count', value: 'predicate count' },
      { label: 'Pending feeds', source: 'listed / permit', value: 'blocked false today' },
    ],
  },

  borrowerOpportunityView: {
    title: 'Borrower opportunity metric view',
    short: 'Borrower opportunity metric view',
    assetKey: 'borrower_opportunity_metric_view',
    assetPath: 'mip.semantics.borrower_opportunity_metric_view',
    usedIn: ['Analytics economics', 'Geography drilldowns', 'Genie'],
    description:
      'Curated borrower-level semantic view used by Genie for drill-down questions about cohorts, geographies, scores, and next-best-offer rationale.',
    lineage: [
      { layer: 'FEATURES', name: 'mip.gold.borrower_360', meta: 'borrower-level feature set' },
      { layer: 'GOLD', name: 'mip.gold.lead_scores', meta: 'score components and offer code' },
      { layer: 'SEMANTIC', name: 'mip.semantics.borrower_opportunity_metric_view', meta: 'Genie trusted asset' },
    ],
    signals: [
      { label: 'Borrower grain', source: 'borrower_360.borrower_id', value: 'masked demo-safe id' },
      { label: 'Offer code', source: 'borrower_360.recommended_offer_code', value: 'NBO output' },
      { label: 'Evidence ids', source: 'borrower_360.evidence_ids', value: 'ordered evidence refs' },
    ],
  },

  leadScore: {
    title: 'Lead score model',
    short: 'Lead score model',
    assetKey: 'lead_scores',
    assetPath: 'mip.gold.lead_scores',
    usedIn: ['Opportunity score', 'Signal strength', 'Borrower proof'],
    description:
      'Canonical 0-100 opportunity score. The score is a deterministic weighted blend of five sub-scores and is parity-pinned between UC SQL, backend scoring, and golden fixtures.',
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
    title: 'Next-Best-Offer logic',
    short: 'Next-Best-Offer logic',
    assetKey: 'borrower_360',
    assetPath: 'mip.gold.borrower_360',
    usedIn: ['Next-best-offer', 'Outreach approvals', 'Borrower proof'],
    description:
      'Deterministic decision tree over Cotality-derived signals. Output is a categorical product code. No ML model - the logic is transparent and auditable in sql/uc_functions/fn_next_best_offer.sql.',
    lineage: [
      { layer: 'FEATURES', name: 'mip.gold.borrower_360', meta: 'Cotality public records + Owner Link + lien history' },
      { layer: 'PRIMITIVE', name: 'mip.gold.fn_next_best_offer', meta: 'UC SQL decision tree' },
      { layer: 'PARITY', name: 'backend/services/scoring.py', meta: 'Pinned to golden cases' },
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
  },

  permit: {
    title: 'Permit signal',
    short: 'Building Permits - pending',
    description:
      'Cotality Building Permits share is pending. The signal is modeled but blocked false until the feed lands, so permit-sourced borrower counts remain 0 today.',
    lineage: [
      { layer: 'SOURCE', name: 'cotality.permits.building', meta: 'Delta Share - pending' },
      { layer: 'JOIN', name: 'join.permit_to_clip', meta: 'pending feed arrival' },
      { layer: 'SEMANTIC', name: 'metrics.permit_signal', meta: 'blocked false until landed' },
    ],
    signals: [
      { label: 'Readiness', source: 'admin.sources', value: 'roadmap' },
      { label: 'has_permit', source: 'mip.gold.borrower_360', value: 'blocked false' },
      { label: 'Permit rows', source: 'cotality.permits.building', value: 'pending share' },
    ],
  },

  mls: {
    title: 'MLS listing signal',
    short: 'MLS - pending',
    description:
      'Cotality MLS listing share is pending. Listed-for-sale predicates are blocked false until the Delta Share lands, so listing chips read as a source-dependency state rather than live listing evidence.',
    lineage: [
      { layer: 'SOURCE', name: 'cotality.mls.listings', meta: 'Delta Share - pending' },
      { layer: 'JOIN', name: 'join.listing_to_clip', meta: 'pending feed arrival' },
      { layer: 'SEMANTIC', name: 'metrics.listed_for_sale_flag', meta: 'blocked false until landed' },
    ],
    signals: [
      { label: 'Readiness', source: 'admin.sources', value: 'roadmap' },
      { label: 'listed_for_sale', source: 'mip.gold.borrower_360', value: 'blocked false' },
      { label: 'Listing rows', source: 'cotality.mls.listings', value: 'pending share' },
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
