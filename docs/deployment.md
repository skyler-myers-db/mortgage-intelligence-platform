# Deployment guide

## Local

```bash
cp .env.example .env.local
pip install -r requirements.txt
npm --prefix frontend install
npm --prefix frontend run dev
uvicorn backend.main:app --reload
```

## Build frontend

```bash
npm --prefix frontend run build
```

FastAPI serves `frontend/dist` automatically if present.

## Databricks App

1. Fill `.env.local` with workspace and warehouse values.
2. Run the deployment script. It builds the frontend, provisions Genie if
   needed, validates/plans/deploys the direct bundle through the env-aware wrapper,
   promotes the uploaded source to the running Databricks App, and runs
   the refresh/smoke steps.

```bash
./scripts/deploy.sh
```

Do not run bare `databricks bundle deploy -t dev` or project-mode
`databricks apps deploy` as the shipping path. Those commands bypass
`.env.local` to `BUNDLE_VAR_*` mapping and can try to update
`databricks_app.mip_app` with placeholder bindings such as
`00000000PLACEHOLDER`, which Databricks reports as an opaque
permission error.

For a narrow resource-only recovery, `make bundle-validate`,
`make bundle-plan`, and `make bundle-deploy` are safe because they use
`tools/databricks/bundle_env.py`.
After a resource-only deploy, promote the uploaded source with
`databricks apps deploy mip-app --mode SNAPSHOT`.

## Resources

Databricks App resources expected by `app.yaml`:

- `sql_warehouse`: SQL warehouse resource, `CAN_USE`.
- `genie_space`: Genie space resource, `CAN_RUN`.
- `database`: Lakebase database resource, `CAN_CONNECT_AND_CREATE`.

## Release checklist

The app runs on live Unity Catalog + Lakebase in every environment — there is no mock-mode runtime toggle (see [CLAUDE.md](../CLAUDE.md) "Negative prompting"). Flakiness is handled by the resilience layer (retry, warehouse warm-start, SWR cache, circuit breaker, degraded-state banner), never by silent mock fallback.

- Warehouse ID + Genie space ID are set (`BUNDLE_VAR_sql_warehouse_id`, `BUNDLE_VAR_genie_space_id`).
- Lakebase schema + `mip_app.action_audit` table exist.
- Genie space is curated against `mip.semantics.*` metric views only.
- `/api/health` reports `warehouse: up`, `genie: up`, `lakebase: up`, all circuits `closed`.
- Resilience is observable: degraded banner renders when a dependency drops; Approve writes a real row to `mip_app.action_audit`.
- `tools/databricks/bundle_env.py validate -t dev` passes.
- `tools/databricks/bundle_env.py plan -t dev` shows the expected direct
  deployment changes.
- Frontend build passes (`npm --prefix frontend run build`).
- Python tests pass (`pytest -q`).
- Talk track rehearsed.
