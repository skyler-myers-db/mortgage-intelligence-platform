/**
 * Data-estate fixtures: the source-readiness proof surface, the asset detail
 * route, the governed lineage manifest the EvidenceDrawer reads, and (W5c
 * w5-evidence-drawer) the every-user asset freshness and the KPI reproduce
 * SQL the drawer reads while it is open.
 */
import type {
  AssetMetadataResponse,
  DataEstateResponse,
  LineageManifestFamily,
  LineageManifestResponse,
} from '../../../../src/types';
import type { AssetFreshnessResponse, KpiProofKey, KpiProofResponse } from '../../../../src/lib/apiTypes';
import type { HomeSummaryAttributionResponse } from '../../../../src/types/homeAttribution';
import type { ContractSample } from '../contractSamples';
import { fixture, json, type FixtureEntry } from '../mockApi';
import { notSnapshottedAttribution } from './portfolio';
import { LENDER_NAME, SNAPSHOT_AT, TOTALS } from './reference';

/** Asset key the smoke spec opens at /data-estate/assets/:assetKey. */
export const PRIMARY_ASSET_KEY = 'borrower_360';

const DATA_ESTATE: DataEstateResponse = {
  generated_at: SNAPSHOT_AT,
  lender_name: LENDER_NAME,
  public_demo_masking: true,
  lanes: [
    {
      id: 'raw',
      title: 'Cotality share (raw)',
      description: 'Delta-shared source products.',
      status: 'live',
      assets: [
        { name: 'voluntary_lien', label: 'Voluntary Lien', status: 'live', uc_object: 'cotality.liens.voluntary_lien', row_count: 1720000, last_updated: SNAPSHOT_AT, note: 'Shared table' },
        { name: 'owner_link', label: 'Owner Link', status: 'live', uc_object: 'cotality.entity.owner_link', row_count: 910000, last_updated: SNAPSHOT_AT, note: 'Shared table' },
      ],
    },
    {
      id: 'silver',
      title: 'Silver features',
      description: 'Cleaned, joined features.',
      status: 'live',
      assets: [
        { name: 'silver_property_features', label: 'Property features', status: 'live', uc_object: 'mip.silver.property_features', row_count: 1840000, last_updated: SNAPSHOT_AT, note: 'Lakeflow pipeline' },
      ],
    },
    {
      id: 'gold',
      title: 'Gold app tables',
      description: 'Precomputed tables the app reads.',
      status: 'live',
      assets: [
        { name: 'borrower_360', label: 'Borrower 360', status: 'live', uc_object: 'mip.gold.borrower_360', row_count: TOTALS.addressable, last_updated: SNAPSHOT_AT, note: 'Primary app table' },
        { name: 'evidence_events', label: 'Evidence events', status: 'live', uc_object: 'mip.gold.evidence_events', row_count: 412000, last_updated: SNAPSHOT_AT, note: 'Evidence drawer source' },
      ],
    },
    {
      id: 'contact',
      title: 'Borrower contact',
      description: 'Synthetic contact fields only.',
      status: 'demo_synthetic',
      assets: [
        { name: 'borrower_contact', label: 'Borrower contact (synthetic)', status: 'demo_synthetic', uc_object: 'mip.gold.borrower_contact', row_count: TOTALS.addressable, last_updated: SNAPSHOT_AT, note: 'Synthetic', synthetic_demo: true },
      ],
    },
  ],
  known_data_gaps: ['Building permits share pending', 'MLS share configured but empty'],
  proof_assets: ['mip.gold.borrower_360', 'mip.semantics.portfolio_headline_metric_view'],
};

function assetMetadata(assetKey: string): AssetMetadataResponse {
  return {
    asset_path: `mip.gold.${assetKey}`,
    title: 'Borrower 360 (gold)',
    description: 'Governed borrower-level gold table (fixture metadata).',
    object_type: 'table',
    status: 'live',
    freshness: 'fresh',
    catalog: 'mip',
    schema_name: 'gold',
    object_name: assetKey,
    uc_object: `mip.gold.${assetKey}`,
    observed_in_unity_catalog: true,
    observation_source: 'system.information_schema.tables',
    generated_at: SNAPSHOT_AT,
    last_updated: SNAPSHOT_AT,
    delta_last_modified: SNAPSHOT_AT,
    row_count: TOTALS.addressable,
    row_count_source: 'delta_stats',
    num_files: 12,
    size_in_bytes: 48_400_000,
    size_label: '48.4 MB',
    catalog_explorer_url: null,
    source_note: 'Fixture metadata; no live Unity Catalog call is made.',
    checked_at: SNAPSHOT_AT,
    tags: [{ name: 'domain', value: 'mortgage' }, { name: 'pii', value: 'masked' }],
    properties: [{ name: 'delta.enableChangeDataFeed', value: 'true' }],
    columns: [
      { name: 'borrower_id', data_type: 'STRING', comment: 'Masked borrower identifier', ordinal_position: 1 },
      { name: 'clip', data_type: 'STRING', comment: 'Cotality mastered property identifier', ordinal_position: 2 },
      { name: 'rate_spread_bps', data_type: 'INT', comment: 'Lien rate minus par, basis points', ordinal_position: 3 },
      { name: 'opportunity_score', data_type: 'INT', comment: 'fn_lead_score output', ordinal_position: 4 },
    ],
    ddl: null,
    ddl_redacted_lines: 0,
    lineage: [
      { direction: 'upstream', asset_path: 'mip.silver.property_features', label: 'Property features', object_type: 'table', event_time: SNAPSHOT_AT, event_count: 1, source: 'lineage_manifest', catalog_explorer_url: null },
      { direction: 'downstream', asset_path: 'mip.semantics.portfolio_headline_metric_view', label: 'Portfolio headline metric view', object_type: 'view', event_time: SNAPSHOT_AT, event_count: 1, source: 'lineage_manifest', catalog_explorer_url: null },
    ],
    known_data_gaps: [],
  };
}

const LINEAGE_FAMILIES: ReadonlyArray<readonly [string, string]> = [
  ['marketable_population', 'Marketable population'],
  ['opportunity_score', 'Opportunity score'],
  ['in_the_money', 'Refi economics screen'],
  ['next_best_offer', 'Primary offer path'],
];

function lineageFamily([id, title]: readonly [string, string]): LineageManifestFamily {
  return {
    id,
    title,
    description: `${title} lineage.`,
    nodes: [
      { id: `${id}_raw`, layer: 'raw_share', object_type: 'table', fqn: 'cotality.liens.voluntary_lien', label: 'Voluntary Lien', note: null, catalog_explorer_url: null },
      { id: `${id}_gold`, layer: 'gold', object_type: 'table', fqn: 'mip.gold.borrower_360', label: 'Borrower 360', note: null, catalog_explorer_url: null },
      { id: `${id}_metric`, layer: 'metric_view', object_type: 'view', fqn: 'mip.semantics.portfolio_headline_metric_view', label: 'Portfolio headline metric view', note: null, catalog_explorer_url: null },
    ],
  };
}

/**
 * GET /api/assets/:assetKey/freshness (critic-03): the 16 registry assets no
 * readiness row covers answer not_tracked; every other one is fresh as of the
 * snapshot, from the readiness row backend/services/asset_registry.py names.
 */
const NOT_TRACKED_ASSETS: ReadonlySet<string> = new Set([
  'fn_bounded_mortgage_rate', 'fn_estimated_upb', 'fn_estimated_upb_confidence_band', 'fn_high_opportunity',
  'fn_in_the_money', 'fn_lead_score', 'fn_loan_product_type', 'fn_next_best_offer', 'fn_rate_spread', 'fn_score_band',
  'offer_rules_config', 'lender_dictionary', 'source_readiness', 'borrower_lifecycle_state',
  'entrada_eval_owner_transfer_domain_v1', 'owner_transfer_events',
]);
const B360 = 'UC Gold Borrower 360';
const FRESHNESS_BASIS: Readonly<Record<string, string>> = {
  lead_population: 'UC Gold Lead Population', lead_generation_metric_view: 'UC Gold Lead Population',
  lead_scores: 'UC Gold Lead Scores', segment_population: 'UC Gold Segment Population',
  segment_performance_metric_view: 'UC Gold Segment Population', borrower_dossier: 'UC Gold Borrower Dossier',
  evidence_events: 'Voluntary Lien', lien_current: 'Voluntary Lien', entrada_eval_voluntary_lien_status_marketing_v2: 'Voluntary Lien',
  property_master: 'Cotality Public Records', entrada_eval_property_domain_v3: 'Cotality Public Records',
  mortgage_events: 'MMA Mortgage Analytics', entrada_eval_mortgage_domain_v1: 'MMA Mortgage Analytics',
  market_rates_weekly: 'FRED Market Rates', rate_window_weekly: 'FRED Market Rates',
  listing_activity: 'MLS Listings', entrada_eval_mls_listing_v1: 'MLS Listings',
  heloc_propensity: 'Cotality HELOC Propensity', entrada_eval_heloc_propensity_score_v1: 'Cotality HELOC Propensity',
  refi_propensity: 'Cotality Refi Propensity', entrada_eval_refi_propensity_score_v1: 'Cotality Refi Propensity',
  property_owners: 'Owner Link', property_owner_bridge: 'Owner Link', household_rollup: 'Owner Link',
  loan_applications: 'First-party LOS / Applications',
};

export function assetFreshness(assetKey: string): AssetFreshnessResponse {
  if (NOT_TRACKED_ASSETS.has(assetKey)) {
    return {
      asset_key: assetKey, title: assetKey, freshness: 'unavailable', last_updated: null, checked_at: null,
      status: 'unknown', basis: null, source: 'not_tracked',
    };
  }
  return {
    asset_key: assetKey, title: assetKey, freshness: 'fresh', last_updated: SNAPSHOT_AT, checked_at: SNAPSHOT_AT,
    status: 'live', basis: FRESHNESS_BASIS[assetKey] ?? B360, source: 'source_readiness',
  };
}

const HEADLINE = 'mip.semantics.portfolio_headline_metric_view';
const HOME_SQL =
  'WITH preview_population AS (  SELECT headline.borrower_id, headline.in_the_money, headline.is_high_opportunity, ' +
  'headline.offer_recommended, borrower.rate_spread_bps AS preview_rate_spread_bps ' +
  `  FROM ${HEADLINE} AS headline   LEFT JOIN mip.gold.borrower_360 AS borrower     ON borrower.borrower_id = headline.borrower_id) ` +
  'SELECT   COUNT(*) AS marketable_population,   SUM(CASE WHEN in_the_money THEN 1 ELSE 0 END) AS high_intent_leads, ' +
  '  SUM(CASE WHEN is_high_opportunity THEN 1 ELSE 0 END) AS top_tier_opportunities, ' +
  '  SUM(CASE WHEN offer_recommended THEN 1 ELSE 0 END) AS offers_recommended FROM preview_population';
const FUNNEL_SQL =
  'SELECT   CAST(COUNT(*) AS INT) AS population,   CAST(COALESCE(SUM(CASE WHEN is_high_opportunity THEN 1 ELSE 0 END), 0) AS INT)' +
  `     AS high_opportunity FROM ${HEADLINE}`;
const KPI_PROOF_SHAPES: Readonly<Record<KpiProofKey, { measure: string; predicates: string[]; sql: string; hash: string }>> = {
  'home.addressable_population': { measure: 'marketable_population', predicates: [], sql: HOME_SQL, hash: '1f2e3d4c5b6a7980' },
  'home.in_the_money': { measure: 'high_intent_leads', predicates: ['in_the_money = TRUE'], sql: HOME_SQL, hash: '1f2e3d4c5b6a7980' },
  'home.high_opportunity': { measure: 'top_tier_opportunities', predicates: ['is_high_opportunity = TRUE'], sql: HOME_SQL, hash: '1f2e3d4c5b6a7980' },
  'home.primary_offer_paths': { measure: 'offers_recommended', predicates: ['offer_recommended = TRUE'], sql: HOME_SQL, hash: '1f2e3d4c5b6a7980' },
  'funnel.population': { measure: 'population', predicates: [], sql: FUNNEL_SQL, hash: '0a9b8c7d6e5f4a3b' },
  'funnel.high_opportunity': { measure: 'high_opportunity', predicates: ['is_high_opportunity = TRUE'], sql: FUNNEL_SQL, hash: '0a9b8c7d6e5f4a3b' },
};

/** GET /api/kpi-proof?kpi= (flow-06): the governed statement behind one KPI card. */
export function kpiProof(kpiParam: string | null): KpiProofResponse {
  const kpi = (kpiParam && kpiParam in KPI_PROOF_SHAPES ? kpiParam : 'home.addressable_population') as KpiProofKey;
  const shape = KPI_PROOF_SHAPES[kpi];
  return {
    kpi,
    measure_column: shape.measure,
    predicates: shape.predicates,
    sql: shape.sql,
    sql_hash: shape.hash,
    params: [],
    relations: shape.sql.includes('borrower_360') ? ['mip.gold.borrower_360', HEADLINE] : [HEADLINE],
    note: `Column ${shape.measure} of this statement is the number on the card.`,
    databricks_sql_url: null,
  };
}

/** Curated bodies for the fixture contract exporter: the branches the default sample does not reach. */
export function contractSamples(): ContractSample[] {
  const attribution: HomeSummaryAttributionResponse = notSnapshottedAttribution('competitor_lien', '2026-07-09');
  return [
    { source: 'assetFreshness(not_tracked)', method: 'GET', pattern: '/api/assets/:assetKey/freshness', path: '/api/assets/fn_in_the_money/freshness', status: 200, body: assetFreshness('fn_in_the_money') },
    { source: 'kpiProof(funnel.high_opportunity)', method: 'GET', pattern: '/api/kpi-proof', path: '/api/kpi-proof', query: 'kpi=funnel.high_opportunity', status: 200, body: kpiProof('funnel.high_opportunity') },
    { source: 'homeAttribution(competitor_lien)', method: 'GET', pattern: '/api/home/summary/attribution', path: '/api/home/summary/attribution', query: 'measure=competitor_lien&baseline=2026-07-09', status: 200, body: attribution },
  ];
}

export const dataEstateFixtures: FixtureEntry[] = [
  fixture('GET', '/api/data-estate', () => json<DataEstateResponse>(DATA_ESTATE)),
  fixture('GET', '/api/admin/assets/:assetKey/metadata', ({ params }) => json<AssetMetadataResponse>(assetMetadata(params.assetKey))),
  fixture('GET', '/api/assets/:assetKey/freshness', ({ params }) => json<AssetFreshnessResponse>(assetFreshness(params.assetKey))),
  fixture('GET', '/api/kpi-proof', ({ query }) => json<KpiProofResponse>(kpiProof(query.get('kpi')))),
  fixture('GET', '/api/lineage/manifest', () =>
    json<LineageManifestResponse>({
      schema_version: 1,
      manifest_path: 'backend/resources/lineage_manifest.json',
      families: LINEAGE_FAMILIES.map(lineageFamily),
    }),
  ),
];
