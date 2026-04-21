-- =============================================================================
-- silver_owner_property_bridge.sql
-- -----------------------------------------------------------------------------
-- Purpose:   DDL for `mip_demo.silver.owner_property_bridge`, a
--            silver-layer Owner-Link rollup. One row per distinct
--            `owner_link_id` (from property_master) carrying portfolio-level
--            aggregates: property count, total AVM, total equity, open-lien
--            sum, and state/CBSA dispersion. Gold's
--            `gold.property_owner_bridge` reads this table to stamp
--            investor/multi-property signals onto `borrower_360`.
--
-- Data contract reference: docs/data-contract-module0.md §3.1
--            (`gold.property_owner_bridge`) is the authoritative downstream
--            projection. This silver table exists because (a) gold should
--            not re-aggregate 5M property rows per refresh -- the agg belongs
--            at silver so gold becomes a cheap JOIN -- and (b) keeping the
--            Owner-Link rollup in silver lets the bridge be reused by future
--            metric views without duplicating the aggregation.
--
-- Slice:     module0-real-data-slice2 (silver lift from the Cotality share).
-- Source:    silver.property_master (CLIP-grain, state-filtered) ⟕
--            silver.lien_current    (CLIP-grain, state-filtered).
--            Both upstreams are themselves state-filtered to IN (IL, CA,
--            FL, TX, WA, CO), so the 6-state footprint is inherited via the
--            join and does NOT need to be re-filtered here. The
--            transformation file still includes the state-filter comment so
--            the slice-2 contract test can detect the intent at the
--            transformation layer for every silver table.
--
-- PII posture:
--            No PII columns -- this is a rollup of opaque owner_link_id's
--            plus numeric aggregates.
--
-- Columns:
--            owner_link_id               PK. Cotality Owner Link.
--            related_property_count      COUNT(DISTINCT clip) for this owner.
--            corporate_property_count    Count where property_master.owner_is_corporate.
--            absentee_property_count     Count where property_master.is_absentee.
--            distinct_states_count       COUNT(DISTINCT situs_state).
--            distinct_cbsa_count         COUNT(DISTINCT situs_cbsa_code).
--            total_avm_value             SUM(lien_current.avm_value).
--            total_open_lien_balance     SUM(lien_current.total_open_lien_balance).
--            total_estimated_equity      SUM(lien_current.estimated_equity).
--            primary_clip                MAX(clip) WHERE owner_occupancy_code='O'.
--            ingest_ts, _meta_batch_id   Audit metadata.
--
-- Clustering: Liquid cluster on (owner_link_id) -- gold joins 1:1 on this
--            column. Row count is ~3.44M distinct Owner-Link IDs across the
--            6-state footprint (gap-analysis §1), so clustering beats
--            partitioning.
--
-- Idempotency: CREATE TABLE IF NOT EXISTS. Transformation MERGEs on
--            `owner_link_id`.
-- =============================================================================

CREATE TABLE IF NOT EXISTS mip_demo.silver.owner_property_bridge (
  owner_link_id              STRING    NOT NULL COMMENT 'Cotality Owner Link (property_master.owner_link_id). PK.',
  related_property_count     INT       NOT NULL COMMENT 'Count of distinct CLIPs tied to this Owner Link in the 6-state footprint.',
  corporate_property_count   INT       NOT NULL COMMENT 'Number of related properties with owner_is_corporate=TRUE.',
  absentee_property_count    INT       NOT NULL COMMENT 'Number of related properties with is_absentee=TRUE.',
  distinct_states_count      INT       NOT NULL COMMENT 'Number of distinct situs_state values across related properties.',
  distinct_cbsa_count        INT       NOT NULL COMMENT 'Number of distinct situs_cbsa_code values across related properties.',
  total_avm_value            BIGINT             COMMENT 'SUM(lien_current.avm_value) across related properties.',
  total_open_lien_balance    BIGINT             COMMENT 'SUM(lien_current.total_open_lien_balance) across related properties.',
  total_estimated_equity     BIGINT             COMMENT 'SUM(lien_current.estimated_equity) across related properties.',
  primary_clip               STRING             COMMENT 'MAX(clip) where owner_occupancy_code = ''O''; NULL if no owner-occupant in set.',
  ingest_ts                  TIMESTAMP NOT NULL COMMENT 'Silver ingest timestamp (CURRENT_TIMESTAMP at MERGE time).',
  _meta_batch_id             STRING             COMMENT 'Lakeflow pipeline run id for observability.'
)
USING DELTA
CLUSTER BY (owner_link_id)
COMMENT 'Owner-Link rollup derived from silver.property_master and silver.lien_current. Feeds gold.property_owner_bridge and investor-segment signals. 6-state filter is inherited from upstream silver. See docs/data-contract-module0.md §3.1.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);
