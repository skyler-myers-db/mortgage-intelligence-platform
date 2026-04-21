-- =============================================================================
-- silver_property_master.sql (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Idempotent MERGE that populates `mip.silver.property_master`
--            from `cotality_mortgage_data.corelogic.entrada_eval_property_
--            domain_v3`, filtered to the 6-state demo footprint.
--
-- Grain:     One row per CLIP (1:1 with source).
-- PK (MERGE key): clip.
-- Geography filter: WHERE situs_state IN ('IL','CA','FL','TX','WA','CO').
-- Slice:     module0-real-data-slice2.
-- Data contract: docs/data-contract-module0.md §2.2.
--
-- PII posture:
--   - `owner_1_full_name` is HASHED at INSERT/UPDATE time into
--     `owner_name_hash` using sha2(LOWER(TRIM(name)) || ':' || salt, 256).
--     The salt is read from secret scope `mip`, key `pii-salt-v1`
--     (governance-real-data-review §1 + data-contract §7). If the secret
--     is not configured, the MERGE uses the literal sentinel
--     `mip_pii_salt_v1` which is documented in the contract; rotating the
--     salt invalidates prior hashes (acceptable -- hashes are internal).
--   - Raw `owner_1_full_name` is NEVER written to silver.
--   - `situs_street_address` is NEVER projected (dropped at the SELECT).
--   - `mailing_street_address` is NEVER projected; only `mailing_city` and
--     `mailing_state` are kept so `is_absentee` is computable here.
--
-- Type coercion notes (from share probe, Apr 2026):
--   - `owner_1_corporate_indicator` is BIGINT (1/0) -> CAST AS BOOLEAN.
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
    situs_zip_code,
    situs_core_based_statistical_area_cbsa                          AS situs_cbsa_code,
    CAST(block_level_latitude  AS DOUBLE)                            AS situs_lat,
    CAST(block_level_longitude AS DOUBLE)                            AS situs_lon,
    owner_1_identifier                                               AS owner_link_id,
    -- PII HASH: raw owner_1_full_name is read here, hashed, and dropped.
    -- Salt comes from the Databricks secret scope; fallback to the literal
    -- named in data-contract-module0 §7 keeps this file runnable when the
    -- secret is not yet provisioned in a fresh workspace.
    sha2(
      CONCAT(
        LOWER(TRIM(COALESCE(owner_1_full_name, ''))),
        ':',
        COALESCE(
          TRY_CAST(secret('mip', 'pii-salt-v1') AS STRING),
          'mip_pii_salt_v1'
        )
      ),
      256
    )                                                                 AS owner_name_hash,
    CAST(COALESCE(owner_1_corporate_indicator, 0) AS BOOLEAN)        AS owner_is_corporate,
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
  WHERE situs_state IN ('IL','CA','FL','TX','WA','CO')
    AND clip IS NOT NULL
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
