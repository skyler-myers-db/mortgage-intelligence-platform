-- =============================================================================
-- gold_source_readiness.sql (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Populate `mip.gold.source_readiness` via CTAS. This keeps the
--            Admin source-readiness panel green without granting the running
--            Databricks App principal direct access to `mip.silver.*`.
--
-- Pattern:   CREATE OR REPLACE TABLE ... AS SELECT.
-- =============================================================================

CREATE OR REPLACE TABLE mip.gold.source_readiness AS
WITH refresh_anchor AS (
  SELECT refresh_at AS checked_at
  FROM mip.ref.refresh_run_state
  ORDER BY captured_at DESC
  LIMIT 1
),
source_rows AS (
  SELECT
    1 AS sort_order,
    'Cotality Public Records' AS source_name,
    CASE WHEN COUNT(*) > 0 THEN 'live' ELSE 'configured_empty' END AS status,
    COUNT(*) AS row_count,
    MAX(ingest_ts) AS last_updated,
    CASE WHEN COUNT(*) > 0
      THEN 'Delta Share · nightly'
      ELSE 'Delta Share configured but property master is empty'
    END AS note,
    'mip.silver.property_master' AS source_table,
    FALSE AS synthetic_demo
  FROM mip.silver.property_master

  UNION ALL

  SELECT
    2 AS sort_order,
    'Voluntary Lien' AS source_name,
    CASE WHEN COUNT(*) > 0 THEN 'live' ELSE 'configured_empty' END AS status,
    COUNT(*) AS row_count,
    MAX(ingest_ts) AS last_updated,
    CASE WHEN COUNT(*) > 0
      THEN 'Delta Share · nightly'
      ELSE 'Delta Share configured but lien table is empty'
    END AS note,
    'mip.silver.lien_current' AS source_table,
    FALSE AS synthetic_demo
  FROM mip.silver.lien_current

  UNION ALL

  SELECT
    3 AS sort_order,
    'MMA Mortgage Analytics' AS source_name,
    CASE WHEN COUNT(*) > 0 THEN 'live' ELSE 'configured_empty' END AS status,
    COUNT(*) AS row_count,
    MAX(ingest_ts) AS last_updated,
    CASE WHEN COUNT(*) > 0
      THEN 'Delta Share · nightly'
      ELSE 'Delta Share configured but mortgage events are empty'
    END AS note,
    'mip.silver.mortgage_events' AS source_table,
    FALSE AS synthetic_demo
  FROM mip.silver.mortgage_events

  UNION ALL

  SELECT
    4 AS sort_order,
    'CLIP' AS source_name,
    CASE WHEN COUNT(DISTINCT clip) > 0 THEN 'live' ELSE 'configured_empty' END AS status,
    COUNT(DISTINCT clip) AS row_count,
    MAX(ingest_ts) AS last_updated,
    CASE WHEN COUNT(DISTINCT clip) > 0
      THEN 'Mastered property id'
      ELSE 'Property master configured but CLIP is empty'
    END AS note,
    'mip.silver.property_master' AS source_table,
    FALSE AS synthetic_demo
  FROM mip.silver.property_master

  UNION ALL

  SELECT
    5 AS sort_order,
    'Owner Link' AS source_name,
    CASE WHEN COUNT(*) > 0 THEN 'live' ELSE 'configured_empty' END AS status,
    COUNT(*) AS row_count,
    MAX(ingest_ts) AS last_updated,
    CASE WHEN COUNT(*) > 0
      THEN 'Mastered owner graph'
      ELSE 'Owner graph configured but bridge is empty'
    END AS note,
    'mip.silver.owner_property_bridge' AS source_table,
    FALSE AS synthetic_demo
  FROM mip.silver.owner_property_bridge

  UNION ALL

  SELECT
    6 AS sort_order,
    'AVM' AS source_name,
    CASE WHEN COUNT_IF(avm_value IS NOT NULL) > 0 THEN 'live' ELSE 'configured_empty' END AS status,
    COUNT_IF(avm_value IS NOT NULL) AS row_count,
    MAX(CASE
      WHEN avm_value IS NOT NULL THEN COALESCE(CAST(avm_as_of_date AS TIMESTAMP), ingest_ts)
      ELSE NULL
    END) AS last_updated,
    CASE WHEN COUNT_IF(avm_value IS NOT NULL) > 0
      THEN 'Delta Share · weekly; freshness uses AVM as-of date when supplied, otherwise lien ingest timestamp'
      ELSE 'Lien table configured but AVM values are empty'
    END AS note,
    'mip.silver.lien_current' AS source_table,
    FALSE AS synthetic_demo
  FROM mip.silver.lien_current

  UNION ALL

  SELECT
    7 AS sort_order,
    'FRED Market Rates' AS source_name,
    CASE
      WHEN COUNT_IF(series_id = 'MORTGAGE30US' AND is_latest AND source = 'fred'
        AND observation_week >= current_date() - INTERVAL 14 DAYS) > 0 THEN 'live'
      WHEN COUNT_IF(series_id = 'MORTGAGE30US' AND is_latest) > 0 THEN 'error'
      ELSE 'configured_empty'
    END AS status,
    COUNT_IF(series_id = 'MORTGAGE30US' AND is_latest) AS row_count,
    MAX(CASE WHEN series_id = 'MORTGAGE30US' AND is_latest THEN vintage_ts END) AS last_updated,
    CASE
      WHEN COUNT_IF(series_id = 'MORTGAGE30US' AND is_latest AND source = 'fred'
        AND observation_week >= current_date() - INTERVAL 14 DAYS) > 0
      THEN 'FRED MORTGAGE30US weekly market rate · live'
      WHEN COUNT_IF(series_id = 'MORTGAGE30US' AND is_latest AND source <> 'fred') > 0
      THEN 'Latest MORTGAGE30US row is seed or non-FRED; refresh required'
      WHEN COUNT_IF(series_id = 'MORTGAGE30US' AND is_latest) > 0
      THEN 'Latest MORTGAGE30US row is stale; refresh required'
      ELSE 'No latest MORTGAGE30US market-rate row'
    END AS note,
    'mip.silver.market_rates_weekly' AS source_table,
    FALSE AS synthetic_demo
  FROM mip.silver.market_rates_weekly

  UNION ALL

  SELECT
    8 AS sort_order,
    'First-party LOS / Applications' AS source_name,
    CASE
      WHEN COUNT(*) > 0 AND COUNT_IF(COALESCE(synthetic_demo, FALSE)) = COUNT(*) THEN 'demo_synthetic'
      WHEN COUNT(*) > 0 THEN 'live'
      ELSE 'not_configured'
    END AS status,
    COUNT(*) AS row_count,
    MAX(refreshed_at) AS last_updated,
    CASE WHEN COUNT(*) > 0 AND COUNT_IF(COALESCE(synthetic_demo, FALSE)) = COUNT(*)
      THEN 'Summit Mortgage synthetic LOS/application feed · connected'
      WHEN COUNT(*) > 0
      THEN 'Customer LOS/application feed · connected'
      ELSE 'Customer LOS/application feed has not been connected'
    END AS note,
    'mip.first_party.loan_applications' AS source_table,
    COUNT(*) > 0 AND COUNT_IF(COALESCE(synthetic_demo, FALSE)) = COUNT(*) AS synthetic_demo
  FROM mip.first_party.loan_applications

  UNION ALL

  SELECT
    9 AS sort_order,
    'First-party Servicing Portfolio' AS source_name,
    CASE
      WHEN COUNT(*) > 0 AND COUNT_IF(COALESCE(synthetic_demo, FALSE)) = COUNT(*) THEN 'demo_synthetic'
      WHEN COUNT(*) > 0 THEN 'live'
      ELSE 'not_configured'
    END AS status,
    COUNT(*) AS row_count,
    MAX(refreshed_at) AS last_updated,
    CASE WHEN COUNT(*) > 0 AND COUNT_IF(COALESCE(synthetic_demo, FALSE)) = COUNT(*)
      THEN 'Summit Mortgage synthetic servicing feed · connected'
      WHEN COUNT(*) > 0
      THEN 'Customer servicing feed · connected'
      ELSE 'Customer servicing feed has not been connected'
    END AS note,
    'mip.first_party.servicing_portfolio' AS source_table,
    COUNT(*) > 0 AND COUNT_IF(COALESCE(synthetic_demo, FALSE)) = COUNT(*) AS synthetic_demo
  FROM mip.first_party.servicing_portfolio

  UNION ALL

  SELECT
    10 AS sort_order,
    'First-party CRM / Campaigns' AS source_name,
    CASE
      WHEN COUNT(*) > 0 AND COUNT_IF(COALESCE(synthetic_demo, FALSE)) = COUNT(*) THEN 'demo_synthetic'
      WHEN COUNT(*) > 0 THEN 'live'
      ELSE 'not_configured'
    END AS status,
    COUNT(*) AS row_count,
    MAX(refreshed_at) AS last_updated,
    CASE WHEN COUNT(*) > 0 AND COUNT_IF(COALESCE(synthetic_demo, FALSE)) = COUNT(*)
      THEN 'Summit Mortgage synthetic CRM/campaign feed · connected'
      WHEN COUNT(*) > 0
      THEN 'Customer CRM/campaign feed · connected'
      ELSE 'Customer CRM/campaign feed has not been connected'
    END AS note,
    'mip.first_party.crm_campaign_membership' AS source_table,
    COUNT(*) > 0 AND COUNT_IF(COALESCE(synthetic_demo, FALSE)) = COUNT(*) AS synthetic_demo
  FROM mip.first_party.crm_campaign_membership

  UNION ALL

  SELECT
    11 AS sort_order,
    'First-party Customer Interactions' AS source_name,
    CASE
      WHEN COUNT(*) > 0 AND COUNT_IF(COALESCE(synthetic_demo, FALSE)) = COUNT(*) THEN 'demo_synthetic'
      WHEN COUNT(*) > 0 THEN 'live'
      ELSE 'not_configured'
    END AS status,
    COUNT(*) AS row_count,
    MAX(refreshed_at) AS last_updated,
    CASE WHEN COUNT(*) > 0 AND COUNT_IF(COALESCE(synthetic_demo, FALSE)) = COUNT(*)
      THEN 'Summit Mortgage synthetic interaction feed · connected'
      WHEN COUNT(*) > 0
      THEN 'Customer interaction feed · connected'
      ELSE 'Customer interaction feed has not been connected'
    END AS note,
    'mip.first_party.customer_interactions' AS source_table,
    COUNT(*) > 0 AND COUNT_IF(COALESCE(synthetic_demo, FALSE)) = COUNT(*) AS synthetic_demo
  FROM mip.first_party.customer_interactions

  UNION ALL

  SELECT
    12 AS sort_order,
    'First-party Product Balances' AS source_name,
    CASE
      WHEN COUNT(*) > 0 AND COUNT_IF(COALESCE(synthetic_demo, FALSE)) = COUNT(*) THEN 'demo_synthetic'
      WHEN COUNT(*) > 0 THEN 'live'
      ELSE 'not_configured'
    END AS status,
    COUNT(*) AS row_count,
    MAX(refreshed_at) AS last_updated,
    CASE WHEN COUNT(*) > 0 AND COUNT_IF(COALESCE(synthetic_demo, FALSE)) = COUNT(*)
      THEN 'Summit Mortgage synthetic banking-product feed · connected'
      WHEN COUNT(*) > 0
      THEN 'Customer banking-product feed · connected'
      ELSE 'Customer banking-product feed has not been connected'
    END AS note,
    'mip.first_party.product_balances' AS source_table,
    COUNT(*) > 0 AND COUNT_IF(COALESCE(synthetic_demo, FALSE)) = COUNT(*) AS synthetic_demo
  FROM mip.first_party.product_balances

  UNION ALL

  SELECT
    13 AS sort_order,
    'MLS' AS source_name,
    'roadmap' AS status,
    CAST(NULL AS BIGINT) AS row_count,
    CAST(NULL AS TIMESTAMP) AS last_updated,
    'Contracted · pending Cotality share' AS note,
    CAST(NULL AS STRING) AS source_table,
    FALSE AS synthetic_demo

  UNION ALL

  SELECT
    14 AS sort_order,
    'Building Permits' AS source_name,
    'roadmap' AS status,
    CAST(NULL AS BIGINT) AS row_count,
    CAST(NULL AS TIMESTAMP) AS last_updated,
    'Contracted · pending Cotality share' AS note,
    CAST(NULL AS STRING) AS source_table,
    FALSE AS synthetic_demo

  UNION ALL

  SELECT
    15 AS sort_order,
    'UC Gold Borrower 360' AS source_name,
    CASE WHEN COUNT(*) > 0 THEN 'live' ELSE 'configured_empty' END AS status,
    COUNT(*) AS row_count,
    MAX(refreshed_at) AS last_updated,
    CASE WHEN COUNT(*) > 0
      THEN 'Governed borrower profile table · refreshed'
      ELSE 'Governed borrower profile table is empty'
    END AS note,
    'mip.gold.borrower_360' AS source_table,
    FALSE AS synthetic_demo
  FROM mip.gold.borrower_360

  UNION ALL

  SELECT
    16 AS sort_order,
    'UC Gold Lead Scores' AS source_name,
    CASE WHEN COUNT(*) > 0 THEN 'live' ELSE 'configured_empty' END AS status,
    COUNT(*) AS row_count,
    MAX(refreshed_at) AS last_updated,
    CASE WHEN COUNT(*) > 0
      THEN 'Deterministic score and offer primitives · refreshed'
      ELSE 'Score table is empty'
    END AS note,
    'mip.gold.lead_scores' AS source_table,
    FALSE AS synthetic_demo
  FROM mip.gold.lead_scores

  UNION ALL

  SELECT
    17 AS sort_order,
    'UC Gold Lead Population' AS source_name,
    CASE WHEN COUNT(*) > 0 THEN 'live' ELSE 'configured_empty' END AS status,
    COUNT(*) AS row_count,
    MAX(refreshed_at) AS last_updated,
    CASE WHEN COUNT(*) > 0
      THEN 'Ranked lead queue table · refreshed'
      ELSE 'Lead population table is empty'
    END AS note,
    'mip.gold.lead_population' AS source_table,
    FALSE AS synthetic_demo
  FROM mip.gold.lead_population

  UNION ALL

  SELECT
    18 AS sort_order,
    'UC Gold Segment Population' AS source_name,
    CASE WHEN COUNT(*) > 0 THEN 'live' ELSE 'configured_empty' END AS status,
    COUNT(*) AS row_count,
    MAX(refreshed_at) AS last_updated,
    CASE WHEN COUNT(*) > 0
      THEN 'Segment rollup table · refreshed'
      ELSE 'Segment population table is empty'
    END AS note,
    'mip.gold.segment_population' AS source_table,
    FALSE AS synthetic_demo
  FROM mip.gold.segment_population

  UNION ALL

  SELECT
    19 AS sort_order,
    'UC Gold Borrower Dossier' AS source_name,
    CASE WHEN COUNT(*) > 0 THEN 'live' ELSE 'configured_empty' END AS status,
    COUNT(*) AS row_count,
    MAX(refreshed_at) AS last_updated,
    CASE WHEN COUNT(*) > 0
      THEN 'Borrower dossier pre-join · refreshed'
      ELSE 'Borrower dossier table is empty'
    END AS note,
    'mip.gold.borrower_dossier' AS source_table,
    FALSE AS synthetic_demo
  FROM mip.gold.borrower_dossier
)
SELECT
  source_name,
  status,
  row_count,
  last_updated,
  note,
  source_table,
  synthetic_demo,
  sort_order,
  (SELECT checked_at FROM refresh_anchor) AS checked_at
FROM source_rows;
