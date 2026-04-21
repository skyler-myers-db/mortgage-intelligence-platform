---
name: Slice 10 catalog rename — mip_demo → mip
description: Catalog moved from mip_demo to mip across SQL/bundle/backend/frontend/docs; app resource mip-demo-app → mip-app; job mip_refresh_demo_data → mip_refresh_silver; talk-track/rehearsal/seed files renamed via git mv.
type: project
---

Slice 10 of the Module 0 real-data migration completed the customer-facing
product-naming sweep so the app reads as "Mortgage Intelligence Platform"
rather than a DAIS demo.

**Why:** The product is a real platform; DAIS is one venue. Customer-facing
surfaces (UI copy, SQL catalog, bundle resource names, API responses,
product docs) cannot say "demo" going forward.

**How to apply:**
- Catalog is now `mip` (was `mip_demo`). Every UC path is `mip.<schema>.*`.
- Default bundle app resource key: `mip_app` (was `mip_demo_app`).
- App display name: `mip-app` (was `mip-demo-app`).
- Job renamed: `mip_refresh_silver` (was `mip_refresh_demo_data`).
- File renames via git mv: `docs/module0-talk-track.md`,
  `docs/module0-rehearsal-checklist.md`, `lakebase/seed_campaigns.sql`.
- Also swept in the master-agent follow-up immediately after the subagent
  returned: `settings.mip_demo_lender` → `settings.mip_lender_name`;
  `/api/admin` + `/api/config/options` response key `demo_lender` →
  `lender_name`; secret scope `mip-demo` → `mip` (silver_property_master
  SQL + mip_feature_pipeline); User-Agent `mip-demo-ingest/0.1` →
  `mip-ingest/0.1`; CTE column `historical_mortgage_count_at_demo_lender`
  → `historical_mortgage_count_at_lender`; admin `offer_rules_version:
  demo-v1` → `v1`.
- Internal dev artifacts untouched: `.claude/agents/`, `.claude/skills/`,
  commit history, `frontend/src/mocks/demoData.ts` filename.
- Scoring primitives contract unchanged: only the namespace moved; UDF
  signatures, weights, NULL handling, banker's rounding identical.
- Post-deploy: `databricks bundle deploy -t dev` must provision the `mip`
  catalog (not `mip_demo`); parity tests run `SELECT mip.gold.fn_*` now.
