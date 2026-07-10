-- =============================================================================
-- fn_refi_propensity_heuristic
-- -----------------------------------------------------------------------------
-- Purpose:   Canonical Module 0 TRANSPARENT refinance-propensity heuristic
--            (S1.3). Drives the `refi_propensity` overlay segment. This is a
--            deterministic, fully published points table over observable
--            Cotality lien/AVM/MLS signals -- it is NOT a machine-learning
--            model and is deliberately distinct from Cotality's opaque
--            refi_propensity_score model feed (which the app surfaces
--            separately and never re-labels as this heuristic).
--
-- Owner:     data-modeler (Mortgage Intelligence Platform, Module 0)
--
-- Published points table (also published verbatim in the app glossary
-- "Refi propensity (heuristic)" methodology entry -- keep both in lockstep):
--
--   Rate spread over par (rate_spread_bps):
--     >= 100 bps -> 40 pts;  75-99 -> 32;  50-74 -> 22;  25-49 -> 10;  else 0
--   First-lien seasoning (loan_age_months):
--     24-84 months -> 20 pts;  12-23 or 85-120 -> 10;  else 0
--   Available equity (equity_pct):
--     >= 20% -> 20 pts;  10-19% -> 10;  else 0
--   Balance worth refinancing (estimated_upb, USD):
--     >= 150000 -> 10 pts;  75000-149999 -> 5;  else 0
--   Not listed for sale (listed_for_sale):
--     FALSE/NULL -> 10 pts;  TRUE -> 0 (an active MLS listing signals a sale,
--     not a refinance)
--
--   Score = sum of points, range 0..100. Segment membership threshold is
--   >= 60 (applied by gold.borrower_360, not baked here).
--
-- NULLs:     NULL numeric inputs contribute 0 points for that component
--            ("no data" must never read as refi intent). NULL
--            listed_for_sale is treated as not-listed (matches the
--            COALESCE(is_active_listing, FALSE) contract in gold).
--
-- Determinism: Pure integer arithmetic, no nondeterministic calls. Safe for
--            gold materializations and metric views. Python parity:
--            backend/services/scoring.py::refi_propensity_heuristic, pinned
--            by tests/fixtures/refi_propensity_heuristic_golden.json.
-- =============================================================================

CREATE OR REPLACE FUNCTION mip.gold.fn_refi_propensity_heuristic(
  rate_spread_bps INT,
  loan_age_months INT,
  equity_pct      INT,
  estimated_upb   BIGINT,
  listed_for_sale BOOLEAN
)
RETURNS INT
DETERMINISTIC
COMMENT 'Module 0 transparent deterministic refi-propensity heuristic (S1.3). Published points table: spread >=100->40/75->32/50->22/25->10; seasoning 24-84mo->20, 12-23 or 85-120mo->10; equity >=20->20/>=10->10; UPB >=150k->10/>=75k->5; not listed ->10. Range 0..100; segment threshold >=60 applied in gold. NOT the Cotality refi propensity model. Parity: scoring.py::refi_propensity_heuristic + tests/fixtures/refi_propensity_heuristic_golden.json.'
RETURN
  CASE
    WHEN COALESCE(rate_spread_bps, 0) >= 100 THEN 40
    WHEN COALESCE(rate_spread_bps, 0) >= 75  THEN 32
    WHEN COALESCE(rate_spread_bps, 0) >= 50  THEN 22
    WHEN COALESCE(rate_spread_bps, 0) >= 25  THEN 10
    ELSE 0
  END
  + CASE
      WHEN loan_age_months IS NULL THEN 0
      WHEN loan_age_months BETWEEN 24 AND 84  THEN 20
      WHEN loan_age_months BETWEEN 12 AND 23  THEN 10
      WHEN loan_age_months BETWEEN 85 AND 120 THEN 10
      ELSE 0
    END
  + CASE
      WHEN COALESCE(equity_pct, 0) >= 20 THEN 20
      WHEN COALESCE(equity_pct, 0) >= 10 THEN 10
      ELSE 0
    END
  + CASE
      WHEN COALESCE(estimated_upb, 0) >= 150000 THEN 10
      WHEN COALESCE(estimated_upb, 0) >= 75000  THEN 5
      ELSE 0
    END
  + CASE WHEN COALESCE(listed_for_sale, FALSE) THEN 0 ELSE 10 END;
