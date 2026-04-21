-- =============================================================================
-- gold_funnel_snapshot_daily.sql  (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Record one row per (snapshot_date, state, segment_code) per
--            scoring refresh into `mip.gold.funnel_snapshot_daily`. Powers
--            delta_vs_prior_* (WoW / QoQ / YoY) on executive + segment
--            dashboards and on the lead-generation metric view.
--
-- Grain:     (snapshot_date, state, segment_code).
--            * state = 2-char state OR '_ALL' national rollup.
--            * segment_code = one of itm/listed/permit/investor/equity/
--              retention (exploded from borrower_360.segment_codes) OR
--              '_ALL' for the full cross-segment population.
--
-- Pattern:   MERGE INTO on (snapshot_date, state, segment_code), so running
--            this more than once on the same refresh date is idempotent —
--            the second run rewrites the day's row with the latest counts.
--
-- Inputs:    mip.gold.borrower_360              (population + scores)
--            mip.gold.borrower_lifecycle_state  (approval / outreach state)
--
-- Slice:     slice13-accuracy.
-- Data contract: metric views read delta_vs_prior_addressable /
--            delta_vs_prior_in_the_money / delta_vs_prior_approved as
--            (current_value - prior_value) / NULLIF(prior_value, 0) * 100
--            against snapshot_date = CURRENT_DATE() - INTERVAL 7 DAYS (WoW).
--
-- Run-order: after gold_borrower_lifecycle_state.sql in the scoring refresh.
-- =============================================================================

MERGE INTO mip.gold.funnel_snapshot_daily AS t
USING (
  WITH exploded AS (
    SELECT
      b.borrower_id,
      b.state,
      sc AS segment_code,
      b.opportunity_score,
      b.in_the_money,
      b.recommended_offer_code,
      COALESCE(ls.approval_status, 'pending')  AS approval_status,
      COALESCE(ls.outreach_status, 'none')     AS outreach_status
    FROM mip.gold.borrower_360 AS b
    LEFT JOIN mip.gold.borrower_lifecycle_state AS ls
      ON ls.borrower_id = b.borrower_id
    LATERAL VIEW EXPLODE(b.segment_codes) s AS sc
  ),
  -- Each borrower also contributes to the '_ALL' segment row, so dashboards
  -- can read a single cross-segment line per state per day.
  all_segments AS (
    SELECT
      b.borrower_id,
      b.state,
      '_ALL'                                    AS segment_code,
      b.opportunity_score,
      b.in_the_money,
      b.recommended_offer_code,
      COALESCE(ls.approval_status, 'pending')   AS approval_status,
      COALESCE(ls.outreach_status, 'none')      AS outreach_status
    FROM mip.gold.borrower_360 AS b
    LEFT JOIN mip.gold.borrower_lifecycle_state AS ls
      ON ls.borrower_id = b.borrower_id
  ),
  unioned AS (
    SELECT * FROM exploded
    UNION ALL
    SELECT * FROM all_segments
  ),
  per_state AS (
    SELECT
      CURRENT_DATE()                                                              AS snapshot_date,
      state,
      segment_code,
      CAST(COUNT(*) AS INT)                                                       AS addressable_borrowers,
      CAST(SUM(CASE WHEN in_the_money THEN 1 ELSE 0 END) AS INT)                  AS in_the_money_borrowers,
      CAST(SUM(CASE WHEN opportunity_score >= 75 THEN 1 ELSE 0 END) AS INT)       AS high_opportunity_borrowers,
      CAST(SUM(CASE WHEN recommended_offer_code IS NOT NULL
                     AND recommended_offer_code <> 'nurture' THEN 1 ELSE 0 END) AS INT)
                                                                                  AS offer_recommended_borrowers,
      CAST(SUM(CASE WHEN approval_status = 'approved' THEN 1 ELSE 0 END) AS INT)  AS approved_borrowers,
      CAST(SUM(CASE WHEN outreach_status = 'actioned' THEN 1 ELSE 0 END) AS INT)  AS actioned_borrowers,
      CAST(ROUND(AVG(opportunity_score)) AS INT)                                  AS avg_opportunity_score,
      CURRENT_TIMESTAMP()                                                         AS snapshot_at
    FROM unioned
    GROUP BY state, segment_code
  ),
  national AS (
    SELECT
      CURRENT_DATE()                                                              AS snapshot_date,
      '_ALL'                                                                      AS state,
      segment_code,
      CAST(COUNT(*) AS INT)                                                       AS addressable_borrowers,
      CAST(SUM(CASE WHEN in_the_money THEN 1 ELSE 0 END) AS INT)                  AS in_the_money_borrowers,
      CAST(SUM(CASE WHEN opportunity_score >= 75 THEN 1 ELSE 0 END) AS INT)       AS high_opportunity_borrowers,
      CAST(SUM(CASE WHEN recommended_offer_code IS NOT NULL
                     AND recommended_offer_code <> 'nurture' THEN 1 ELSE 0 END) AS INT)
                                                                                  AS offer_recommended_borrowers,
      CAST(SUM(CASE WHEN approval_status = 'approved' THEN 1 ELSE 0 END) AS INT)  AS approved_borrowers,
      CAST(SUM(CASE WHEN outreach_status = 'actioned' THEN 1 ELSE 0 END) AS INT)  AS actioned_borrowers,
      CAST(ROUND(AVG(opportunity_score)) AS INT)                                  AS avg_opportunity_score,
      CURRENT_TIMESTAMP()                                                         AS snapshot_at
    FROM unioned
    GROUP BY segment_code
  )
  SELECT * FROM per_state
  UNION ALL
  SELECT * FROM national
) AS s
  ON t.snapshot_date = s.snapshot_date
  AND t.state         = s.state
  AND t.segment_code  = s.segment_code
WHEN MATCHED THEN UPDATE SET
  addressable_borrowers        = s.addressable_borrowers,
  in_the_money_borrowers       = s.in_the_money_borrowers,
  high_opportunity_borrowers   = s.high_opportunity_borrowers,
  offer_recommended_borrowers  = s.offer_recommended_borrowers,
  approved_borrowers           = s.approved_borrowers,
  actioned_borrowers           = s.actioned_borrowers,
  avg_opportunity_score        = s.avg_opportunity_score,
  snapshot_at                  = s.snapshot_at
WHEN NOT MATCHED THEN INSERT (
  snapshot_date, state, segment_code,
  addressable_borrowers, in_the_money_borrowers, high_opportunity_borrowers,
  offer_recommended_borrowers, approved_borrowers, actioned_borrowers,
  avg_opportunity_score, snapshot_at
) VALUES (
  s.snapshot_date, s.state, s.segment_code,
  s.addressable_borrowers, s.in_the_money_borrowers, s.high_opportunity_borrowers,
  s.offer_recommended_borrowers, s.approved_borrowers, s.actioned_borrowers,
  s.avg_opportunity_score, s.snapshot_at
);
