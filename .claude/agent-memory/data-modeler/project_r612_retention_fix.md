---
name: R6-12 retention segment drift root cause
description: Retention materialized 0 in gold because lender_dictionary equi-JOIN missed Summit tenant-name variants; fixed with LIKE fallback 2026-04-23
type: project
---

Retention segment (`mip.gold.borrower_360.segment_codes`) collapsed to 0 rows across all 6 states after slice13-accuracy swapped `UPPER(first_pos_lender_current) LIKE '%SUMMIT%'` for a governed `JOIN mip.ref.lender_dictionary`.

**Why:** the dictionary seeds `raw_key = 'SUMMIT MTG'` (exact, uppercase). Cotality's raw share carries multiple tenant variants (`SUMMIT MORTGAGE`, `SUMMIT MTG CORP`, ...) so the equi-JOIN missed every non-seeded variant. The parity test's reference SQL (`_retention_reference_sql` in `tests/integration/test_segment_count_parity.py`) still uses `LIKE '%SUMMIT%'`, which is the data-contract §3.2 intent -- gold silently diverged from the test oracle.

**How to apply:** when adding governed lookups that replace permissive LIKE/regex matches, always keep the permissive matcher as a fallback OR audit the raw data for all variants first. Seed coverage against live share is the ongoing gap -- `mip.ref.lender_dictionary` needs a periodic `MERGE` workflow that scans raw lender strings and adds unseen variants.

Fix landed in `sql/transformations/gold_borrower_360.sql` (2026-04-23, fix/copilot-batch-post-merge): `is_current_customer` is now `ref-says-customer OR LIKE '%SUMMIT%'` and `is_competitor_lien` mirrors it. The LIKE uses hardcoded `'SUMMIT'` token matching the CLAUDE.md sample-lender naming rule; when admin-config lands (Slice 5), bind it to `settings.tenant_lender_token`.
