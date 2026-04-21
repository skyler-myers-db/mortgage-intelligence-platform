---
name: silver.market_rates_weekly column contract
description: The canonical column spec for mip.silver.market_rates_weekly is in docs/data-contract-module0.md §2.5 — use observation_week (Monday), rate_pct DOUBLE, rate_fraction DOUBLE, vintage_ts, is_latest, NOT the DECIMAL/observation_date variant.
type: project
---

`mip.silver.market_rates_weekly` column spec (data-contract §2.5 is authoritative):
- `series_id STRING NOT NULL` (e.g. `MORTGAGE30US`)
- `observation_week DATE NOT NULL` — week-starting Monday; FRED publishes Thursday, ingest applies `date_trunc('week', ...)`
- `rate_pct DOUBLE NOT NULL` — rate in percent (6.40 == 6.40%)
- `rate_fraction DOUBLE NOT NULL` — rate_pct / 100; consumed by `fn_rate_spread`
- `vintage_ts TIMESTAMP NOT NULL`
- `is_latest BOOLEAN NOT NULL` — exactly one row per series_id; metric views MUST join on this
- `source STRING NOT NULL` — extension added in Slice 1: `'fred'` or `'seed'`
- `_meta_batch_id STRING NULLABLE` — extension added in Slice 1

PK: `(series_id, observation_week)`. Delta + liquid clustering on `(series_id, observation_week)`.

**Why:** Two competing specs existed: (a) task instructions with DECIMAL + `observation_date` + `loaded_at`, and (b) data-contract §2.5 with DOUBLE + `observation_week` + `vintage_ts` + `is_latest`. Task explicitly said "match the data-contract if pinned there," so (b) wins. `source` + `_meta_batch_id` were added from (a) as extensions since they don't conflict.

**How to apply:** Any future slice that touches market rates — especially `gold_borrower_360.sql` joining `is_latest = TRUE` in Slice 3 — must use the data-contract §2.5 column names. `fn_rate_spread` takes fractional rates, so the join column on the gold side is `rate_fraction`.
