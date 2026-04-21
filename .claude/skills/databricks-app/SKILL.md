---
name: databricks-app
description: Use when implementing Databricks Apps, app.yaml, FastAPI runtime, app resources, Databricks SQL, Lakebase, Genie, or bundles.
---


# Databricks App implementation Skill

Use this skill when touching `app.yaml`, `databricks.yml`, backend runtime, app resource bindings, Databricks SQL, Lakebase, Genie, or deployment.

Checklist:
1. Keep secrets out of source and app config.
2. Use `valueFrom` for Databricks App resources.
3. Use `python -m backend.runtime` so ports come from Databricks runtime variables.
4. Mock mode must work without Databricks network access.
5. All Databricks calls must have typed service wrappers and graceful fallbacks.
6. Run `python tools/verify_scaffold.py` and `databricks bundle validate -t dev` after workspace variables are filled.

Preferred patterns:
- `backend/services/databricks_sql.py` owns SQL Warehouse access.
- `backend/services/lakebase.py` owns app-state writes.
- `backend/services/genie_client.py` owns Genie calls.
- API routers never construct raw SQL from user input.

