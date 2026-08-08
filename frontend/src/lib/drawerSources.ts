import type { DrawerSource } from '../components/AppContext';
import { DRAWER_SOURCES } from './drawerSourceRegistry';

export { DRAWER_SOURCES } from './drawerSourceRegistry';
export { segmentEvidenceSource } from './segmentEvidenceSource';

export type EvidenceDestination =
  | {
      kind: 'unity_catalog';
      label: 'Unity Catalog';
      description: string;
    }
  | {
      kind: 'lakebase';
      label: 'Lakebase operational records';
      description: string;
      objectPaths: readonly string[];
      href: string;
      actionLabel: string;
    }
  | {
      kind: 'readiness';
      label: 'Pending source readiness';
      description: string;
      objectPaths: readonly string[];
      href: string;
      actionLabel: string;
    }
  | {
      kind: 'unmapped';
      label: 'Destination not mapped';
      description: string;
    };

const UNITY_CATALOG_DESTINATION: EvidenceDestination = {
  kind: 'unity_catalog',
  label: 'Unity Catalog',
  description: 'Governed tables, views, and functions open in Catalog Explorer.',
};

const UNMAPPED_DESTINATION: EvidenceDestination = {
  kind: 'unmapped',
  label: 'Destination not mapped',
  description: 'This source is not in the reviewed evidence-destination registry.',
};

export const ASSET_KEYS_BY_SOURCE: Readonly<Record<string, string>> = {
  'cotality_mortgage_data.corelogic.entrada_eval_property_domain_v3': 'entrada_eval_property_domain_v3',
  'cotality_mortgage_data.corelogic.entrada_eval_voluntary_lien_status_marketing_v2': 'entrada_eval_voluntary_lien_status_marketing_v2',
  'cotality_mortgage_data.corelogic.entrada_eval_mls_listing_v1': 'entrada_eval_mls_listing_v1',
  'cotality_mortgage_data.corelogic.entrada_eval_heloc_propensity_score_v1': 'entrada_eval_heloc_propensity_score_v1',
  'cotality_mortgage_data.corelogic.entrada_eval_refi_propensity_score_v1': 'entrada_eval_refi_propensity_score_v1',
  'cotality_mortgage_data.corelogic.entrada_eval_mortgage_domain_v1': 'entrada_eval_mortgage_domain_v1',
  'cotality_mortgage_data.corelogic.entrada_eval_owner_transfer_domain_v1': 'entrada_eval_owner_transfer_domain_v1',
  'mip.first_party.loan_applications': 'loan_applications',
  'mip.silver.property_master': 'property_master',
  'mip.silver.property_owners': 'property_owners',
  'mip.silver.lien_current': 'lien_current',
  'mip.silver.market_rates_weekly': 'market_rates_weekly',
  'mip.silver.mortgage_events': 'mortgage_events',
  'mip.silver.owner_transfer_events': 'owner_transfer_events',
  'mip.silver.listing_activity': 'listing_activity',
  'mip.silver.heloc_propensity': 'heloc_propensity',
  'mip.silver.refi_propensity': 'refi_propensity',
  'mip.gold.lead_population': 'lead_population',
  'mip.gold.segment_population': 'segment_population',
  'mip.gold.lead_scores': 'lead_scores',
  'mip.gold.borrower_360': 'borrower_360',
  'mip.gold.borrower_dossier': 'borrower_dossier',
  'mip.gold.evidence_events': 'evidence_events',
  'mip.gold.household_rollup': 'household_rollup',
  'mip.gold.property_owner_bridge': 'property_owner_bridge',
  'mip.gold.source_readiness': 'source_readiness',
  'mip.gold.lockin_cohort': 'lockin_cohort',
  'mip.gold.borrower_lifecycle_state': 'borrower_lifecycle_state',
  'mip.gold.funnel_snapshot_daily': 'funnel_snapshot_daily',
  'mip.gold.county_rollup': 'county_rollup',
  'mip.gold.zip_rollup': 'zip_rollup',
  'mip.gold.equity_spread_points': 'equity_spread_points',
  'mip.gold.fn_bounded_mortgage_rate': 'fn_bounded_mortgage_rate',
  'mip.gold.fn_estimated_upb': 'fn_estimated_upb',
  'mip.gold.fn_estimated_upb_confidence_band': 'fn_estimated_upb_confidence_band',
  'mip.gold.fn_high_opportunity': 'fn_high_opportunity',
  'mip.gold.fn_in_the_money': 'fn_in_the_money',
  'mip.gold.fn_lead_score': 'fn_lead_score',
  'mip.gold.fn_loan_product_type': 'fn_loan_product_type',
  'mip.gold.fn_next_best_offer': 'fn_next_best_offer',
  'mip.gold.fn_rate_spread': 'fn_rate_spread',
  'mip.gold.fn_score_band': 'fn_score_band',
  'mip.semantics.portfolio_headline_metric_view': 'portfolio_headline_metric_view',
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
  if (!key.includes('.') && Object.values(ASSET_KEYS_BY_SOURCE).includes(key)) return key;
  return null;
}

export function assetHrefForSource(rawSource?: string | null): string | null {
  const assetKey = assetKeyForSource(rawSource);
  return assetKey ? assetDetailHref(assetKey) : null;
}

export function assetDetailHref(assetKey: string): string {
  return `/data-estate/assets/${encodeURIComponent(assetKey)}`;
}

export function enrichAsset(source: DrawerSource): DrawerSource {
  if (source.assetKey) return source;
  const assetKey = source.assetKey ?? assetKeyForSource(source.assetPath ?? source.short);
  return assetKey ? { ...source, assetKey } : source;
}

/**
 * Route a raw UC source (for example, `mip.gold.fn_in_the_money`) to the
 * matching DRAWER_SOURCES entry. Unknown objects get an explicitly unmapped
 * descriptor; they never inherit a Catalog destination from a matching suffix.
 *
 * Keep this in sync with backend/services/scoring.source_display_label and
 * backend/api/offers._sources_for: the app should not tell a different lineage
 * story from the API payload that supplied the evidence.
 */
export function descriptorFor(rawSource: string): DrawerSource {
  return drawerForAsset(rawSource) ?? {
    title: rawSource,
    short: rawSource.split('.').pop() ?? rawSource,
    description: `Unmapped evidence source: ${rawSource}.`,
    signals: [],
  };
}

export function drawerForAsset(rawSource: string): DrawerSource | null {
  const key = rawSource.toLowerCase();

  if (key === 'mip_app.call_dispositions') return DRAWER_SOURCES.callDispositions;
  if (key === 'mip_app.lead_outcomes') return DRAWER_SOURCES.leadOutcomes;
  if (key.includes('equity_spread_points')) return enrichAsset(DRAWER_SOURCES.equitySpreadPoints);
  if (key.includes('fn_rate_spread')) return enrichAsset(DRAWER_SOURCES.marketRate);
  if (key.includes('fn_estimated_upb_confidence_band')) return enrichAsset(DRAWER_SOURCES.lien);
  if (key.includes('fn_estimated_upb')) return enrichAsset(DRAWER_SOURCES.lien);
  if (key.includes('fn_bounded_mortgage_rate')) return enrichAsset(DRAWER_SOURCES.lien);
  if (key.includes('fn_loan_product_type')) return enrichAsset(DRAWER_SOURCES.loanProductType);
  if (key.includes('fn_score_band') || key.includes('fn_high_opportunity')) {
    return enrichAsset(DRAWER_SOURCES.leadScore);
  }
  if (key.includes('rate_spread')) return enrichAsset(DRAWER_SOURCES.rateSpread);
  if (key.includes('fn_in_the_money') || key.includes('itm')) return enrichAsset(DRAWER_SOURCES.itm);
  if (key.includes('fn_lead_score') || key.includes('lead_scores')) return enrichAsset(DRAWER_SOURCES.leadScore);
  if (key.includes('fn_next_best_offer') || key.includes('nbo')) return enrichAsset(DRAWER_SOURCES.nbo);
  if (key.includes('portfolio_headline_metric_view')) return enrichAsset(DRAWER_SOURCES.portfolioHeadlineView);
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
  if (key.includes('funnel_snapshot_daily') || key.includes('borrower_lifecycle_state')) {
    return enrichAsset(DRAWER_SOURCES.funnelSnapshot);
  }
  if (key.includes('county_rollup')) return enrichAsset(DRAWER_SOURCES.countyRollup);
  if (key.includes('zip_rollup')) return enrichAsset(DRAWER_SOURCES.zipRollup);
  if (key.includes('property_owner_bridge') || key.includes('property_owners') || key.includes('owner_link')) {
    return enrichAsset(DRAWER_SOURCES.ownerGraph);
  }
  if (key.includes('property_master') || key.includes('entrada_eval_property_domain')) {
    return enrichAsset(DRAWER_SOURCES.propertyProfile);
  }
  if (key.includes('mortgage_events') || key.includes('mortgage_domain')) {
    return enrichAsset(DRAWER_SOURCES.mortgageDomain);
  }
  if (key.includes('owner_transfer_events') || key.includes('owner_transfer')) {
    return enrichAsset(DRAWER_SOURCES.ownerTransfer);
  }
  if (key.includes('loan_applications') || key.includes('origination_channel')) {
    return enrichAsset(DRAWER_SOURCES.originationChannel);
  }
  if (key.includes('offer_rules_config')) return enrichAsset(DRAWER_SOURCES.loanProductType);
  if (key.includes('lender_dictionary')) return enrichAsset(DRAWER_SOURCES.lien);
  if (key.includes('market_rates_weekly')) return enrichAsset(DRAWER_SOURCES.marketRate);
  if (key.includes('avm')) return enrichAsset(DRAWER_SOURCES.avm);
  if (key.includes('lien_current') || key.includes('voluntary_lien')) {
    return enrichAsset(DRAWER_SOURCES.lien);
  }
  if (key.includes('heloc_propensity')) return enrichAsset(DRAWER_SOURCES.helocPropensity);
  if (key.includes('refi_propensity')) return enrichAsset(DRAWER_SOURCES.refiPropensity);
  if (key.includes('mls') || key.includes('listing')) return enrichAsset(DRAWER_SOURCES.mls);
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
  // S1.6 dimensions. `product_type` shares the Voluntary Lien source product
  // with rate/lien signals, so route on the specific signal_type BEFORE the
  // generic lien block below (which would otherwise swallow it via the shared
  // 'Voluntary Lien' source product). Origination channel comes from the
  // First-Party LOS product.
  if (signal.includes('product_type')) {
    return withEventDate(DRAWER_SOURCES.loanProductType, event.timestamp);
  }
  if (signal.includes('origination_channel') || product.includes('first-party los')) {
    return withEventDate(DRAWER_SOURCES.originationChannel, event.timestamp);
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

type DrawerSourceKey = keyof typeof DRAWER_SOURCES;

const DESTINATION_BY_SOURCE = {
  population: UNITY_CATALOG_DESTINATION,
  populationMarketable: UNITY_CATALOG_DESTINATION,
  equitySpreadPoints: UNITY_CATALOG_DESTINATION,
  leadPopulation: UNITY_CATALOG_DESTINATION,
  segmentPopulation: UNITY_CATALOG_DESTINATION,
  ownerGraph: UNITY_CATALOG_DESTINATION,
  householdRollup: UNITY_CATALOG_DESTINATION,
  propertyProfile: UNITY_CATALOG_DESTINATION,
  borrower360: UNITY_CATALOG_DESTINATION,
  borrowerDossier: UNITY_CATALOG_DESTINATION,
  lien: UNITY_CATALOG_DESTINATION,
  rateSpread: UNITY_CATALOG_DESTINATION,
  mortgageDomain: UNITY_CATALOG_DESTINATION,
  ownerTransfer: UNITY_CATALOG_DESTINATION,
  evidenceStream: UNITY_CATALOG_DESTINATION,
  sourceReadiness: UNITY_CATALOG_DESTINATION,
  avm: UNITY_CATALOG_DESTINATION,
  itm: UNITY_CATALOG_DESTINATION,
  marketRate: UNITY_CATALOG_DESTINATION,
  portfolioHeadlineView: UNITY_CATALOG_DESTINATION,
  leadGenerationView: UNITY_CATALOG_DESTINATION,
  segmentPerformanceView: UNITY_CATALOG_DESTINATION,
  borrowerOpportunityView: UNITY_CATALOG_DESTINATION,
  leadScore: UNITY_CATALOG_DESTINATION,
  lockinCohort: UNITY_CATALOG_DESTINATION,
  funnelSnapshot: UNITY_CATALOG_DESTINATION,
  countyRollup: UNITY_CATALOG_DESTINATION,
  zipRollup: UNITY_CATALOG_DESTINATION,
  nbo: UNITY_CATALOG_DESTINATION,
  helocPropensity: UNITY_CATALOG_DESTINATION,
  refiPropensity: UNITY_CATALOG_DESTINATION,
  loanProductType: UNITY_CATALOG_DESTINATION,
  originationChannel: UNITY_CATALOG_DESTINATION,
  permit: {
    kind: 'readiness',
    label: 'Pending source readiness',
    description:
      'No governed building-permit table exists. Navigate to the source-readiness ledger; do not open a fabricated Catalog object.',
    objectPaths: ['mip.gold.source_readiness'],
    href: assetDetailHref('source_readiness'),
    actionLabel: 'View source readiness',
  },
  mls: UNITY_CATALOG_DESTINATION,
  assignmentOverlay: {
    kind: 'lakebase',
    label: 'Lakebase operational records',
    description:
      'Assignment and loan-officer rows are operational Postgres state queried through the sales-operations APIs, not Unity Catalog assets.',
    objectPaths: ['mip_app.lead_assignments', 'mip_app.loan_officers'],
    href: '/analytics?view=sales-ops',
    actionLabel: 'Open sales operations',
  },
  callDispositions: {
    kind: 'lakebase',
    label: 'Lakebase operational records',
    description:
      'Contact dispositions are operational Postgres rows queried through the non-admin Sales Ops analytics surface.',
    objectPaths: ['mip_app.call_dispositions'],
    href: '/analytics?view=sales-ops',
    actionLabel: 'Open sales operations',
  },
  leadOutcomes: {
    kind: 'lakebase',
    label: 'Lakebase operational records',
    description:
      'Closed-loop lead outcomes are PII-safe operational Postgres rows queried through the non-admin Sales Ops analytics surface.',
    objectPaths: ['mip_app.lead_outcomes'],
    href: '/analytics?view=sales-ops',
    actionLabel: 'Open sales operations',
  },
  config: {
    kind: 'lakebase',
    label: 'Lakebase operational records',
    description:
      'Saved campaign assumptions live in the campaigns operational record and are queried through the campaign workflow.',
    objectPaths: ['mip_app.campaigns'],
    href: '/portfolio-builder',
    actionLabel: 'Open campaign builder',
  },
} satisfies Record<DrawerSourceKey, EvidenceDestination>;

export function evidenceDestinationFor(source?: DrawerSource | null): EvidenceDestination {
  if (!source) return UNMAPPED_DESTINATION;
  if (source.assetKey && source.assetKey in DRAWER_SOURCES) {
    return DESTINATION_BY_SOURCE[source.assetKey as DrawerSourceKey];
  }
  const registeredKey = assetKeyForSource(source.assetPath ?? source.assetKey);
  if (registeredKey && registeredKey in DRAWER_SOURCES) {
    return DESTINATION_BY_SOURCE[registeredKey as DrawerSourceKey];
  }
  const entry = (Object.entries(DRAWER_SOURCES) as Array<[
    DrawerSourceKey,
    DrawerSource,
  ]>).find(([, candidate]) =>
    candidate.title === source.title &&
    candidate.assetPath === source.assetPath &&
    candidate.short === source.short,
  );
  if (entry) return DESTINATION_BY_SOURCE[entry[0]];
  return registeredKey ? UNITY_CATALOG_DESTINATION : UNMAPPED_DESTINATION;
}
