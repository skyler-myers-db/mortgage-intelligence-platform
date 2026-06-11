---
name: gold-refresh-forbids-current-timestamp
description: Gold-refresh CTAS files must source refreshed_at/snapshot_at from mip.ref.refresh_run_state, never CURRENT_TIMESTAMP() in the body (test_gold_ddl_contract pins this)
metadata:
  type: project
---

`tests/unit/test_gold_ddl_contract.py::test_ctas_reads_shared_refresh_at` strips comments from listed gold CTAS files and asserts the body contains NO `CURRENT_TIMESTAMP(` — the timestamp must come from `(SELECT refresh_at FROM mip.ref.refresh_run_state ORDER BY captured_at DESC LIMIT 1)`.

**Why:** audit-holes-round-3 #7 — per-task `CURRENT_TIMESTAMP()` made every gold table's "Refreshed ..." chip disagree by seconds within one run. The single seed task `capture_refresh_timestamp.sql` is the ONE place `CURRENT_TIMESTAMP()` is allowed.

**How to apply:** when adding header comments or column comments to these CTAS files, never write the literal `CURRENT_TIMESTAMP(` even in a comment — the test strips `--` line comments but a block/inline form could slip through; safest to avoid the token entirely. `gold_state_top_segment.sql` uses `CURRENT_DATE()` for a DATE column (allowed — only the TIMESTAMP function is forbidden) and is NOT in the pinned file list. Files pinned: property_owner_bridge, borrower_360, lead_scores, segment_population, lockin_cohort, borrower_dossier, county_rollup, zip_rollup, source_readiness. See [[gold-ctas-redeclares-ddl-metadata]].
