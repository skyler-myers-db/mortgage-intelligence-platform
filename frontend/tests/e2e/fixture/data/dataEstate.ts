/**
 * Data-estate fixtures: the source-readiness proof surface, the asset detail
 * route and the governed lineage manifest the EvidenceDrawer reads.
 */
import type {
  AssetMetadataResponse,
  DataEstateResponse,
  LineageManifestFamily,
  LineageManifestResponse,
} from '../../../../src/types';
import { fixture, json, type FixtureEntry } from '../mockApi';
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

export const dataEstateFixtures: FixtureEntry[] = [
  fixture('GET', '/api/data-estate', () => json<DataEstateResponse>(DATA_ESTATE)),
  fixture('GET', '/api/admin/assets/:assetKey/metadata', ({ params }) => json<AssetMetadataResponse>(assetMetadata(params.assetKey))),
  fixture('GET', '/api/lineage/manifest', () =>
    json<LineageManifestResponse>({
      schema_version: 1,
      manifest_path: 'backend/resources/lineage_manifest.json',
      families: LINEAGE_FAMILIES.map(lineageFamily),
    }),
  ),
];
