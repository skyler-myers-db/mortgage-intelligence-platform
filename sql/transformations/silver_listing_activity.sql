-- =============================================================================
-- silver_listing_activity.sql (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Idempotent MERGE from Cotality MLS/Listings into
--            mip.silver.listing_activity. The source table has raw address,
--            public remarks, agent/contact, and lockbox fields; this projection
--            intentionally excludes them.
-- =============================================================================

MERGE INTO mip.silver.listing_activity AS t
USING (
  SELECT
    clip,
    UPPER(TRIM(COALESCE(situs_state, listing_address_state))) AS situs_state,
    CASE
      WHEN LENGTH(REGEXP_REPLACE(CAST(COALESCE(situs_zip_code, listing_address_zip_code) AS STRING), '[^0-9]', '')) >= 5
        THEN SUBSTR(REGEXP_REPLACE(CAST(COALESCE(situs_zip_code, listing_address_zip_code) AS STRING), '[^0-9]', ''), 1, 5)
      WHEN LENGTH(REGEXP_REPLACE(CAST(COALESCE(situs_zip_code, listing_address_zip_code) AS STRING), '[^0-9]', '')) = 4
       AND UPPER(TRIM(COALESCE(situs_state, listing_address_state))) IN ('CT','MA','ME','NH','NJ','RI','VT','PR','VI')
        THEN LPAD(REGEXP_REPLACE(CAST(COALESCE(situs_zip_code, listing_address_zip_code) AS STRING), '[^0-9]', ''), 5, '0')
      ELSE NULL
    END AS situs_zip_code,
    composite_listing_id_derived AS listing_record_id,
    listing_status_code_standardized AS listing_status_code,
    listing_status_category_code_standardized AS listing_status_category,
    listing_status_description,
    listing_type,
    TRY_TO_DATE(CAST(listing_date AS STRING)) AS listing_date,
    TRY_TO_TIMESTAMP(CAST(COALESCE(
      status_change_date_and_time_standardized,
      last_status_change_date,
      last_listing_date_and_time_standardized,
      listing_date,
      original_listing_date
    ) AS STRING)) AS listing_status_ts,
    CAST(current_listing_price AS BIGINT) AS listing_price,
    CAST(original_listing_price AS BIGINT) AS original_listing_price,
    CAST(COALESCE(days_on_market_dom_derived, days_on_market_dom, days_on_market_dom_cumulative) AS INT)
      AS days_on_market,
    COALESCE(listing_service_name_abbrev_standardized, listing_service_name_standardized) AS listing_service,
    UPPER(TRIM(COALESCE(most_recent_listing_indicator_derived, 'N'))) = 'Y' AS is_current_listing,
    (
      UPPER(TRIM(COALESCE(most_recent_listing_indicator_derived, 'N'))) = 'Y'
      AND listing_status_category_code_standardized IN ('A', 'U')
    ) AS is_active_listing,
    COALESCE(updated_at, _meta_loaded_at) AS source_updated_at,
    CURRENT_TIMESTAMP() AS ingest_ts,
    _meta_source_file_name,
    CAST(:batch_id AS STRING) AS _meta_batch_id
  FROM cotality_mortgage_data.corelogic.entrada_eval_mls_listing_v1
  WHERE clip IS NOT NULL
    AND UPPER(TRIM(COALESCE(situs_state, listing_address_state))) RLIKE '^[A-Z]{2}$'
    AND composite_listing_id_derived IS NOT NULL
) AS s
  ON t.clip = s.clip
 AND t.listing_record_id = s.listing_record_id
WHEN MATCHED THEN UPDATE SET
  situs_state = s.situs_state,
  situs_zip_code = s.situs_zip_code,
  listing_status_code = s.listing_status_code,
  listing_status_category = s.listing_status_category,
  listing_status_description = s.listing_status_description,
  listing_type = s.listing_type,
  listing_date = s.listing_date,
  listing_status_date = CAST(s.listing_status_ts AS DATE),
  listing_price = s.listing_price,
  original_listing_price = s.original_listing_price,
  days_on_market = s.days_on_market,
  listing_service = s.listing_service,
  is_current_listing = s.is_current_listing,
  is_active_listing = s.is_active_listing,
  source_updated_at = s.source_updated_at,
  ingest_ts = s.ingest_ts,
  _meta_source_file_name = s._meta_source_file_name,
  _meta_batch_id = s._meta_batch_id
WHEN NOT MATCHED THEN INSERT (
  clip, situs_state, situs_zip_code, listing_record_id,
  listing_status_code, listing_status_category, listing_status_description,
  listing_type, listing_date, listing_status_date, listing_price,
  original_listing_price, days_on_market, listing_service,
  is_current_listing, is_active_listing, source_updated_at,
  ingest_ts, _meta_source_file_name, _meta_batch_id
) VALUES (
  s.clip, s.situs_state, s.situs_zip_code, s.listing_record_id,
  s.listing_status_code, s.listing_status_category, s.listing_status_description,
  s.listing_type, s.listing_date, CAST(s.listing_status_ts AS DATE), s.listing_price,
  s.original_listing_price, s.days_on_market, s.listing_service,
  s.is_current_listing, s.is_active_listing, s.source_updated_at,
  s.ingest_ts, s._meta_source_file_name, s._meta_batch_id
);
