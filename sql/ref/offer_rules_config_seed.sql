-- =============================================================================
-- offer_rules_config_seed.sql
-- -----------------------------------------------------------------------------
-- Purpose:   Seed `mip.ref.offer_rules_config` with the canonical threshold
--            vocabulary that drives the Offer Orchestrator decision tree
--            (`fn_next_best_offer`), the In-the-Money flag
--            (`fn_in_the_money`), and the Admin surface (/api/admin/rules).
--
-- Provenance:
--            Every row mirrors a default documented inline in the UC function
--            headers under `sql/uc_functions/`. Changing a value here does
--            NOT retune UC compute -- the thresholds are passed as explicit
--            args by the application layer at query time. The row is the
--            single canonical source the admin UI reads + the product of
--            record for "what is the active ruleset" on a given day.
--
--            Pin points (keep in lock-step with UC headers):
--              mip_min_spread_bps            = 75     (fn_in_the_money.sql L28, fn_next_best_offer.sql L92)
--              mip_min_equity_pct            = 15     (fn_in_the_money.sql L29, fn_next_best_offer.sql L93)
--              mip_heloc_equity_min_pct      = 35     (fn_next_best_offer.sql L94)
--              mip_cashout_equity_min_pct    = 25     (fn_next_best_offer.sql L95)
--              mip_retention_min_spread_bps  = 50     (fn_next_best_offer.sql L96)
--              mip_market_rate               = 0.04875 (fn_rate_spread.sql L36)
--
-- Idempotency: MERGE on key. Re-running this file (as part of the
--            `mip_ref_seed` + `mip_refresh_silver` bundle jobs) is a no-op
--            for unchanged rows and refreshes `last_updated` for any row
--            whose value / unit / label / description / sort_order changed.
-- =============================================================================

MERGE INTO mip.ref.offer_rules_config AS t
USING (
  SELECT
    col1 AS key,
    col2 AS value,
    col3 AS unit,
    col4 AS label,
    col5 AS description,
    col6 AS sort_order
  FROM VALUES
    ('mip_min_spread_bps',           75.0,    'bps',           'Min spread (bps)',
     'Minimum rate spread vs. market before a borrower is considered in the money.',            1),
    ('mip_min_equity_pct',           15.0,    'pct',           'Min equity (%)',
     'Minimum equity percentage required to qualify as in the money.',                         2),
    ('mip_heloc_equity_min_pct',     35.0,    'pct',           'HELOC equity floor (%)',
     'Equity floor required for HELOC eligibility and refi+HELOC cross-sell.',                 3),
    ('mip_cashout_equity_min_pct',   25.0,    'pct',           'Cash-out equity floor (%)',
     'Equity floor required for cash-out refi eligibility when rate economics are absent.',    4),
    ('mip_retention_min_spread_bps', 50.0,    'bps',           'Retention min spread (bps)',
     'Lowered spread bar used for retention outreach on existing customers.',                  5),
    ('mip_market_rate',              0.04875, 'rate_fraction', 'Market rate reference',
     'Par / market rate baseline used by fn_rate_spread for spread calculations.',             6)
) AS s
ON t.key = s.key
WHEN MATCHED THEN UPDATE SET
  value        = s.value,
  unit         = s.unit,
  label        = s.label,
  description  = s.description,
  sort_order   = s.sort_order,
  last_updated = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (
  key, value, unit, label, description, sort_order, last_updated
) VALUES (
  s.key, s.value, s.unit, s.label, s.description, s.sort_order, CURRENT_TIMESTAMP()
);
