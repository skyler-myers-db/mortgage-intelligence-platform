-- =============================================================================
-- fn_rate_spread
-- -----------------------------------------------------------------------------
-- Purpose:   Canonical Module 0 rate-spread primitive. Returns the spread in
--            basis points between a borrower's current lien coupon and the
--            prevailing market/par rate. Consumed by fn_in_the_money, the
--            Segment Intelligence route, the Lead Queue evidence chips, and
--            Borrower 360 "why now" storytelling.
--
-- Owner:     data-modeler (Mortgage Intelligence Platform, Module 0)
--
-- Sign:      POSITIVE = current rate is ABOVE market = borrower is paying
--            too much = refi opportunity. This is the direction that drives
--            outreach, so we keep the sign intuitive to a human reading a
--            lead card rather than inverting for a "savings" framing.
--
-- Units:     Basis points (1% = 100 bps). Inputs are fractional rates
--            (5.75% expressed as 0.0575), matching the Borrower360 schema
--            and the Cotality voluntary-lien feed upstream.
--
-- Rounding:  Round-half-to-even (banker's rounding) via Databricks `bround`.
--            The Python scorer (backend/services/scoring.py, next slice) MUST
--            use Python 3's built-in `round()`, which also bankers-rounds,
--            to stay identical. Golden fixtures in
--            tests/fixtures/rate_spread_golden.json pin both.
--
-- NULLs:     Either input NULL -> return 0. Same rationale as fn_lead_score:
--            "no signal" == "no opportunity" is the safer default for a
--            contact-prioritization surface, and keeps the downstream
--            boolean from fn_in_the_money stable (rate_spread_bps is never
--            NULL, so the >= comparison never short-circuits on NULL).
--
-- Market baseline (Module 0 demo convention):
--            The demo fixes market_rate at 4.875% (0.04875). This is a
--            fictional par rate chosen so the three canonical demo borrowers
--            in backend/services/mock_data.py produce defensible spreads:
--                B-48291 (current 5.75%): +88 bps  (raw 87.5, bankers-rounds up)
--                B-48294 (current 6.75%): +188 bps (raw 187.5, bankers-rounds up)
--                B-48295 (current 6.50%): +162 bps (raw 162.5, bankers-rounds DOWN to even)
--            All three remain "in the money" under the default
--            min_spread_bps=75 threshold documented on fn_in_the_money.
--            Production deployments will source market_rate from a rate
--            feed table rather than a constant; the function signature
--            does not change.
--
-- Determinism: Pure arithmetic, no nondeterministic calls. Safe for use in
--            gold materializations and metric views.
-- =============================================================================

CREATE OR REPLACE FUNCTION mip.gold.fn_rate_spread(
  current_rate DOUBLE,
  market_rate  DOUBLE
)
RETURNS INT
DETERMINISTIC
COMMENT 'Module 0 canonical rate-spread primitive. Returns (current_rate - market_rate) in basis points, banker-rounded. Positive = borrower paying above market = refi opportunity. NULL inputs return 0. See tests/fixtures/rate_spread_golden.json for parity fixtures.'
RETURN
  CASE
    WHEN current_rate IS NULL OR market_rate IS NULL THEN 0
    ELSE CAST(BROUND((current_rate - market_rate) * 10000) AS INT)
  END;
