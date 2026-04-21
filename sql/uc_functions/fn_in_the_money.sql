-- =============================================================================
-- fn_in_the_money
-- -----------------------------------------------------------------------------
-- Purpose:   Canonical Module 0 "In the Money" flag. Collapses the two
--            required economic conditions (rate spread and equity) into a
--            single BOOLEAN that drives segment membership, Lead Queue
--            filtering, and offer-eligibility logic.
--
-- Domain:    Per CLAUDE.md, "In the Money = economic incentive to transact,
--            usually rate spread and equity/LTV based." This function is the
--            single source of truth for that definition.
--
-- Owner:     data-modeler (Mortgage Intelligence Platform, Module 0)
--
-- Semantics: Returns TRUE iff BOTH
--                rate_spread_bps >= min_spread_bps   (strictly inclusive)
--              AND equity_pct     >= min_equity_pct  (strictly inclusive)
--            `>=` (not `>`) is deliberate: a borrower sitting exactly on the
--            policy threshold IS in the money. Golden fixtures pin the
--            boundary behavior explicitly so a future inequality flip would
--            fail parity loudly.
--
-- Thresholds: Taken as explicit inputs, NOT baked as UDF defaults. The
--            application layer carries thresholds from admin config so a
--            growth lead can tune aggressiveness without a SQL deploy.
--            Demo defaults (cited here, applied in backend/config and the
--            admin UI):
--                min_spread_bps  = 75
--                min_equity_pct  = 15
--            Under these defaults, the three canonical demo borrowers
--            (see fn_rate_spread.sql header) all evaluate TRUE — the demo
--            promise "act on these now" is preserved.
--
-- NULLs:     Any NULL input -> FALSE. Rationale: for a contact-prioritization
--            surface, "unknown" must not silently become "go" — treating
--            unknowns as "not ITM" is the safer default for outreach
--            decisions. This is the inverse of fn_lead_score's NULL policy
--            (score coerces to 0) because the downstream effect differs:
--            here, a stray NULL threshold would otherwise propagate through
--            `>=` and yield NULL, which ambiguates boolean sorts and
--            segment-count metrics.
--
-- Determinism: Pure boolean arithmetic, no nondeterministic calls. Safe
--            for use in gold materializations and metric views.
-- =============================================================================

CREATE OR REPLACE FUNCTION mip_demo.gold.fn_in_the_money(
  rate_spread_bps INT,
  equity_pct      INT,
  min_spread_bps  INT,
  min_equity_pct  INT
)
RETURNS BOOLEAN
DETERMINISTIC
COMMENT 'Module 0 canonical In-the-Money flag. TRUE iff rate_spread_bps >= min_spread_bps AND equity_pct >= min_equity_pct. NULL inputs return FALSE. Demo defaults: min_spread_bps=75, min_equity_pct=15 (supplied by application layer, not baked). See tests/fixtures/in_the_money_golden.json for parity fixtures.'
RETURN
  CASE
    WHEN rate_spread_bps IS NULL
      OR equity_pct      IS NULL
      OR min_spread_bps  IS NULL
      OR min_equity_pct  IS NULL
    THEN FALSE
    ELSE (rate_spread_bps >= min_spread_bps) AND (equity_pct >= min_equity_pct)
  END;
