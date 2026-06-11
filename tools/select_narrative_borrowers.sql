-- =============================================================================
-- select_narrative_borrowers.sql
-- -----------------------------------------------------------------------------
-- Purpose:  Re-select the five REAL borrower IDs pinned by the Lakebase
--           narrative seed (lakebase/seed_campaigns.sql approvals block).
--           Run this against the live warehouse whenever a Cotality share
--           refresh drops one of the pinned CLIPs, then update BOTH the
--           borrower_id literals and the rationale stats in the seed so
--           the approvals keep joining to real dossiers with matching
--           numbers (2026-06-11 audit P1-5).
--
-- Slots (each mirrors a campaign's criteria so the row is narratively
-- consistent with the campaign it hangs off):
--   refi_approve     -> campaign 1 (refi, states IL/CA/WA/CO), ITM, top score
--   cashout_approve  -> campaign 2 (cash-out, states IL/FL/TX), top equity
--   heloc_approve    -> campaign 3 (HELOC), heloc/refi_plus_heloc NBO
--   refi_hold        -> campaign 1, marginal spread just over the 75 bps floor
--   investor_reject  -> campaign 2, max Owner Link related-property count
--
-- Determinism: every slot orders by its business metric then borrower_id
-- so reruns against unchanged data return identical rows.
-- =============================================================================

WITH base AS (
  SELECT borrower_id, state, rate_spread_bps, equity_pct, opportunity_score,
         recommended_offer_code, is_investor, in_the_money,
         related_property_count
  FROM mip.gold.borrower_360
  WHERE marketing_eligible = TRUE
)
SELECT 'refi_approve' AS slot, * FROM (
  SELECT * FROM base
  WHERE recommended_offer_code = 'refi' AND in_the_money
    AND state IN ('IL','CA','WA','CO')
  ORDER BY opportunity_score DESC, borrower_id ASC LIMIT 1)
UNION ALL
SELECT 'cashout_approve', * FROM (
  SELECT * FROM base
  WHERE recommended_offer_code = 'cash_out' AND state IN ('IL','FL','TX')
  ORDER BY equity_pct DESC, opportunity_score DESC, borrower_id ASC LIMIT 1)
UNION ALL
SELECT 'heloc_approve', * FROM (
  SELECT * FROM base
  WHERE recommended_offer_code IN ('heloc', 'refi_plus_heloc')
  ORDER BY opportunity_score DESC, borrower_id ASC LIMIT 1)
UNION ALL
SELECT 'refi_hold', * FROM (
  SELECT * FROM base
  WHERE recommended_offer_code = 'refi'
    AND rate_spread_bps BETWEEN 76 AND 95
    AND state IN ('IL','CA','WA','CO')
  ORDER BY opportunity_score DESC, borrower_id ASC LIMIT 1)
UNION ALL
SELECT 'investor_reject', * FROM (
  SELECT * FROM base
  WHERE is_investor = TRUE AND recommended_offer_code = 'cash_out'
    AND state IN ('IL','FL','TX')
  ORDER BY related_property_count DESC, opportunity_score DESC, borrower_id ASC
  LIMIT 1);
