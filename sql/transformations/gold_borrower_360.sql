-- =============================================================================
-- gold_borrower_360.sql (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Populate `mip.gold.borrower_360` via CTAS. One row per CLIP
--            joining silver.lien_current (SPINE) + silver.property_master +
--            gold.property_owner_bridge + silver.market_rates_weekly
--            (is_latest=TRUE).
--
-- Multi-catalog note (R6-01 prototype, 2026-04-23):
--            Every `mip.<schema>.<object>` identifier in this file is a
--            three-part name against the default tenant catalog `mip`. For
--            customers deploying with `--var="uc_catalog=<other>"`, the
--            intended substitution pattern is a bundle-deploy Jinja / sed
--            preprocessor that rewrites `mip.gold.`, `mip.silver.`,
--            `mip.ref.`, `mip.semantics.`, `mip.raw.` to the target catalog
--            (see docs/multi-catalog-plan.md for the full design). A
--            Databricks SQL-task can already interpolate bundle variables
--            via `${bundle.variables.uc_catalog}`, but that only works for
--            files listed under a `sql_task.parameters` block -- not for
--            inline three-part names inside the statement body. Templating
--            strategy is a packaging change, not a per-file edit.
--
-- Grain:     One row per clip.
-- Pattern:   CREATE OR REPLACE TABLE ... AS SELECT. Full rebuild is the
--            default refresh posture per data-contract §3.2. 5M rows on
--            serverless photon completes in minutes; idempotent on repeat
--            runs.
-- Slice:     module0-real-data-slice3.
-- Data contract: docs/data-contract-module0.md §3.2.
--
-- PII posture (non-negotiable, governance §1):
--   - display_name = 'Owner ' || SUBSTR(owner_name_hash, 1, 8). Synthesized.
--   - subject_property = 'Synthetic property · ' || city || ', ' || state ||
--                        ' ' || zip. No street.
--   - owner_name_hash is carried for internal correlation; router strips
--     before /api/* emission.
--
-- Live intent overlays:
--   - listed_for_sale comes from silver.listing_activity active/current
--     Cotality MLS rows. It drives the Listed segment, listing evidence, and
--     the purchase branch in fn_next_best_offer.
--   - has_permit remains FALSE until a filed building-permit source is
--     present in cotality_mortgage_data.corelogic. Cotality HELOC propensity
--     is integrated as its own governed model signal and never represented
--     as a filed permit.
--
-- Threshold convention: default thresholds live in data-contract §5 + UDF headers
-- and are seeded into mip.ref.offer_rules_config. The CTAS reads that governed
-- ref table once per refresh, falling back to contract defaults only if a row is
-- missing. Operators retune scoring by updating the ref table, then refreshing
-- gold; no SQL-code deploy is required for threshold changes.
--
-- Market rate: one row from silver.market_rates_weekly where is_latest=TRUE
-- and series_id='MORTGAGE30US'. This is a CROSS-like join via the CTE
-- `market` -- every borrower reads the same par rate at refresh time.
--
-- Estimated UPB: first-lien current balance is derived in gold via
-- mip.gold.fn_estimated_upb(first_pos_amount, first_pos_rate, months_elapsed)
-- plus any second-position amount. This closes gap A5 and keeps current lien,
-- equity dollars, equity_pct, and display LTV on the same amortized estimate.
--
-- trigger_timeline_json: materialized via SUBQUERY + to_json(collect_list(...))
-- of the top-3 evidence_events ORDER BY signal_rank. Prevents per-row fan-out
-- when the dossier renders.
--
-- Product label resolution: recommended_offer comes from an inline map MAP<
-- STRING, STRING> so gold emits the human label directly and the router
-- passes through. Matches NBO_PRODUCT_LABELS in scoring.py.
--
-- why_now: deterministic template per offer_code (data-contract §6). Uses
-- `FORMAT_STRING` and `CAST(... AS STRING)` to interpolate bps and equity
-- into the one-sentence template.
--
-- 2026-06-11 audit P2-8: CTAS re-declares clustering/comments/properties
-- because COR TABLE drops DDL metadata on every refresh. Clustering, column
-- COMMENTs, and TBLPROPERTIES mirror sql/ddl/gold_borrower_360.sql; the column
-- list order matches the final SELECT projection 1:1.
-- =============================================================================

CREATE OR REPLACE TABLE mip.gold.borrower_360
CLUSTER BY (state, clip)
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
)
AS
WITH market AS (
  -- Exactly one row: the latest MORTGAGE30US market rate. A missing row here
  -- is a hard failure -- gold cannot compute rate_spread without a par
  -- rate -- so the Lakeflow pipeline should ensure market_rates_weekly has
  -- is_latest data before this CTAS runs.
  SELECT rate_fraction AS market_rate_fraction
  FROM mip.silver.market_rates_weekly
  WHERE series_id = 'MORTGAGE30US' AND is_latest = TRUE
  LIMIT 1
),
refresh_anchor AS (
  SELECT refresh_at
  FROM mip.ref.refresh_run_state
  ORDER BY captured_at DESC
  LIMIT 1
),
rules AS (
  SELECT
    CAST(COALESCE(MAX(CASE WHEN key = 'mip_min_spread_bps'           THEN value END), 75.0) AS INT) AS min_spread_bps,
    CAST(COALESCE(MAX(CASE WHEN key = 'mip_min_equity_pct'           THEN value END), 15.0) AS INT) AS min_equity_pct,
    CAST(COALESCE(MAX(CASE WHEN key = 'mip_heloc_equity_min_pct'     THEN value END), 35.0) AS INT) AS heloc_equity_min_pct,
    CAST(COALESCE(MAX(CASE WHEN key = 'mip_cashout_equity_min_pct'   THEN value END), 25.0) AS INT) AS cashout_equity_min_pct,
    CAST(COALESCE(MAX(CASE WHEN key = 'mip_retention_min_spread_bps' THEN value END), 50.0) AS INT) AS retention_min_spread_bps
  FROM mip.ref.offer_rules_config
),
-- Recent-event aggregates per CLIP feeding intent_trigger (last 90 days).
-- Intent_trigger itself is computed in gold.lead_scores, not here, but the
-- AVM-uplift evidence signal reads from recent AVMs; we expose the raw
-- counts on borrower_360 so WhyPanel can cite them without a fresh join.
base AS (
  SELECT
    lc.clip,
    CONCAT('B-', LPAD(UPPER(CONV(CAST(ABS(XXHASH64(lc.clip)) AS STRING), 10, 36)), 13, '0')) AS borrower_id,
    lc.situs_state                      AS state,
    CASE
      WHEN LENGTH(REGEXP_REPLACE(CAST(lc.situs_zip_code AS STRING), '[^0-9]', '')) = 5
        THEN REGEXP_REPLACE(CAST(lc.situs_zip_code AS STRING), '[^0-9]', '')
      WHEN LENGTH(REGEXP_REPLACE(CAST(lc.situs_zip_code AS STRING), '[^0-9]', '')) = 4
       AND UPPER(TRIM(lc.situs_state)) IN ('CT','MA','ME','NH','NJ','RI','VT','PR','VI')
        THEN LPAD(REGEXP_REPLACE(CAST(lc.situs_zip_code AS STRING), '[^0-9]', ''), 5, '0')
      ELSE NULL
    END                                 AS zip,
    pm.situs_city                       AS city,
    pm.situs_cbsa_code,
    -- 5-char FIPS county code from silver.property_master. Projected up so
    -- gold.county_rollup + gold.zip_rollup can aggregate natively without a
    -- ZIP->county crosswalk seed. Nullable: ~0.2% of silver rows have a
    -- missing fips_county_code (block-level geocode gap); those CLIPs land
    -- in the state rollup but not in any county/ZIP rollup.
    pm.fips_county_code                 AS county_fips_5,
    pm.owner_link_id,
    pm.owner_name_hash,
    pm.owner_is_corporate,
    pm.is_absentee,
    pm.owner_occupancy_code,
    pm.year_built,
    pm.bedrooms,
    pm.bathrooms,
    lc.avm_value,
    lc.total_open_lien_balance,
    lc.estimated_cltv,
    lc.first_pos_date,
    lc.first_pos_amount,
    -- Fractional mortgage rate after source-quality bounding. The synthetic
    -- Cotality seed can emit rare long-tail values above 1000 bps of spread
    -- (for example 84.56%). Those are generator artifacts, not mortgage
    -- economics. Keep the scoring path inside a defensible 1%-15% APR range:
    -- invalid lows become NULL (no rate signal), invalid highs clamp to 15%.
    CASE
      WHEN lc.first_pos_rate IS NULL THEN NULL
      WHEN lc.first_pos_rate < 0.01 THEN NULL
      WHEN lc.first_pos_rate > 0.15 THEN 0.15
      ELSE lc.first_pos_rate
    END                                 AS first_pos_rate,
    lc.first_pos_loan_type,
    lc.first_pos_lender_current,
    -- Normalised lender raw_key for the JOIN to mip.ref.lender_dictionary.
    -- Both sides are normalised with UPPER(TRIM(...)) so the match is case +
    -- trailing-whitespace insensitive. See sql/ref/lender_dictionary_seed.sql
    -- for the canonical raw_key set and tenant-specific aliases.
    CASE
      WHEN lc.first_pos_lender_current IS NULL THEN NULL
      ELSE UPPER(TRIM(lc.first_pos_lender_current))
    END                                 AS lender_raw_key,
    lc.second_pos_amount,
    COALESCE(pob.related_property_count, 1) AS related_property_count
  FROM mip.silver.lien_current AS lc
  LEFT JOIN mip.silver.property_master AS pm
    ON pm.clip = lc.clip
  LEFT JOIN mip.gold.property_owner_bridge AS pob
    ON pob.owner_link_id = pm.owner_link_id
  -- Coverage is data-driven. If the Cotality share expands or contracts,
  -- borrower_360 follows the refreshed silver source rows instead of a
  -- tenant/demo state seed.
  WHERE lc.situs_state IS NOT NULL
    AND lc.clip IS NOT NULL
),
upb_estimates AS (
  SELECT
    b.*,
    CAST(CASE
      WHEN b.first_pos_amount IS NOT NULL AND b.first_pos_amount > 0 THEN
        mip.gold.fn_estimated_upb(
          b.first_pos_amount,
          b.first_pos_rate,
          CASE
            WHEN b.first_pos_date IS NULL THEN NULL
            ELSE CAST(FLOOR(months_between(DATE(ra.refresh_at), b.first_pos_date)) AS INT)
          END
        )
        + COALESCE(b.second_pos_amount, 0)
      ELSE COALESCE(b.total_open_lien_balance, 0)
    END AS BIGINT) AS estimated_current_lien_balance
  FROM base AS b
  CROSS JOIN refresh_anchor AS ra
),
latest_listing AS (
  SELECT *
  FROM (
    SELECT
      la.*,
      ROW_NUMBER() OVER (
        PARTITION BY la.clip
        ORDER BY
          CASE WHEN la.is_active_listing THEN 0 ELSE 1 END,
          COALESCE(la.listing_status_date, la.listing_date, DATE(la.source_updated_at), DATE(la.ingest_ts)) DESC,
          la.listing_record_id
      ) AS rn
    FROM mip.silver.listing_activity AS la
    WHERE la.clip IS NOT NULL
      AND la.is_current_listing = TRUE
  ) ranked
  WHERE rn = 1
),
latest_heloc_propensity AS (
  SELECT *
  FROM (
    SELECT
      hp.*,
      ROW_NUMBER() OVER (
        PARTITION BY hp.clip
        ORDER BY COALESCE(hp.heloc_propensity_run_date, DATE(hp.source_updated_at), DATE(hp.ingest_ts)) DESC
      ) AS rn
    FROM mip.silver.heloc_propensity AS hp
    WHERE hp.clip IS NOT NULL
  ) ranked
  WHERE rn = 1
),
latest_refi_propensity AS (
  SELECT *
  FROM (
    SELECT
      rp.*,
      ROW_NUMBER() OVER (
        PARTITION BY rp.clip
        ORDER BY COALESCE(rp.refi_propensity_run_date, DATE(rp.source_updated_at), DATE(rp.ingest_ts)) DESC
      ) AS rn
    FROM mip.silver.refi_propensity AS rp
    WHERE rp.clip IS NOT NULL
  ) ranked
  WHERE rn = 1
),
-- Current-customer detection is dictionary-driven, not hardcoded to a
-- brand token. mip.ref.lender_dictionary is the tenant override point:
-- rows with is_competitor = FALSE are the tenant lender aliases; rows
-- with is_competitor = TRUE are public-demo-safe competitor aliases.
lender_ref AS (
  SELECT
    UPPER(TRIM(raw_key)) AS raw_key,
    display_name,
    is_competitor
  FROM mip.ref.lender_dictionary
),
tenant_display AS (
  SELECT COALESCE(MIN(display_name), 'Summit Mortgage') AS tenant_lender_ref
  FROM lender_ref
  WHERE COALESCE(NOT is_competitor, FALSE)
),
historical_tenant AS (
  SELECT
    pm.owner_link_id,
    COUNT(DISTINCT me.clip) AS historical_tenant_distinct_clips
  FROM mip.silver.mortgage_events AS me
  JOIN mip.silver.property_master AS pm
    ON pm.clip = me.clip
  JOIN lender_ref AS lr
    ON lr.raw_key = UPPER(TRIM(me.lender_name))
  WHERE me.lender_name IS NOT NULL
    AND COALESCE(NOT lr.is_competitor, FALSE)
    AND me.situs_state IS NOT NULL
    AND pm.owner_link_id IS NOT NULL
  GROUP BY pm.owner_link_id
),
first_party_servicing AS (
  SELECT
    borrower_id,
    COUNT(*) AS servicing_rows,
    COUNT_IF(servicing_status = 'active') > 0 AS has_active_servicing,
    COUNT_IF(servicing_status IN ('paid_off', 'transferred')) > 0 AS has_closed_servicing,
    COUNT_IF(COALESCE(synthetic_demo, FALSE)) > 0 AS synthetic_demo
  FROM mip.first_party.servicing_portfolio
  GROUP BY borrower_id
),
first_party_applications AS (
  SELECT
    borrower_id,
    COUNT(*) AS application_rows,
    COUNT_IF(application_status = 'funded') AS funded_application_count,
    COUNT_IF(application_at >= DATE_SUB(CURRENT_DATE(), 180)) > 0 AS has_recent_application,
    COUNT_IF(COALESCE(synthetic_demo, FALSE)) > 0 AS synthetic_demo
  FROM mip.first_party.loan_applications
  GROUP BY borrower_id
),
first_party_crm_rollup AS (
  SELECT
    borrower_id,
    COUNT(*) AS crm_rows,
    MAX(last_touch_at) AS last_touch_at,
    COUNT_IF(consent_status = 'opt_out') > 0 AS has_opt_out,
    COUNT_IF(consent_status = 'opt_in') > 0 AS has_opt_in,
    COUNT_IF(suppression_reason = 'do_not_contact') > 0 AS has_do_not_contact,
    COUNT_IF(suppression_reason = 'recent_contact_cap') > 0 AS has_recent_contact_cap,
    MAX(NULLIF(suppression_reason, '')) AS any_suppression_reason,
    COUNT_IF(COALESCE(synthetic_demo, FALSE)) > 0 AS synthetic_demo
  FROM mip.first_party.crm_campaign_membership
  GROUP BY borrower_id
),
first_party_crm AS (
  SELECT
    borrower_id,
    crm_rows,
    CASE
      WHEN has_opt_out THEN 'opt_out'
      WHEN has_opt_in THEN 'opt_in'
      ELSE 'unknown'
    END AS consent_status,
    CASE
      WHEN has_do_not_contact THEN 'do_not_contact'
      WHEN has_recent_contact_cap THEN 'recent_contact_cap'
      ELSE any_suppression_reason
    END AS suppression_reason,
    last_touch_at,
    CASE
      WHEN last_touch_at >= (SELECT refresh_at FROM refresh_anchor) - INTERVAL 30 DAYS
        THEN last_touch_at + INTERVAL 30 DAYS
      WHEN has_recent_contact_cap AND last_touch_at IS NOT NULL
        THEN last_touch_at + INTERVAL 30 DAYS
      ELSE NULL
    END AS eligible_recontact_at,
    (
      CASE
        WHEN has_opt_out THEN 'opt_out'
        WHEN has_opt_in THEN 'opt_in'
        ELSE 'unknown'
      END = 'opt_in'
      AND NOT has_do_not_contact
      AND NOT has_recent_contact_cap
      AND any_suppression_reason IS NULL
      AND (last_touch_at IS NULL OR last_touch_at < (SELECT refresh_at FROM refresh_anchor) - INTERVAL 30 DAYS)
    ) AS marketing_eligible,
    synthetic_demo
  FROM first_party_crm_rollup
),
first_party_interactions AS (
  SELECT
    borrower_id,
    COUNT(*) AS interaction_rows,
    COUNT_IF(outcome_code = 'positive' AND interaction_at >= DATE_SUB(CURRENT_DATE(), 180)) AS recent_positive_interactions,
    COUNT_IF(COALESCE(synthetic_demo, FALSE)) > 0 AS synthetic_demo
  FROM mip.first_party.customer_interactions
  GROUP BY borrower_id
),
first_party_products AS (
  SELECT
    borrower_id,
    COUNT(*) AS product_rows,
    MAX(relationship_tenure_months) AS max_relationship_tenure_months,
    COUNT_IF(COALESCE(synthetic_demo, FALSE)) > 0 AS synthetic_demo
  FROM mip.first_party.product_balances
  GROUP BY borrower_id
),
enriched AS (
  SELECT
    b.*,
    m.market_rate_fraction,
    -- Rate spread via frozen UDF. Both sides fractional.
    mip.gold.fn_rate_spread(b.first_pos_rate, m.market_rate_fraction) AS rate_spread_bps,
    -- Equity % is the capped available-equity companion to display LTV.
    -- Prefer the gold-estimated current lien balance so equity dollars,
    -- equity_pct, display LTV, and current_lien_balance all read from the
    -- same amortized UPB estimate. An underwater borrower can display LTV
    -- > 100 while equity_pct stays at 0 for scoring / outreach gating.
    -- The no-signal default stays 0, NOT the complement -- "no data" must
    -- never read as "free and clear" on a contact-prioritization surface.
    CAST(CASE
      WHEN b.avm_value IS NOT NULL AND b.avm_value > 0
      THEN GREATEST(
        0,
        LEAST(
          100,
          100 - GREATEST(0, ROUND(100.0 * COALESCE(b.estimated_current_lien_balance, 0) / b.avm_value))
        )
      )
      WHEN b.estimated_cltv IS NOT NULL AND b.estimated_cltv > 0
      THEN GREATEST(0, LEAST(100, 100 - GREATEST(0, ROUND(b.estimated_cltv))))
      ELSE 0
    END AS INT) AS equity_pct,
    CAST(GREATEST(0, COALESCE(b.avm_value, 0) - COALESCE(b.estimated_current_lien_balance, 0)) AS BIGINT)
      AS equity_estimate,
    -- LTV: display truth. Prefer AVM / gold-estimated lien math so the
    -- shown LTV ties to current_lien_balance and equity_estimate; fall
    -- back to Cotality-modeled estimated_cltv only when AVM is missing.
    -- Do not cap the upper bound: underwater borrowers should show LTV
    -- > 100 instead of a misleading 100% cap.
    CAST(
      GREATEST(0, CASE
        WHEN b.avm_value IS NOT NULL AND b.avm_value > 0
          THEN ROUND(100.0 * COALESCE(b.estimated_current_lien_balance, 0) / b.avm_value)
        WHEN b.estimated_cltv IS NOT NULL AND b.estimated_cltv > 0
          THEN ROUND(b.estimated_cltv)
        ELSE 0
      END)
    AS INT) AS ltv,
    -- is_investor derived boolean.
    (b.related_property_count >= 2
     OR COALESCE(b.owner_is_corporate, FALSE)
     OR COALESCE(b.is_absentee, FALSE)) AS is_investor,
    COALESCE(ht.historical_tenant_distinct_clips, 0) AS historical_tenant_distinct_clips,
    COALESCE(fps.servicing_rows, 0) AS fp_servicing_rows,
    COALESCE(fpa.application_rows, 0) AS fp_application_rows,
    COALESCE(fpc.crm_rows, 0) AS fp_crm_rows,
    COALESCE(fpi.interaction_rows, 0) AS fp_interaction_rows,
    COALESCE(fpp.product_rows, 0) AS fp_product_rows,
    COALESCE(fps.has_active_servicing, FALSE) AS fp_has_active_servicing,
    COALESCE(fps.has_closed_servicing, FALSE) AS fp_has_closed_servicing,
    COALESCE(fpa.funded_application_count, 0) AS fp_funded_application_count,
    COALESCE(fpa.has_recent_application, FALSE) AS fp_has_recent_application,
    COALESCE(fpc.marketing_eligible, FALSE) AS fp_marketing_eligible,
    COALESCE(fpc.consent_status, 'unknown') AS fp_consent_status,
    fpc.suppression_reason AS fp_suppression_reason,
    fpc.last_touch_at AS fp_last_touch_at,
    fpc.eligible_recontact_at AS fp_eligible_recontact_at,
    COALESCE(fpi.recent_positive_interactions, 0) AS fp_recent_positive_interactions,
    COALESCE(fpp.max_relationship_tenure_months, 0) AS fp_max_relationship_tenure_months,
    (
      COALESCE(fps.synthetic_demo, FALSE)
      OR COALESCE(fpa.synthetic_demo, FALSE)
      OR COALESCE(fpc.synthetic_demo, FALSE)
      OR COALESCE(fpi.synthetic_demo, FALSE)
      OR COALESCE(fpp.synthetic_demo, FALSE)
    ) AS fp_synthetic_demo,
    (
      CASE WHEN COALESCE(fps.servicing_rows, 0) > 0 THEN 1 ELSE 0 END
      + CASE WHEN COALESCE(fpa.application_rows, 0) > 0 THEN 1 ELSE 0 END
      + CASE WHEN COALESCE(fpc.crm_rows, 0) > 0 THEN 1 ELSE 0 END
      + CASE WHEN COALESCE(fpi.interaction_rows, 0) > 0 THEN 1 ELSE 0 END
      + CASE WHEN COALESCE(fpp.product_rows, 0) > 0 THEN 1 ELSE 0 END
    ) AS fp_relationship_depth,
    -- Current-customer detection combines the lender dictionary with optional
    -- first-party servicing rows. The latter lets the demo/customer lender show
    -- its own book even when the public-record current servicer is absent or
    -- generalized.
    (COALESCE(NOT lr.is_competitor, FALSE) OR COALESCE(fps.has_active_servicing, FALSE)) AS is_current_customer,
    (
      (
        COALESCE(ht.historical_tenant_distinct_clips, 0) > 0
        OR COALESCE(fps.has_closed_servicing, FALSE)
        OR COALESCE(fpa.funded_application_count, 0) > 0
      )
      AND NOT (COALESCE(NOT lr.is_competitor, FALSE) OR COALESCE(fps.has_active_servicing, FALSE))
    ) AS is_former_customer,
    -- Competitor lien: servicer known AND is NOT our tenant. Mirrors the
    -- customer detection above -- any row that lands as is_current_customer
    -- (either via dictionary match or first-party servicing) is NOT a
    -- competitor lien; everything else with a non-null lender string is.
    -- Preserves the "servicer known and != tenant" semantic so unknown
    -- third-party lenders keep lighting up retention + competitor-lien paths.
    (b.first_pos_lender_current IS NOT NULL
     AND NOT (COALESCE(NOT lr.is_competitor, FALSE) OR COALESCE(fps.has_active_servicing, FALSE))) AS is_competitor_lien,
    CASE
      WHEN b.first_pos_lender_current IS NULL THEN NULL
      WHEN COALESCE(fps.has_active_servicing, FALSE) THEN td.tenant_lender_ref
      WHEN COALESCE(NOT lr.is_competitor, FALSE) THEN lr.display_name
      ELSE COALESCE(lr.display_name, 'Competitor Other')
    END AS current_lender_ref,
    (COALESCE(b.owner_occupancy_code, '') = 'O') AS is_owner_occupied,
    -- Filed building permits are still not present as a Cotality source
    -- table. Keep the literal false separate from Cotality's model
    -- propensity signals so the app never claims a permit was filed when
    -- only a propensity score exists.
    CAST(FALSE AS BOOLEAN) AS has_permit,
    COALESCE(cl.is_active_listing, FALSE) AS listed_for_sale,
    cl.listing_status_category,
    cl.listing_status_description,
    cl.listing_date,
    cl.listing_status_date,
    -- Guard borrower-facing listing price against source-scale defects
    -- before it reaches gold / API / Genie metric views. Status + days on
    -- market remain visible even when the supplied price is implausible.
    CASE
      WHEN cl.listing_price IS NULL THEN NULL
      WHEN cl.listing_price < 25000 THEN NULL
      WHEN b.avm_value IS NOT NULL AND b.avm_value > 0
        AND (cl.listing_price < b.avm_value * 0.15 OR cl.listing_price > b.avm_value * 5.0) THEN NULL
      ELSE cl.listing_price
    END AS listing_price,
    cl.days_on_market AS listing_days_on_market,
    cl.listing_service,
    hp.heloc_propensity_score,
    hp.heloc_propensity_run_date,
    COALESCE(hp.heloc_propensity_score, 0) >= 700 AS has_heloc_propensity_trigger,
    rp.refi_propensity_score,
    rp.refi_propensity_run_date,
    COALESCE(rp.refi_propensity_score, 0) >= 700 AS has_refi_propensity_trigger
  FROM upb_estimates AS b
  CROSS JOIN market AS m
  CROSS JOIN tenant_display AS td
  LEFT JOIN lender_ref AS lr
    ON lr.raw_key = b.lender_raw_key
  LEFT JOIN latest_listing AS cl
    ON cl.clip = b.clip
  LEFT JOIN latest_heloc_propensity AS hp
    ON hp.clip = b.clip
  LEFT JOIN latest_refi_propensity AS rp
    ON rp.clip = b.clip
  LEFT JOIN historical_tenant AS ht
    ON ht.owner_link_id = b.owner_link_id
  LEFT JOIN first_party_servicing AS fps
    ON fps.borrower_id = b.borrower_id
  LEFT JOIN first_party_applications AS fpa
    ON fpa.borrower_id = b.borrower_id
  LEFT JOIN first_party_crm AS fpc
    ON fpc.borrower_id = b.borrower_id
  LEFT JOIN first_party_interactions AS fpi
    ON fpi.borrower_id = b.borrower_id
  LEFT JOIN first_party_products AS fpp
    ON fpp.borrower_id = b.borrower_id
),
-- Scored rows: bind the governed threshold row and call the frozen UDFs
-- for ITM + next-best-offer.
scored AS (
  SELECT
    e.*,
    r.min_spread_bps           AS min_spread_bps_applied,
    r.min_equity_pct           AS min_equity_pct_applied,
    r.heloc_equity_min_pct     AS heloc_equity_min_applied,
    r.cashout_equity_min_pct   AS cashout_equity_min_applied,
    r.retention_min_spread_bps AS retention_min_spread_applied,
    mip.gold.fn_in_the_money(
      e.rate_spread_bps, e.equity_pct, r.min_spread_bps, r.min_equity_pct
    ) AS in_the_money,
    mip.gold.fn_next_best_offer(
      e.rate_spread_bps,
      e.equity_pct,
      (e.has_permit OR e.has_heloc_propensity_trigger),
      e.listed_for_sale,
      e.is_investor,
      e.is_current_customer,
      e.is_competitor_lien,
      r.min_spread_bps,
      r.min_equity_pct,
      r.heloc_equity_min_pct,
      r.cashout_equity_min_pct,
      r.retention_min_spread_bps
    ) AS recommended_offer_code
  FROM enriched AS e
  CROSS JOIN rules AS r
),
-- Segment codes array (order matters for the segment stripe rendering).
with_segments AS (
  SELECT
    s.*,
    FILTER(
      ARRAY(
        CASE WHEN s.in_the_money                            THEN 'itm'       END,
        CASE WHEN s.listed_for_sale                         THEN 'listed'    END,
        CASE WHEN (s.has_permit OR s.has_heloc_propensity_trigger)
                                                            THEN 'permit'    END,
        CASE WHEN s.is_investor                             THEN 'investor'  END,
        -- Cotality can express "no second-position balance" as NULL or 0.
        CASE WHEN s.equity_pct >= s.heloc_equity_min_applied AND COALESCE(s.second_pos_amount, 0) = 0
                                                            THEN 'equity'    END,
        CASE WHEN s.is_current_customer
              AND (s.rate_spread_bps >= s.retention_min_spread_applied
                   OR s.is_competitor_lien
                   OR s.listed_for_sale)                    THEN 'retention' END
      ),
      x -> x IS NOT NULL
    ) AS segment_codes
  FROM scored AS s
),
-- Evidence counts per CLIP (feeds the `evidence` sub-score in lead_scores,
-- but we also need a rough count here to populate evidence_ids). Exclude
-- blocked feed placeholders and compliance-only rationale rows so adding
-- explainability evidence does not retune opportunity scores.
evidence_counts AS (
  SELECT clip, COUNT(*) AS evidence_event_count
  FROM mip.gold.evidence_events
  WHERE signal_type NOT IN ('permit', 'loan_type_fit')
  GROUP BY clip
),
-- Top-3 evidence timeline per CLIP, pre-materialized as JSON to avoid
-- per-row fan-out at read.
timeline AS (
  SELECT
    clip,
    to_json(collect_list(ev)) AS trigger_timeline_json,
    collect_list(evidence_id) AS evidence_ids
  FROM (
    SELECT
      clip,
      evidence_id,
      struct(
        evidence_id,
        source_product,
        source_table,
        signal_type,
        signal_value,
        display_text,
        confidence,
        `timestamp`
      ) AS ev,
      ROW_NUMBER() OVER (PARTITION BY clip ORDER BY signal_rank, evidence_id) AS rn
    FROM mip.gold.evidence_events
    WHERE signal_type <> 'permit'
  ) ranked
  WHERE rn <= 3
  GROUP BY clip
),
-- Sub-scores: economic_incentive + fit + relationship here; intent_trigger +
-- evidence live fully in gold.lead_scores but we need them NOW to compute
-- opportunity_score + confidence for borrower_360 (matches §3.2 contract).
-- Sub-score formulas (fix/copilot-batch-post-merge 2026-04-22):
--
-- The tiered CASE statements collapsed 5.16M borrowers into only a
-- handful of discrete (economic_incentive, fit, relationship, evidence)
-- buckets, which in turn collapsed `fn_lead_score` into just 3 unique
-- values across the top 500 of the ranked queue (66/67/68 at refresh
-- time). Fix: replace the tiered case with continuous (linear) blends
-- so small variation in inputs produces small variation in outputs and
-- the opportunity_score distribution actually spans a useful range.
--
-- Continuity invariants vs. the prior tiered formulas:
--   - economic_incentive: rows that clear BOTH the 200-bps/35% band
--     still score ~95+; rows that clear only the 0-bps lane still
--     score in the mid-30s. Monotonic in spread AND equity.
--   - fit: owner-occupant + CONV/FHA/VA still beats corporate, which
--     still beats the fallback. Monotonic in property-size signals.
--   - relationship: current-customer > competitor-lien > no-relation.
--     Customer tenure (historical_tenant_distinct_clips) feeds the
--     relationship sub-score below.
--   - evidence: unchanged (already continuous).
--
-- fn_lead_score itself is unchanged (frozen primitive; Python parity
-- test pins the weighted-sum math + banker's rounding). The
-- gold.lead_scores CTAS mirrors this formula 1:1 -- drift between the
-- two is a parity-test failure by construction (they recompute the same
-- sub-scores from the same inputs; the weighted blend is canonical).
subscores AS (
  SELECT
    w.clip,
    -- economic_incentive: continuous blend of spread + equity that
    -- saturates gently. spread is compressed via a log-style curve
    -- (via the sqrt shape) so very-high-spread borrowers don't all
    -- bunch at 100; equity contributes linearly.
    --   spread_pts = LEAST(55, ROUND(3 * sqrt(GREATEST(0, spread_bps)))) -- saturates at spread ~340 bps
    --   equity_pts = LEAST(50, ROUND(equity_pct * 0.5))                  -- linear, saturates at 100% equity
    -- A borrower with spread=246, equity=79 scores ~47 + ~40 = 87.
    -- A borrower with spread=735, equity=83 scores ~55 + ~42 = 97.
    -- A borrower with spread=100, equity=25 scores ~30 + ~13 = 43.
    CAST(LEAST(100, GREATEST(0,
        LEAST(55, CAST(ROUND(3 * sqrt(GREATEST(0, w.rate_spread_bps))) AS INT))
      + LEAST(50, CAST(ROUND(0.5 * LEAST(100, GREATEST(0, w.equity_pct))) AS INT))
    )) AS INT) AS economic_incentive,
    -- intent_trigger: live intent overlays now include current MLS listing,
    -- Cotality HELOC propensity, and Cotality refinance propensity. Filed
    -- building permits remain 0 until a true permit source lands. Continuous
    -- contributions from always-live signals. Sum cap is above 100 but the
    -- outer LEAST keeps the score bounded, so even top-tier borrowers rarely
    -- saturate -- separation in the top tail is the whole point of
    -- the 2026-04-22 fix.
    --   * 20 if is_competitor_lien (recapture trigger)
    --   * LEAST(25, 10 * (related_property_count - 1)) Owner Link signal
    --   * 0-30 continuous rate-drift: LEAST(30, ROUND(2 * sqrt(spread_bps)))
    --     (saturates gently around ~225 bps so top-band still varies)
    --   * LEAST(10, equity_pct / 10) continuous equity proxy
    --   * 8 bump for is_current_customer (soft retention intent)
    CAST(LEAST(100, GREATEST(0,
        20 * CASE WHEN w.is_competitor_lien THEN 1 ELSE 0 END
      + LEAST(25, GREATEST(0, (COALESCE(w.related_property_count, 1) - 1) * 10))
      + LEAST(30, CAST(ROUND(2 * sqrt(GREATEST(0, w.rate_spread_bps))) AS INT))
      + LEAST(10, GREATEST(0, CAST(w.equity_pct / 10 AS INT)))
      + CASE WHEN w.is_current_customer THEN 8 ELSE 0 END
      + CASE WHEN w.listed_for_sale THEN 18 ELSE 0 END
      + CASE WHEN w.has_heloc_propensity_trigger
          THEN LEAST(18, CAST(ROUND(COALESCE(w.heloc_propensity_score, 0) / 50.0) AS INT))
          ELSE 0
        END
      + CASE WHEN w.has_refi_propensity_trigger
          THEN LEAST(12, CAST(ROUND(COALESCE(w.refi_propensity_score, 0) / 85.0) AS INT))
          ELSE 0
        END
    )) AS INT) AS intent_trigger,
    -- fit: continuous over property-size features. Monotonic
    -- (owner-occupant + CONV/FHA/VA with many bedrooms beats everything).
    CAST(LEAST(100, GREATEST(0,
        CASE
          WHEN w.is_owner_occupied AND w.first_pos_loan_type IN ('CONV','FHA','VA') THEN 70
          WHEN w.is_owner_occupied                                                  THEN 60
          WHEN w.owner_is_corporate                                                 THEN 50
          ELSE 40
        END
      + LEAST(20, 4 * COALESCE(w.bedrooms, 0))
      + LEAST(10, 3 * CAST(COALESCE(w.bathrooms, 0) AS INT))
    )) AS INT) AS fit,
    -- relationship: continuous. Current/former customers get an owner-
    -- level historical-tenant bonus; competitor-lien stays distinct.
    -- Multi-property owners get a +10 nudge over the no-relation floor.
    CAST(LEAST(100, GREATEST(0,
      CASE
        WHEN w.is_current_customer
          THEN 70
        WHEN w.is_former_customer
          THEN 60
        WHEN w.is_competitor_lien
          THEN 55
        WHEN COALESCE(w.related_property_count, 1) > 1
          THEN 45
        ELSE 35
      END
      + CASE
          WHEN w.is_current_customer OR w.is_former_customer
            THEN LEAST(25, 5 * LEAST(5, w.historical_tenant_distinct_clips))
          ELSE LEAST(25, GREATEST(0, (COALESCE(w.related_property_count, 1) - 1) * 5))
        END
      + LEAST(12, 3 * COALESCE(w.fp_relationship_depth, 0))
      + LEAST(8, 4 * COALESCE(w.fp_recent_positive_interactions, 0))
      + CASE WHEN w.fp_has_recent_application THEN 5 ELSE 0 END
    )) AS INT) AS relationship,
    -- evidence: was LEAST(100, 20*count) which capped every borrower
    -- with >=5 events at 100 (median real count is 4, so ~50% of rows
    -- saturated). Swap to 10*count + sqrt(second_pos_amount/1000) --
    -- keeps the per-event linearity but spreads the top tail via a
    -- continuous lien-amount term so dossier-rich borrowers still beat
    -- dossier-sparse ones, without every row landing at 100.
    LEAST(100, GREATEST(0,
      10 * COALESCE(ec.evidence_event_count, 0)
      + CASE
          WHEN w.second_pos_amount IS NOT NULL AND w.second_pos_amount > 0
            THEN LEAST(20, CAST(ROUND(sqrt(w.second_pos_amount / 1000.0)) AS INT))
          ELSE 0
        END
    )) AS evidence
  FROM with_segments AS w
  LEFT JOIN evidence_counts AS ec ON ec.clip = w.clip
)
SELECT
  w.clip,
  -- Borrower ID derivation (slice13 Wave-2 fix):
  --   Prior formula `LPAD(ABS(XXHASH64(clip)) % 99999 + 10000, 5, '0')` collapsed
  --   5.16M CLIPs into ~90K synthetic IDs (avg 57 collisions per id, worst 688),
  --   so `SELECT ... WHERE borrower_id = :id LIMIT 1` returned a non-deterministic
  --   CLIP. We now widen to base36(ABS(XXHASH64(clip))) padded to 13 chars,
  --   giving 36^13 = ~1.7e20 slots for 5.16M rows -- collision probability negligible.
  --   CONV(..., 10, 36) is Spark's base converter; LPAD stabilises the string length.
  --   UPPER() normalises the base-36 output to 0-9A-Z so the
  --   `B-[0-9A-Z]{13}` contract the parity test pins is deterministic
  --   across engines (raised by Copilot 2026-04-22 — some SQL engines
  --   return lowercase base-36 digits).
  w.borrower_id,
  -- Round-3 hole-finder #6: NULL owner_name_hash used to render "Owner "
  -- (trailing space). Coalesce to the short borrower_id suffix so the
  -- rendered label is always readable.
  CASE
    WHEN w.owner_name_hash IS NULL OR LENGTH(TRIM(w.owner_name_hash)) = 0
      THEN CONCAT('Borrower ', LPAD(UPPER(CONV(CAST(ABS(XXHASH64(w.clip)) AS STRING), 10, 36)), 6, '0'))
    ELSE CONCAT('Owner ', SUBSTR(w.owner_name_hash, 1, 8))
  END                                                                                   AS display_name,
  w.city,
  w.state,
  w.zip,
  w.situs_cbsa_code,
  w.county_fips_5,
  w.segment_codes,
  w.equity_estimate,
  w.equity_pct,
  w.rate_spread_bps,
  w.market_rate_fraction,
  mip.gold.fn_lead_score(
    ss.economic_incentive, ss.intent_trigger, ss.fit, ss.relationship, ss.evidence
  )                                                                                  AS opportunity_score,
  CAST(ROUND((ss.economic_incentive + ss.intent_trigger + ss.fit
               + ss.relationship + ss.evidence) / 5.0) AS INT)                       AS confidence,
  w.recommended_offer_code,
  -- Human label map: matches NBO_PRODUCT_LABELS in scoring.py. Kept inline
  -- so gold is self-contained -- no runtime join required.
  CASE w.recommended_offer_code
    WHEN 'purchase'        THEN 'Purchase Mortgage'
    WHEN 'refi_plus_heloc' THEN 'Refinance + HELOC'
    WHEN 'heloc'           THEN 'HELOC'
    WHEN 'refi'            THEN 'Refinance'
    WHEN 'cash_out'        THEN 'Cash-out Refi'
    WHEN 'investor'        THEN 'Investor Product'
    WHEN 'retention'       THEN 'Retention'
    ELSE                        'Nurture'
  END                                                                               AS recommended_offer,
  -- why_now template (data-contract §6). Plain-English for business
  -- personas (loan officers, marketing leads, VPs of Lending). No bps,
  -- no threshold syntax, no internal jargon -- this string renders
  -- verbatim on Borrower 360 and is what a human reads before
  -- approving outreach. Updated 2026-04-22 (fix/copilot-batch-post-merge)
  -- to drop '+XXX bps (>= YY)' rule-engine phrasing.
  CASE w.recommended_offer_code
    WHEN 'refi_plus_heloc' THEN
      'The current mortgage appears meaningfully above today''s market reference rate, and the property has enough equity to review refinance and home-equity options together.'
    WHEN 'heloc' THEN
      'Home-equity signals suggest a conversation about available equity may be useful without replacing the first mortgage.'
    WHEN 'refi' THEN
      'The current mortgage appears above today''s market reference rate, and the property has enough equity to review refinance options.'
    WHEN 'cash_out' THEN
      'The borrower appears to have available equity, so a licensed loan officer can review whether a cash-out refinance would fit their goals.'
    WHEN 'purchase' THEN
      'The property is listed for sale, so the useful conversation is likely about financing the next home before closing.'
    WHEN 'investor' THEN
      CONCAT('Owner Link ties ', CAST(w.related_property_count AS STRING),
             ' related properties, so route the review to an investor-lending specialist.')
    WHEN 'retention' THEN
      'This current-customer relationship has signals worth reviewing, so prioritize a service-focused check-in before the borrower shops alternatives.'
    ELSE
      'No strong borrower benefit is active yet, so keep this borrower in nurture until a clearer signal appears.'
  END                                                                                AS why_now,
  COALESCE(tl.evidence_ids, ARRAY())                                                 AS evidence_ids,
  'pending'                                                                          AS approval_status,
  w.owner_link_id,
  -- Gold keeps the same ZIP5-or-null contract as silver. Invalid short
  -- fragments (for example a WA row with only 4 digits) are nulled in `base`;
  -- the synthetic property label falls back to the API redaction sentinel.
  CONCAT('Synthetic property · ', COALESCE(w.city, 'Unknown'), ', ',
         w.state, ' ', COALESCE(w.zip, '00000'))                                      AS subject_property,
  CAST(COALESCE(w.avm_value, 0) AS BIGINT)                                           AS avm_value,
  CAST(COALESCE(w.estimated_current_lien_balance, 0) AS BIGINT)                      AS current_lien_balance,
  -- current_rate in PERCENT form (5.75), matches Pydantic + mock_data.
  CAST(COALESCE(w.first_pos_rate * 100, 0.0) AS DOUBLE)                              AS current_rate,
  w.ltv,
  w.related_property_count,
  w.is_owner_occupied,
  COALESCE(w.is_absentee, FALSE)                                                     AS is_absentee,
  COALESCE(w.owner_is_corporate, FALSE)                                              AS is_corporate_owner,
  w.has_permit,
  w.listed_for_sale,
  w.listing_status_category,
  w.listing_status_description,
  w.listing_date,
  w.listing_status_date,
  w.listing_price,
  w.listing_days_on_market,
  w.listing_service,
  w.heloc_propensity_score,
  w.heloc_propensity_run_date,
  w.has_heloc_propensity_trigger,
  w.refi_propensity_score,
  w.refi_propensity_run_date,
  w.has_refi_propensity_trigger,
  w.is_investor,
  w.is_current_customer,
  w.is_former_customer,
  w.is_competitor_lien,
  (w.fp_relationship_depth > 0)                                                         AS has_first_party_relationship,
  CAST(LEAST(5, COALESCE(w.fp_relationship_depth, 0)) AS INT)                            AS first_party_relationship_depth,
  CAST(COALESCE(w.fp_recent_positive_interactions, 0) AS INT)                            AS first_party_recent_interactions,
  COALESCE(w.fp_has_recent_application, FALSE)                                           AS first_party_recent_application,
  COALESCE(w.fp_synthetic_demo, FALSE)                                                   AS first_party_synthetic_demo,
  COALESCE(w.fp_marketing_eligible, FALSE)                                                AS marketing_eligible,
  COALESCE(w.fp_consent_status, 'unknown')                                                AS consent_status,
  w.fp_suppression_reason                                                                 AS suppression_reason,
  w.fp_last_touch_at                                                                      AS last_touch_at,
  w.fp_eligible_recontact_at                                                              AS eligible_recontact_at,
  w.current_lender_ref,
  w.second_pos_amount,
  w.first_pos_loan_type,
  w.owner_name_hash,
  w.min_spread_bps_applied,
  w.min_equity_pct_applied,
  w.heloc_equity_min_applied,
  w.cashout_equity_min_applied,
  w.retention_min_spread_applied,
  w.in_the_money,
  COALESCE(tl.trigger_timeline_json, '[]')                                           AS trigger_timeline_json,
  -- refresh_at comes from mip.ref.refresh_run_state (captured once per run
  -- by the capture_refresh_timestamp seed task) so every gold CTAS agrees
  -- to the second. See audit-holes-round-3 #7.
  (SELECT refresh_at FROM refresh_anchor) AS refreshed_at
FROM with_segments AS w
LEFT JOIN subscores AS ss ON ss.clip = w.clip
LEFT JOIN timeline  AS tl ON tl.clip = w.clip;

-- Column comments re-applied post-CTAS (2026-06-11 audit P2-8 follow-up):
-- CREATE OR REPLACE drops DDL column comments on every refresh, and the
-- typeless CTAS column list is a PARSE_SYNTAX_ERROR on DBSQL (observed
-- live, run 2026-06-11). COMMENT ON COLUMN keeps the Genie grounding /
-- asset-page comments refresh-stable; the SQL file task executes the
-- statements in order.
COMMENT ON COLUMN mip.gold.borrower_360.clip IS 'Cotality CLIP. PK. Router maps to Borrower360.clip_id.';
COMMENT ON COLUMN mip.gold.borrower_360.borrower_id IS 'Synthetic stable id from CLIP: CONCAT("B-", LPAD(CONV(ABS(xxhash64(clip)), 10, 36), 13, "0")). Base36 encoding of the 64-bit hash, width 13 => 36^13 slots. No PII.';
COMMENT ON COLUMN mip.gold.borrower_360.display_name IS 'Synthesized label "Owner " || SUBSTR(owner_name_hash, 1, 8). Never a real name.';
COMMENT ON COLUMN mip.gold.borrower_360.city IS 'Situs city from property_master.';
COMMENT ON COLUMN mip.gold.borrower_360.state IS 'Situs state from refreshed source coverage.';
COMMENT ON COLUMN mip.gold.borrower_360.zip IS '5-digit situs ZIP.';
COMMENT ON COLUMN mip.gold.borrower_360.situs_cbsa_code IS 'CBSA metro code. Gold-only; used for geography drill-down.';
COMMENT ON COLUMN mip.gold.borrower_360.county_fips_5 IS '5-char FIPS county code (2-char state + 3-char county) from silver.property_master.fips_county_code. Feeds gold.county_rollup + gold.zip_rollup. NULL for the ~0.2% of rows where silver has no county geocode.';
COMMENT ON COLUMN mip.gold.borrower_360.segment_codes IS 'Ordered list of SegmentCode Literals (itm/listed/permit/investor/equity/retention) this borrower belongs to.';
COMMENT ON COLUMN mip.gold.borrower_360.equity_estimate IS 'USD: GREATEST(0, avm_value - estimated current lien balance). Current lien uses fn_estimated_upb(first_pos_amount, first_pos_rate, months_elapsed) plus second-position amount when first-lien inputs are present.';
COMMENT ON COLUMN mip.gold.borrower_360.equity_pct IS '0..100 int available-equity percentage from AVM and estimated current lien balance; falls back to Cotality estimated_cltv only when AVM is missing. Underwater borrowers clamp to 0 for scoring while display LTV can exceed 100. Feeds fn_in_the_money + fn_next_best_offer.';
COMMENT ON COLUMN mip.gold.borrower_360.rate_spread_bps IS 'fn_rate_spread(first_pos_rate, market_rate_fraction). Positive = above market = refi opportunity.';
COMMENT ON COLUMN mip.gold.borrower_360.market_rate_fraction IS 'Fractional market rate from silver.market_rates_weekly WHERE is_latest=TRUE. Router maps to WhyPanel.market_rate.';
COMMENT ON COLUMN mip.gold.borrower_360.opportunity_score IS 'fn_lead_score output. 0..100.';
COMMENT ON COLUMN mip.gold.borrower_360.confidence IS 'ROUND(mean(5 sub-scores)). 0..100. Matches mock_data._build_borrower.';
COMMENT ON COLUMN mip.gold.borrower_360.recommended_offer_code IS 'fn_next_best_offer output (lowercase code). Router resolves to human label via NBO_PRODUCT_LABELS.';
COMMENT ON COLUMN mip.gold.borrower_360.recommended_offer IS 'Human label for recommended_offer_code (resolved in SQL via product_labels map).';
COMMENT ON COLUMN mip.gold.borrower_360.why_now IS 'Deterministic one-sentence template per offer_code. No PII. See data-contract §6.';
COMMENT ON COLUMN mip.gold.borrower_360.evidence_ids IS 'Ordered evidence_ids from gold.evidence_events (ORDER BY signal_rank).';
COMMENT ON COLUMN mip.gold.borrower_360.approval_status IS 'Default "pending"; Lakebase authoritative for actual state.';
COMMENT ON COLUMN mip.gold.borrower_360.owner_link_id IS 'Cotality Owner Link id. Opaque Cotality identifier; not a direct PII risk.';
COMMENT ON COLUMN mip.gold.borrower_360.subject_property IS 'Synthetic city/state/ZIP5 string. No street address.';
COMMENT ON COLUMN mip.gold.borrower_360.avm_value IS 'COALESCE(avm_value, 0).';
COMMENT ON COLUMN mip.gold.borrower_360.current_lien_balance IS 'Estimated current lien balance in USD: fn_estimated_upb(first_pos_amount, first_pos_rate, months_elapsed) plus second-position amount when first-lien inputs are present; otherwise COALESCE(total_open_lien_balance, 0).';
COMMENT ON COLUMN mip.gold.borrower_360.current_rate IS 'PERCENT form (5.75, not 0.0575). Matches Pydantic current_rate and mock_data convention.';
COMMENT ON COLUMN mip.gold.borrower_360.ltv IS 'Display LTV int from estimated current lien balance divided by AVM when AVM is present; not upper-capped, so underwater borrowers may exceed 100.';
COMMENT ON COLUMN mip.gold.borrower_360.related_property_count IS 'COALESCE(property_owner_bridge.related_property_count, 1).';
COMMENT ON COLUMN mip.gold.borrower_360.is_owner_occupied IS 'owner_occupancy_code = "O". Feeds fit sub-score.';
COMMENT ON COLUMN mip.gold.borrower_360.is_absentee IS 'property_master.is_absentee. Feeds investor branch.';
COMMENT ON COLUMN mip.gold.borrower_360.is_corporate_owner IS 'property_master.owner_is_corporate. Feeds investor branch.';
COMMENT ON COLUMN mip.gold.borrower_360.has_permit IS 'Filed building-permit flag. FALSE until a true Cotality Building Permits source table is present.';
COMMENT ON COLUMN mip.gold.borrower_360.listed_for_sale IS 'TRUE when silver.listing_activity has a current active/under-contract Cotality MLS row for this CLIP.';
COMMENT ON COLUMN mip.gold.borrower_360.listing_status_category IS 'Cotality standardized MLS listing status category.';
COMMENT ON COLUMN mip.gold.borrower_360.listing_status_description IS 'Display-safe Cotality MLS status description. No address, remarks, agent, phone, or email.';
COMMENT ON COLUMN mip.gold.borrower_360.listing_date IS 'MLS listing date.';
COMMENT ON COLUMN mip.gold.borrower_360.listing_status_date IS 'Most recent MLS status/change date.';
COMMENT ON COLUMN mip.gold.borrower_360.listing_price IS 'Current MLS listing price in USD, when supplied.';
COMMENT ON COLUMN mip.gold.borrower_360.listing_days_on_market IS 'MLS days-on-market value, when supplied.';
COMMENT ON COLUMN mip.gold.borrower_360.listing_service IS 'MLS/listing service label when supplied. No agent or consumer contact data.';
COMMENT ON COLUMN mip.gold.borrower_360.heloc_propensity_score IS 'Cotality HELOC propensity score, 0..999 in the current feed. Model signal, not a permit filing.';
COMMENT ON COLUMN mip.gold.borrower_360.heloc_propensity_run_date IS 'Cotality HELOC propensity model run date.';
COMMENT ON COLUMN mip.gold.borrower_360.has_heloc_propensity_trigger IS 'TRUE when heloc_propensity_score >= 700. Drives HELOC Intent without setting has_permit.';
COMMENT ON COLUMN mip.gold.borrower_360.refi_propensity_score IS 'Cotality refinance propensity score, 0..999 in the current feed.';
COMMENT ON COLUMN mip.gold.borrower_360.refi_propensity_run_date IS 'Cotality refinance propensity model run date.';
COMMENT ON COLUMN mip.gold.borrower_360.has_refi_propensity_trigger IS 'TRUE when refi_propensity_score >= 700. Adds intent score context.';
COMMENT ON COLUMN mip.gold.borrower_360.is_investor IS 'Derived: related_property_count >= 2 OR is_corporate_owner OR is_absentee.';
COMMENT ON COLUMN mip.gold.borrower_360.is_current_customer IS 'Current-servicer relationship to tenant: governed lender_dictionary says non-competitor.';
COMMENT ON COLUMN mip.gold.borrower_360.is_former_customer IS 'Historical tenant-lender Owner Link relationship with no current tenant-serviced lien.';
COMMENT ON COLUMN mip.gold.borrower_360.is_competitor_lien IS 'Current servicer is known and not the tenant. Competitor/recapture signal; mutually exclusive with is_current_customer in the current CLIP-grain refresh path.';
COMMENT ON COLUMN mip.gold.borrower_360.has_first_party_relationship IS 'TRUE when LOS, servicing, CRM, interaction, or product-balance feeds resolve to this borrower.';
COMMENT ON COLUMN mip.gold.borrower_360.first_party_relationship_depth IS 'Bounded count of resolved first-party feed categories for relationship scoring.';
COMMENT ON COLUMN mip.gold.borrower_360.first_party_recent_interactions IS 'Recent call-center/digital interaction count resolved through first-party feeds.';
COMMENT ON COLUMN mip.gold.borrower_360.first_party_recent_application IS 'TRUE when a recent LOS/application event exists.';
COMMENT ON COLUMN mip.gold.borrower_360.first_party_synthetic_demo IS 'TRUE only when resolved first-party rows come from the Summit demo_synthetic seed.';
COMMENT ON COLUMN mip.gold.borrower_360.marketing_eligible IS 'TRUE only when latest first-party CRM consent is opt-in, no suppression exists, and the 30-day touch cap is clear. Campaign and draft APIs fail closed on FALSE.';
COMMENT ON COLUMN mip.gold.borrower_360.consent_status IS 'Controlled first-party CRM consent enum: opt_in / opt_out / unknown. No raw contact data.';
COMMENT ON COLUMN mip.gold.borrower_360.suppression_reason IS 'Controlled first-party CRM suppression reason, e.g. do_not_contact or recent_contact_cap.';
COMMENT ON COLUMN mip.gold.borrower_360.last_touch_at IS 'Most recent first-party marketing/contact touch timestamp used for frequency-cap enforcement.';
COMMENT ON COLUMN mip.gold.borrower_360.eligible_recontact_at IS 'Earliest timestamp the borrower can be contacted again when a frequency cap is active.';
COMMENT ON COLUMN mip.gold.borrower_360.current_lender_ref IS 'Public-demo-safe current-servicer reference: Summit Mortgage, Competitor A/B/etc., or Competitor Other. Never the raw Cotality lender string.';
COMMENT ON COLUMN mip.gold.borrower_360.second_pos_amount IS '2nd-lien balance passthrough; NULL or 0 both mean no active 2nd-lien. Feeds the equity segment clean-lien predicate.';
COMMENT ON COLUMN mip.gold.borrower_360.first_pos_loan_type IS '1st-lien loan type code (CONV / FHA / VA / etc). Feeds fit sub-score.';
COMMENT ON COLUMN mip.gold.borrower_360.owner_name_hash IS 'sha2(LOWER(TRIM(name)) || salt, 256) propagated from silver.property_master. Internal only -- router strips before /api/*.';
COMMENT ON COLUMN mip.gold.borrower_360.min_spread_bps_applied IS 'Threshold applied when computing ITM for THIS refresh. Carried so WhyPanel.min_spread_bps is the run-specific value.';
COMMENT ON COLUMN mip.gold.borrower_360.min_equity_pct_applied IS 'Equity threshold applied this refresh.';
COMMENT ON COLUMN mip.gold.borrower_360.heloc_equity_min_applied IS 'HELOC equity threshold applied this refresh (fn_next_best_offer branch 2/3 and equity segment).';
COMMENT ON COLUMN mip.gold.borrower_360.cashout_equity_min_applied IS 'Cash-out equity threshold applied this refresh (fn_next_best_offer branch 5).';
COMMENT ON COLUMN mip.gold.borrower_360.retention_min_spread_applied IS 'Retention spread threshold applied this refresh (fn_next_best_offer branch 7 and retention segment).';
COMMENT ON COLUMN mip.gold.borrower_360.in_the_money IS 'fn_in_the_money(rate_spread_bps, equity_pct, min_spread_bps_applied, min_equity_pct_applied).';
COMMENT ON COLUMN mip.gold.borrower_360.trigger_timeline_json IS 'JSON-encoded top-3 EvidenceEvent rows pre-materialized to avoid per-row fan-out at read. Router json_decodes into List[EvidenceEvent].';
COMMENT ON COLUMN mip.gold.borrower_360.refreshed_at IS 'Refresh timestamp; used as EvidenceDrawer footer provenance chip.';
