-- =============================================================================
-- silver_property_master.sql (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Idempotent MERGE that populates `mip.silver.property_master`
--            from `cotality_mortgage_data.corelogic.entrada_eval_property_
--            domain_v3`. Coverage follows whatever states are present in the
--            source share; no fixed-state filter is applied.
--
-- Grain:     One row per CLIP (1:1 with source).
-- PK (MERGE key): clip.
-- Geography contract: keep rows with a non-null situs_state; downstream
-- geography rollups discover state/county/ZIP coverage from the data.
-- Slice:     module0-real-data-slice2.
-- Data contract: docs/data-contract-module0.md §2.2.
--
-- PII posture:
--   - `owner_1_full_name` is HASHED at INSERT/UPDATE time into
--     `owner_name_hash` using sha2(LOWER(TRIM(name)) || ':' || salt, 256).
--     The salt is read from secret scope `mip`, key `pii-salt-v1`,
--     provisioned by scripts/deploy.sh step 4d (create-if-missing). There
--     is NO literal fallback (removed 2026-06-11, audit P1-4 — a
--     source-committed salt made hashing predictable, silently); a
--     missing secret fails this refresh visibly. The salt is NEVER
--     rotated: rotation changes every owner_name_hash across refreshes
--     and breaks owner-identity join stability between gold snapshots.
--     (borrower_id is salt-INDEPENDENT — xxhash64(clip) base-36 — so it
--     survives rotation; only owner-name-derived surfaces shift.)
--   - Raw `owner_1_full_name` is NEVER written to silver.
--   - `situs_street_address` is NEVER projected (dropped at the SELECT).
--   - `mailing_street_address` is NEVER projected; only `mailing_city` and
--     `mailing_state` are kept so `is_absentee` is computable here.
--
-- Type coercion notes (from share probe, Apr 2026):
--   - `owner_1_corporate_indicator` is STRING 'Y'/'N' (not BIGINT as first
--     probed). `CAST('Y' AS BOOLEAN)` silently returns NULL in Spark, so the
--     pre-slice13-Wave2 column collapsed to all NULL. We now match on
--     UPPER(TRIM(...)) = 'Y' so every true row is preserved.
--   - `last_foreclosure_transaction_date` is BIGINT yyyyMMdd ->
--     TO_DATE(CAST(... AS STRING), 'yyyyMMdd'). Zero / bad values -> NULL.
--   - `block_level_latitude` / `block_level_longitude` are DOUBLE in share.
--
-- Idempotency: MERGE on clip. Re-running the same day is a no-op on content
--              columns; `ingest_ts` and `_meta_batch_id` are refreshed.
-- =============================================================================

MERGE INTO mip.silver.property_master AS t
USING (
  SELECT
    clip,
    fips_county_code,
    situs_state,
    situs_city,
    -- Data contract §2.2 says situs_zip_code is ZIP5. ZIP+4 rows are
    -- truncated; short non-leading-zero-state fragments are nulled so gold
    -- never displays invalid 4-digit ZIPs. Four-digit values are only left-
    -- padded for jurisdictions whose real ZIPs can start with zero.
    CASE
      WHEN LENGTH(REGEXP_REPLACE(CAST(situs_zip_code AS STRING), '[^0-9]', '')) >= 5
        THEN SUBSTR(REGEXP_REPLACE(CAST(situs_zip_code AS STRING), '[^0-9]', ''), 1, 5)
      WHEN LENGTH(REGEXP_REPLACE(CAST(situs_zip_code AS STRING), '[^0-9]', '')) = 4
       AND UPPER(TRIM(situs_state)) IN ('CT','MA','ME','NH','NJ','RI','VT','PR','VI')
        THEN LPAD(REGEXP_REPLACE(CAST(situs_zip_code AS STRING), '[^0-9]', ''), 5, '0')
      ELSE NULL
    END                                                             AS situs_zip_code,
    situs_core_based_statistical_area_cbsa                          AS situs_cbsa_code,
    CAST(block_level_latitude  AS DOUBLE)                            AS situs_lat,
    CAST(block_level_longitude AS DOUBLE)                            AS situs_lon,
    owner_1_identifier                                               AS owner_link_id,
    -- PII HASH: raw owner_1_full_name is read here, hashed, and dropped.
    -- Salt comes from the Databricks secret scope `mip`/`pii-salt-v1`,
    -- provisioned by scripts/deploy.sh step 4d (create-if-missing, never
    -- rotated). 2026-06-11 audit P1-4: the old COALESCE fallback to a
    -- source-committed literal made hashing PREDICTABLE whenever the
    -- secret was missing — silently. A missing secret now fails this
    -- refresh visibly, matching the DLT path and the repo's
    -- no-silent-fallback contract.
    sha2(
      CONCAT(
        LOWER(TRIM(COALESCE(owner_1_full_name, ''))),
        ':',
        secret('mip', 'pii-salt-v1')
      ),
      256
    )                                                                 AS owner_name_hash,
    -- Share emits STRING 'Y'/'N'; CAST('Y' AS BOOLEAN) is NULL in Spark,
    -- so we coerce explicitly. See header comment (slice13 Wave-2 fix).
    (UPPER(TRIM(COALESCE(CAST(owner_1_corporate_indicator AS STRING), ''))) = 'Y')
                                                                     AS owner_is_corporate,
    owner_occupancy_code,
    mailing_city,
    mailing_state,
    CASE
      WHEN mailing_state IS NOT NULL
       AND UPPER(TRIM(mailing_state)) <> UPPER(TRIM(situs_state))
      THEN TRUE
      ELSE FALSE
    END                                                              AS is_absentee,
    foreclosure_stage_code,
    -- Share probe: last_foreclosure_transaction_date is BIGINT yyyyMMdd.
    -- NULLIF guards the common 0 sentinel; TRY_TO_DATE tolerates malformed rows.
    TRY_TO_DATE(CAST(NULLIF(last_foreclosure_transaction_date, 0) AS STRING), 'yyyyMMdd')
      AS last_foreclosure_date,
    CAST(year_built AS INT)                                          AS year_built,
    CAST(total_living_area_square_feet_all_bldgs AS INT)             AS living_area_sqft,
    CAST(total_number_of_bedrooms_all_bldgs AS INT)                  AS bedrooms,
    CAST(total_number_of_bathrooms AS DOUBLE)                        AS bathrooms,
    CAST(calculated_total_value AS BIGINT)                           AS calculated_total_value,
    CAST(assessed_total_value AS BIGINT)                             AS assessed_total_value,
    CAST(total_tax_amount AS DOUBLE)                                 AS total_tax_amount,
    CAST(tax_year AS INT)                                            AS tax_year,
    CURRENT_TIMESTAMP()                                              AS ingest_ts,
    CAST(:batch_id AS STRING)                                        AS _meta_batch_id
  FROM cotality_mortgage_data.corelogic.entrada_eval_property_domain_v3
  WHERE situs_state IS NOT NULL
    AND clip IS NOT NULL
  -- dedup guard (audit P2-10): share refreshes occasionally land duplicate
  -- clip; keep the newest by tax_year (the most recent assessment vintage on
  -- the record), then last_foreclosure_date / calculated_total_value as
  -- deterministic tiebreaks. Without this a duplicate key makes the MERGE fail
  -- (multiple source rows match one target row). ingest_ts is a single run-wide
  -- CURRENT_TIMESTAMP(), so it cannot order rows within a refresh.
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY clip
    ORDER BY tax_year DESC, last_foreclosure_date DESC, calculated_total_value DESC
  ) = 1
) AS s
  ON t.clip = s.clip
WHEN MATCHED THEN UPDATE SET
  fips_county_code       = s.fips_county_code,
  situs_state            = s.situs_state,
  situs_city             = s.situs_city,
  situs_zip_code         = s.situs_zip_code,
  situs_cbsa_code        = s.situs_cbsa_code,
  situs_lat              = s.situs_lat,
  situs_lon              = s.situs_lon,
  owner_link_id          = s.owner_link_id,
  owner_name_hash        = s.owner_name_hash,
  owner_is_corporate     = s.owner_is_corporate,
  owner_occupancy_code   = s.owner_occupancy_code,
  mailing_city           = s.mailing_city,
  mailing_state          = s.mailing_state,
  is_absentee            = s.is_absentee,
  foreclosure_stage_code = s.foreclosure_stage_code,
  last_foreclosure_date  = s.last_foreclosure_date,
  year_built             = s.year_built,
  living_area_sqft       = s.living_area_sqft,
  bedrooms               = s.bedrooms,
  bathrooms              = s.bathrooms,
  calculated_total_value = s.calculated_total_value,
  assessed_total_value   = s.assessed_total_value,
  total_tax_amount       = s.total_tax_amount,
  tax_year               = s.tax_year,
  ingest_ts              = s.ingest_ts,
  _meta_batch_id         = s._meta_batch_id
WHEN NOT MATCHED THEN INSERT (
  clip, fips_county_code, situs_state, situs_city, situs_zip_code,
  situs_cbsa_code, situs_lat, situs_lon, owner_link_id, owner_name_hash,
  owner_is_corporate, owner_occupancy_code, mailing_city, mailing_state,
  is_absentee, foreclosure_stage_code, last_foreclosure_date, year_built,
  living_area_sqft, bedrooms, bathrooms, calculated_total_value,
  assessed_total_value, total_tax_amount, tax_year, ingest_ts, _meta_batch_id
) VALUES (
  s.clip, s.fips_county_code, s.situs_state, s.situs_city, s.situs_zip_code,
  s.situs_cbsa_code, s.situs_lat, s.situs_lon, s.owner_link_id, s.owner_name_hash,
  s.owner_is_corporate, s.owner_occupancy_code, s.mailing_city, s.mailing_state,
  s.is_absentee, s.foreclosure_stage_code, s.last_foreclosure_date, s.year_built,
  s.living_area_sqft, s.bedrooms, s.bathrooms, s.calculated_total_value,
  s.assessed_total_value, s.total_tax_amount, s.tax_year, s.ingest_ts, s._meta_batch_id
);
