> **Internal implementation artifact. Not approved for public release.**

# Validation: perf-borrower-dossier

Closes the `/api/borrowers/{id}` p95 gap called out in
[`tools/load_test/README.md`](../../tools/load_test/README.md): the 2000 ms
threshold. Slice 13 Wave 1 dropped p95 from 4600 ms to 3300 ms via a
ThreadPoolExecutor; the remaining cost was two warehouse statements
(`borrower_360` + `evidence_events`) per request.

## Design

`mip.gold.borrower_dossier` is a pre-joined superset of `mip.gold.borrower_360`
keyed on `borrower_id` with Delta liquid clustering on the same column, so
`WHERE borrower_id = :id LIMIT 1` is an indexed-row read.

The dossier carries:

- **Every column** from `borrower_360` — the SELECT list is a 1:1 copy.
- **`evidence_events`** — an `ARRAY<STRUCT<...>>` capped at 20 rows per CLIP,
  ordered by `signal_rank ASC, evidence_id ASC`. The 12-entry controlled
  vocabulary in `docs/data-contract-module0.md §3.4` has a current maximum of
  ~7–10 rows per CLIP; 20 is a comfortable ceiling that covers every
  conceivable combination and leaves headroom for when Cotality Permits and
  MLS Listings land (+4 signal types).
- **`trigger_timeline`** — the top-3 slice of the same array, materialised
  separately so the UI's trigger timeline rendering never slices a 20-row
  array for three elements.

## The refresh chain

`databricks.yml` job `mip_refresh_scores` gains `ctas_borrower_dossier`
dependent on both `ctas_borrower_360` and `ctas_evidence_events`. It runs
in parallel with `ctas_lockin_cohort`; total refresh time is unchanged
because the two new tasks are on the same fan-out level. Mirror updated
in `resources/jobs.yml`.

At ~5.16 M rows with an average row size around 1.5 KB (the struct array
dominates), expect ~7–8 GB on disk after autoOptimize compaction — roughly
1.5× `borrower_360`. Refresh-time expectation: a few minutes on serverless
Photon, same order as `lead_population` and `lockin_cohort`.

## How to verify

1. `databricks bundle deploy -t dev` — provisions the DDL §10 table.
2. `databricks bundle run mip_refresh_scores -t dev` — executes the new
   `ctas_borrower_dossier` task alongside the existing chain.
3. `DATABRICKS_HOST=... DATABRICKS_TOKEN=... DATABRICKS_WAREHOUSE_ID=... \
   .venv/bin/pytest tests/integration/test_borrower_dossier_parity.py -q`
   — picks 5 random borrower_ids, asserts every scalar column agrees with
   `borrower_360` and the top-3 evidence ordering agrees with the direct
   `evidence_events` query.
4. `MIP_API_URL=http://localhost:8000 bash tools/load_test/run.sh` — re-run
   the load harness against a live-UC backend; p95 on
   `GET /api/borrowers/{id}` should be < 2000 ms (target < 1000 ms warm).

## Rollback

The change is read-path only. Dropping `mip.gold.borrower_dossier` and
reverting `DatabricksBorrowerRepository._GET_SQL` to the old
`borrower_360`-plus-`evidence_events` pair restores the Slice-13 Wave-1
behaviour without data loss. Upstream `borrower_360` and `evidence_events`
tables are untouched.
