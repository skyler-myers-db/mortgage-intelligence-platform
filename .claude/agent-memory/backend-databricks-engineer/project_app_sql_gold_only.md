---
name: app-sql-gold-only
description: App-executed SQL must never name mip.silver/mip.raw/the provider catalog; precompute into a gold support table; guard test + the LEFT-vs-INNER join trap
metadata:
  type: project
---

The App SP holds no grant on `mip.silver`, `mip.raw` or `cotality_mortgage_data` (GRANTS.md sections 5/7). A statement naming one passes every unit test and fails only live: the Rate Lever 503 of 2026-09-25 (`[INSUFFICIENT_PERMISSIONS] ... USE SCHEMA on Schema 'mip.silver'`) came from a live CTE that LEFT JOINed `silver.lien_current`. Fix (branch fix/rate-lever-gold-only): `mip.gold.rate_sensitivity_book` built by the refresh job as the ETL identity; `tests/unit/test_app_sql_reads_gold_only.py` now fails any backend statement constant or statement f-string that names those schemas.

**Why:** widening grants is not an option; the ETL identity is the only one allowed to read silver.

**How to apply:** when a live statement needs a silver-derived value, add a gold CTAS task in `mip_refresh_scores` and read that. When replacing a LEFT JOIN on silver with a join on a "non-NULL rows only" gold table, keep it a LEFT JOIN: a NULL-gated borrower scores 0 bps and still clears a `min_spread_bps <= 0` screen (admin rules accept 0), so an INNER JOIN silently undercounts only the live subset. Provenance labels naming silver are fine (no FROM/JOIN). Related: [[permission-denied-fail-fast]], [[project_addressable_vs_contactable_gap]].
