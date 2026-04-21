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

- Mock mode works.
- Prod mode has warehouse ID.
- Lakebase schema exists.
- Genie space is curated.
- Bundle validates.
- Frontend build passes.
- Python tests pass.
- Talk track rehearsed.
