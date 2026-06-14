-- =============================================================================
-- fn_next_best_offer
-- -----------------------------------------------------------------------------
-- Purpose:   Canonical Module 0 Next-Best-Offer primitive. Returns a single
--            lowercase product code that names the offer the Lead Queue,
--            Borrower 360, and Offer Orchestrator surfaces should present.
--            This is a decision tree, not a weighted blend — the output is a
--            categorical label, not a score. Downstream UI renders the human
--            label via the `product_labels` map in
--            tests/fixtures/next_best_offer_golden.json.
--
-- Domain:    Per CLAUDE.md:
--              "HELOC/Cash-out candidates = strong equity and/or recent
--               permits/renovation signals."
--              "Listed for Sale = purchase mortgage opportunity."
--              "Investor/Multi-Property = Owner Link shows multiple properties
--               and transaction history."
--              "Retention/Recapture = current customer retention now;
--               former-customer recapture requires an upstream relationship
--               signal to be encoded before this frozen UDF is called."
--              "In the Money = economic incentive to transact, usually rate
--               spread and equity/LTV based."
--            This function is the single source of truth for turning those
--            signals into ONE recommended product.
--
-- Owner:     data-modeler (Mortgage Intelligence Platform, Module 0)
--
-- Returns:   One of eight lowercase codes, in priority order:
--              'purchase'        — Purchase Mortgage
--              'refi_plus_heloc' — Refinance + HELOC (cross-sell on refi)
--              'heloc'           — HELOC (permit/renovation driven)
--              'refi'            — Refinance
--              'cash_out'        — Cash-out Refi
--              'investor'        — Investor Product
--              'retention'       — Retention (customer relationship signal)
--              'nurture'         — Nurture / no-action
--
-- Priority: First match wins. The ordering is deliberate and encodes the
--            product's prioritization philosophy:
--
--   1. listed_for_sale
--        The listing is the strongest intent signal in the funnel — a borrower
--        actively selling has a defined need for a purchase mortgage on the
--        next home. It dominates every rate/equity signal because the lien on
--        the current property is about to be paid off anyway.
--
--   2. rate_spread_bps >= min_spread AND equity_pct >= heloc_equity_min
--        The "refi plus HELOC" cross-sell. If the borrower has BOTH economic
--        refi incentive AND enough equity to layer a HELOC on the same
--        conversation, that is the highest-revenue single offer we can make
--        — two products, one outreach. This branch sits ABOVE pure HELOC
--        because refi economics drive the conversation regardless of whether
--        a permit is on file.
--
--   3. has_permit AND equity_pct >= heloc_equity_min
--        The pure HELOC lane. `has_permit` is the legacy boolean slot for
--        HELOC-intent evidence; callers may pass filed permits OR a governed
--        HELOC-propensity trigger, while preserving separate source fields.
--        Strong equity makes the HELOC viable. Fires only when
--        refi economics are NOT strong enough to combine (branch 2 failed),
--        so this is specifically the "permit, no refi need" story.
--
--   4. rate_spread_bps >= min_spread AND equity_pct >= min_equity
--        Plain refinance. Economics are strong, but equity is not yet at
--        HELOC-threshold — so we lead with refi alone and leave cross-sell
--        for later cycles.
--
--   5. equity_pct >= cashout_equity_min
--        Cash-out refi. No rate economics, no permit, but enough equity to
--        pull cash. Lower priority than HELOC because HELOC is cheaper
--        capital and because cash-out without a rate trigger is a weaker pitch.
--
--   6. is_investor
--        Investor/multi-property treatment (Owner Link signal). Below the
--        equity branches because investor strategies are routed through a
--        different product desk and the owner-occupant economics above are
--        stronger leads when they fire.
--
--   7. is_current_customer AND (rate_spread_bps >= retention_min
--                               OR is_competitor_lien)
--        Retention. Lower rate bar (retention_min < min_spread) so we can
--        reach out earlier on existing relationships. The UDF also allows
--        an upstream recapture model to pass both is_current_customer=TRUE
--        and is_competitor_lien=TRUE; the current borrower_360 refresh path
--        derives both from the same current-servicer string, so those flags
--        are mutually exclusive there and the branch behaves as
--        current-customer rate-drift retention.
--
--   8. else
--        Nurture. No-action lane; keep in the funnel, do not surface to
--        the approval queue.
--
-- Thresholds: Five admin-tunable INT thresholds, all passed explicitly — NONE
--            are baked into the UDF. Rationale matches fn_in_the_money: a
--            growth lead should be able to tune aggressiveness through admin
--            config without a SQL deploy. Default thresholds (applied by
--            the application layer):
--              min_spread_bps       = 75
--              min_equity_pct       = 15
--              heloc_equity_min_pct = 35
--              cashout_equity_min   = 25
--              retention_min_spread = 50
--            `heloc_equity_min_pct > min_equity_pct` is intentional: HELOC
--            underwriting demands more equity cushion than plain refi.
--
-- NULLs:     Borrower-signal numeric NULLs coerce to 0 and boolean NULLs
--            coerce to FALSE. Threshold NULLs do NOT coerce to zero: a missing
--            threshold is a configuration failure, and the function returns
--            'nurture' before evaluating the tree. This keeps "no signal" and
--            "missing rules" in the no-action lane instead of allowing
--            0 >= 0 to qualify a borrower for a positive offer.
--
-- Sample borrowers (under default thresholds and the chosen inputs pinned
-- in tests/fixtures/next_best_offer_golden.json):
--   fixture case A: spread=88, equity=46, permit=F, listed=F, inv=F,
--                        cust=F, comp=F
--                        -> branch 2 fires (88>=75 AND 46>=35)
--                        -> 'refi_plus_heloc' = "Refinance + HELOC"  ✓
--   fixture case B: spread=188, equity=39, permit=T, listed=F, inv=F,
--                        cust=F, comp=F
--                        -> branch 2 fires (188>=75 AND 39>=35)
--                        -> 'refi_plus_heloc' = "Refinance + HELOC"
--                        LABEL SHIFT: previously hardcoded "HELOC" in
--                        mock_data.py. This is an honest consequence of the
--                        decision tree: when BOTH strong rate spread AND
--                        HELOC-grade equity qualify, the refi+HELOC
--                        cross-sell is the correct product — a pure HELOC
--                        leaves refi revenue on the table. The backend
--                        engineer will update mock_data.py to match.
--   fixture case C: listed=T  -> branch 1 -> 'purchase' = "Purchase
--                        Mortgage"  ✓
--
-- Determinism: Pure boolean/arithmetic CASE. No nondeterministic calls.
--            Safe for gold materializations and metric views.
-- =============================================================================

CREATE OR REPLACE FUNCTION mip.gold.fn_next_best_offer(
  rate_spread_bps      INT,
  equity_pct           INT,
  has_permit           BOOLEAN,
  listed_for_sale      BOOLEAN,
  is_investor          BOOLEAN,
  is_current_customer  BOOLEAN,
  is_competitor_lien   BOOLEAN,
  min_spread_bps       INT,
  min_equity_pct       INT,
  heloc_equity_min_pct INT,
  cashout_equity_min   INT,
  retention_min_spread INT
)
RETURNS STRING
DETERMINISTIC
COMMENT 'Module 0 canonical Next-Best-Offer primitive. Returns one of eight lowercase product codes via a priority-ordered decision tree over rate spread, equity, permit, listing, investor, customer, and competitor-lien signals. Signal NULL numerics -> 0, signal NULL booleans -> FALSE; threshold NULLs return nurture. Thresholds passed explicitly; see tests/fixtures/next_best_offer_golden.json for parity fixtures and product_labels map.'
RETURN
  CASE
    WHEN min_spread_bps       IS NULL
      OR min_equity_pct       IS NULL
      OR heloc_equity_min_pct IS NULL
      OR cashout_equity_min   IS NULL
      OR retention_min_spread IS NULL
      THEN 'nurture'
    WHEN COALESCE(listed_for_sale, FALSE)
      THEN 'purchase'
    WHEN COALESCE(rate_spread_bps, 0) >= min_spread_bps
     AND COALESCE(equity_pct,      0) >= heloc_equity_min_pct
      THEN 'refi_plus_heloc'
    WHEN COALESCE(has_permit, FALSE)
     AND COALESCE(equity_pct, 0) >= heloc_equity_min_pct
      THEN 'heloc'
    WHEN COALESCE(rate_spread_bps, 0) >= min_spread_bps
     AND COALESCE(equity_pct,      0) >= min_equity_pct
      THEN 'refi'
    WHEN COALESCE(equity_pct, 0) >= cashout_equity_min
      THEN 'cash_out'
    WHEN COALESCE(is_investor, FALSE)
      THEN 'investor'
    WHEN COALESCE(is_current_customer, FALSE)
     AND ( COALESCE(rate_spread_bps, 0) >= retention_min_spread
        OR COALESCE(is_competitor_lien, FALSE) )
      THEN 'retention'
    ELSE 'nurture'
  END;
