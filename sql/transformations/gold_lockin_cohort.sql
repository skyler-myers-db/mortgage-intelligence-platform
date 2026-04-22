-- =============================================================================
-- gold_lockin_cohort.sql (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Materialise `mip.gold.lockin_cohort` via CTAS. One row per
--            borrower who originated (or most-recently refinanced into) a
--            sub-3 % first-position mortgage between 2020-01-01 and
--            2022-12-31 — the "lock-in" cohort that will not rate-and-term
--            refi but is highly HELOC / cash-out / purchase-trade-up
--            addressable.
--
-- Grain:     One row per clip (subset of gold.borrower_360, inner-joined on
--            silver.lien_current for the origination fields).
-- Pattern:   CREATE OR REPLACE TABLE ... AS SELECT. Full rebuild every
--            `mip_refresh_scores` run, mirroring lead_population.
-- Slice:     slice13-accuracy (sample question 5 + campaign sizing).
--
-- Why a dedicated gold table (vs. a runtime filter):
--   - Keeps Genie inside the trusted `mip.gold.*` / `mip.semantics.*`
--     boundary per genie/trusted_assets.md; silver is out-of-scope.
--   - Pre-materialised so the single hottest cohort question answers in
--     milliseconds instead of scanning silver.
--   - Portable: every client that deploys the bundle gets this table for
--     free — no per-client configuration, no extra Genie training.
--
-- Cohort definition (frozen; data-contract-module0.md §3.9 after this slice):
--   first_pos_rate    < 0.03                                (fractional)
--   first_pos_date   IN [2020-01-01, 2022-12-31]           (inclusive)
--   first_pos_rate   IS NOT NULL AND first_pos_date IS NOT NULL
--
-- PII posture: same as gold.borrower_360 — synthesized display_name only,
-- no street / owner name / raw lender. All columns originate from
-- gold.borrower_360 or from silver.lien_current fields that carry no PII.
--
-- Self-contained refresh: operator or job runs
--   `databricks bundle run mip_refresh_scores -t dev`
-- after silver is current; no out-of-bundle steps.
-- =============================================================================

CREATE OR REPLACE TABLE mip.gold.lockin_cohort AS
SELECT
  b.clip,
  b.borrower_id,
  b.state,
  b.zip,
  b.situs_cbsa_code,
  b.city,
  b.segment_codes,
  b.opportunity_score,
  b.equity_estimate,
  b.equity_pct,
  b.rate_spread_bps,
  b.recommended_offer,
  lc.first_pos_date                        AS origination_date,
  lc.first_pos_rate                        AS origination_rate,
  lc.first_pos_loan_type                   AS first_pos_loan_type,
  lc.first_pos_term_months                 AS first_pos_term_months,
  YEAR(lc.first_pos_date)                  AS origination_year,
  -- `cohort_tag` is a stable human-readable string Genie can cite verbatim
  -- and dashboards can GROUP BY when new cohorts are added later (e.g.
  -- sub-4 % 2015–2019). Today every row carries 'sub3_2020_2022'.
  CAST('sub3_2020_2022' AS STRING)         AS cohort_tag,
  CURRENT_TIMESTAMP()                      AS refreshed_at
FROM mip.gold.borrower_360 AS b
JOIN mip.silver.lien_current AS lc
  ON lc.clip = b.clip
WHERE lc.first_pos_rate IS NOT NULL
  AND lc.first_pos_date IS NOT NULL
  AND lc.first_pos_rate < 0.03
  AND lc.first_pos_date >= DATE'2020-01-01'
  AND lc.first_pos_date <= DATE'2022-12-31';
