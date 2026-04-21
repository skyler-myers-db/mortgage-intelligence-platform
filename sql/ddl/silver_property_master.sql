-- =============================================================================
-- silver_property_master.sql
-- -----------------------------------------------------------------------------
-- Purpose:   DDL for `mip.silver.property_master`, the CLIP-grain property
--            snapshot table for Module 0. One row per CLIP, 1:1 with the
--            share's `entrada_eval_property_domain_v3`. Carries property
--            characteristics, owner IDs, geography, and derived flags that
--            the gold layer (`mip.gold.borrower_360`,
--            `gold.property_owner_bridge`) joins against.
--
-- Data contract reference: docs/data-contract-module0.md §2.2.
-- Slice:     module0-real-data-slice2 (silver lift from the Cotality share).
-- Source:    cotality_mortgage_data.corelogic.entrada_eval_property_domain_v3
--            (5.19M rows, CLIP 1:1).
--
-- Geography filter (non-negotiable, CLAUDE.md + data-contract-module0 §2):
--            WHERE situs_state IN ('IL','CA','FL','TX','WA','CO')
--            -- Applied in sql/transformations/silver_property_master.sql.
--            Single-metro demos are explicitly forbidden.
--
-- PII posture (NON-NEGOTIABLE, governance-real-data-review §1):
--            - `owner_1_full_name`            : HASHED at ingest to
--              `owner_name_hash` (sha2(LOWER(TRIM(name)) || ':' || salt, 256)).
--              The raw name is NEVER persisted in silver. See §7 of
--              data-contract-module0 and the transformation file for the
--              hash expression.
--            - `situs_street_address`         : NEVER landed in silver. Gold
--              emits `subject_property` as city/state/zip only.
--            - `mailing_street_address`       : NEVER landed. We only keep
--              `mailing_state` because gold `is_absentee` is derived from
--              (mailing_state != situs_state).
--            - `owner_1_identifier` (Owner Link) : kept as `owner_link_id`
--              for join; gold hashes to `owner_link_ref` before UI.
--
-- Columns (matches data-contract §2.2 minus raw PII columns the governance
-- review forbids silver from landing):
--
--            clip                    CLIP spine, 1:1 with lien_current.
--            fips_county_code        5-char FIPS for geo rollups.
--            situs_state             6-state filter guarantees ('IL','CA',
--                                    'FL','TX','WA','CO').
--            situs_city, situs_zip_code
--                                    Non-street geography for gold/UI.
--            situs_cbsa_code         Metro code used for geography drill-down.
--            situs_lat, situs_lon    Block-level, per share; gold snaps to
--                                    centroid before rendering.
--            owner_link_id           Cotality Owner Link (83% coverage).
--            owner_name_hash         sha2() of owner_1_full_name; see
--                                    transformation for salt sourcing.
--                                    Derived IN SILVER; raw name never lands.
--            owner_is_corporate      Corporate-owner flag from 1/0 indicator.
--            owner_occupancy_code    'O'/'A'/'T' per CoreLogic dictionary.
--            mailing_state, mailing_city
--                                    Kept so `is_absentee` is computable in
--                                    gold; street addresses dropped per
--                                    governance.
--            is_absentee             Boolean: mailing_state != situs_state.
--                                    Derived at silver to avoid re-reading
--                                    mailing columns in gold.
--            foreclosure_stage_code  Current distress stage snapshot.
--            last_foreclosure_date   Most recent FC transaction date.
--            year_built, living_area_sqft, bedrooms, bathrooms
--                                    Property attributes for fit scoring.
--            calculated_total_value, assessed_total_value
--                                    County market + assessed values.
--            total_tax_amount, tax_year
--                                    Tax fields; cohort-only in gold.
--            ingest_ts               CURRENT_TIMESTAMP() for audit.
--            _meta_batch_id          Lakeflow run correlation id (NULL-OK).
--
-- Clustering: Liquid clustering on (situs_state, situs_cbsa_code, clip) --
--            the gold projection always filters on state (6-state footprint)
--            and frequently drills to CBSA. At 5M rows clustering beats
--            partitioning; partitioning by situs_state would create 6
--            large partitions and hurt selective CBSA-level queries.
--
-- Idempotency: CREATE TABLE IF NOT EXISTS. The Lakeflow pipeline and the
--            transformation file produce the same schema and merge on the
--            declared PK (`clip`). No DROP+CREATE anywhere.
-- =============================================================================

CREATE TABLE IF NOT EXISTS mip.silver.property_master (
  clip                    STRING    NOT NULL COMMENT 'Cotality mastered property ID. PK, 1:1 with lien_current.',
  fips_county_code        STRING             COMMENT '5-char FIPS county code for geo rollups.',
  situs_state             STRING    NOT NULL COMMENT '2-char state code. Filter guarantees IN (IL, CA, FL, TX, WA, CO).',
  situs_city              STRING             COMMENT 'Situs city (non-PII at cohort granularity).',
  situs_zip_code          STRING             COMMENT '5-digit situs ZIP (STRING to preserve leading zeros).',
  situs_cbsa_code         STRING             COMMENT 'Core-Based Statistical Area (metro) code.',
  situs_lat               DOUBLE             COMMENT 'Block-level latitude from share; gold snaps to ZIP centroid before UI.',
  situs_lon               DOUBLE             COMMENT 'Block-level longitude from share; gold snaps to ZIP centroid before UI.',
  owner_link_id           STRING             COMMENT 'Cotality Owner Link (owner_1_identifier). 83% coverage.',
  owner_name_hash         STRING             COMMENT 'sha2(LOWER(TRIM(owner_1_full_name)) || salt, 256). Raw name never persisted.',
  owner_is_corporate      BOOLEAN            COMMENT 'Derived from owner_1_corporate_indicator (BIGINT 1/0).',
  owner_occupancy_code    STRING             COMMENT 'Owner-occupancy code: O=owner-occupied, A=absentee, T=tenant.',
  mailing_city            STRING             COMMENT 'Mailing city (kept for investor-flag derivation).',
  mailing_state           STRING             COMMENT 'Mailing state (kept for is_absentee derivation only; no street).',
  is_absentee             BOOLEAN            COMMENT 'TRUE when mailing_state != situs_state. Derived at silver.',
  foreclosure_stage_code  STRING             COMMENT 'Current distress stage snapshot from share.',
  last_foreclosure_date   DATE               COMMENT 'Most recent foreclosure transaction date.',
  year_built              INT                COMMENT 'Property year built.',
  living_area_sqft        INT                COMMENT 'Living area (all buildings), square feet.',
  bedrooms                INT                COMMENT 'Total bedrooms across all buildings.',
  bathrooms               DOUBLE             COMMENT 'Total bathrooms (half-bath math per share).',
  calculated_total_value  BIGINT             COMMENT 'County calculated total value, USD.',
  assessed_total_value    BIGINT             COMMENT 'Assessor total value, USD.',
  total_tax_amount        DOUBLE             COMMENT 'Property tax amount for `tax_year`.',
  tax_year                INT                COMMENT 'Tax year for `total_tax_amount`.',
  ingest_ts               TIMESTAMP NOT NULL COMMENT 'Silver ingest timestamp (CURRENT_TIMESTAMP at MERGE time).',
  _meta_batch_id          STRING             COMMENT 'Lakeflow pipeline run id for observability.'
)
USING DELTA
CLUSTER BY (situs_state, situs_cbsa_code, clip)
COMMENT 'CLIP-grain property snapshot lifted from cotality_mortgage_data.corelogic.entrada_eval_property_domain_v3 filtered to IN (IL, CA, FL, TX, WA, CO). Raw owner names + street addresses never land in silver; see docs/data-contract-module0.md §2.2 and docs/governance-real-data-review.md §1.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);
