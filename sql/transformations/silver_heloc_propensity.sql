-- =============================================================================
-- silver_heloc_propensity.sql (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Idempotent MERGE from Cotality HELOC propensity score into
--            mip.silver.heloc_propensity. This is a model propensity feed, not
--            a building-permit filing feed.
-- =============================================================================

MERGE INTO mip.silver.heloc_propensity AS t
USING (
  SELECT
    clip,
    UPPER(TRIM(situs_state)) AS situs_state,
    CASE
      WHEN LENGTH(REGEXP_REPLACE(CAST(situs_zip_code AS STRING), '[^0-9]', '')) >= 5
        THEN SUBSTR(REGEXP_REPLACE(CAST(situs_zip_code AS STRING), '[^0-9]', ''), 1, 5)
      WHEN LENGTH(REGEXP_REPLACE(CAST(situs_zip_code AS STRING), '[^0-9]', '')) = 4
       AND UPPER(TRIM(situs_state)) IN ('CT','MA','ME','NH','NJ','RI','VT','PR','VI')
        THEN LPAD(REGEXP_REPLACE(CAST(situs_zip_code AS STRING), '[^0-9]', ''), 5, '0')
      ELSE NULL
    END AS situs_zip_code,
    CAST(heloc_model_propensity_score AS INT) AS heloc_propensity_score,
    TRY_TO_DATE(CAST(NULLIF(heloc_model_propensity_score_run_date, 0) AS STRING), 'yyyyMMdd')
      AS heloc_propensity_run_date,
    _meta_loaded_at AS source_updated_at,
    CURRENT_TIMESTAMP() AS ingest_ts,
    _meta_source_file_name,
    CAST(:batch_id AS STRING) AS _meta_batch_id
  FROM cotality_mortgage_data.corelogic.entrada_eval_heloc_propensity_score_v1
  WHERE clip IS NOT NULL
    AND UPPER(TRIM(situs_state)) RLIKE '^[A-Z]{2}$'
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY clip
    ORDER BY heloc_model_propensity_score_run_date DESC NULLS LAST,
             _meta_loaded_at DESC NULLS LAST
  ) = 1
) AS s
  ON t.clip = s.clip
WHEN MATCHED THEN UPDATE SET
  situs_state = s.situs_state,
  situs_zip_code = s.situs_zip_code,
  heloc_propensity_score = s.heloc_propensity_score,
  heloc_propensity_run_date = s.heloc_propensity_run_date,
  source_updated_at = s.source_updated_at,
  ingest_ts = s.ingest_ts,
  _meta_source_file_name = s._meta_source_file_name,
  _meta_batch_id = s._meta_batch_id
WHEN NOT MATCHED THEN INSERT (
  clip, situs_state, situs_zip_code, heloc_propensity_score,
  heloc_propensity_run_date, source_updated_at, ingest_ts,
  _meta_source_file_name, _meta_batch_id
) VALUES (
  s.clip, s.situs_state, s.situs_zip_code, s.heloc_propensity_score,
  s.heloc_propensity_run_date, s.source_updated_at, s.ingest_ts,
  s._meta_source_file_name, s._meta_batch_id
);
