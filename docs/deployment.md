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

1. Fill bundle variables.
2. Build frontend.
3. Validate bundle.
4. Deploy app.

```bash
npm --prefix frontend run build
databricks bundle validate -t dev
databricks bundle deploy -t dev
```

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
- `databricks bundle validate -t dev` passes.
- Frontend build passes (`npm --prefix frontend run build`).
- Python tests pass (`pytest -q`).
- Talk track rehearsed.
