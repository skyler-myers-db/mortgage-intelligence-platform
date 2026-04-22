# Validation: `/api/health` stale-while-revalidate cache

Slice-13 follow-up. Closes the remaining p95 gap after the plain-TTL
v1 left a 1100 ms tail at 20 VUs.

## Design choice

Plain TTL (v1, 3 s) → stale-while-revalidate (v2, 2 s soft / 10 s
hard). Inside the soft window every caller gets a sub-ms cache hit.
Between 2 s and 10 s callers still get the cached value and exactly
**one** background refresh is scheduled on a shared
`ThreadPoolExecutor(max_workers=3)`. Only past the 10 s hard TTL does
a request thread run the probe synchronously. Stdlib-only, no async,
no new wheels — fits the Databricks Apps runtime constraint.

## Trade-offs

- Hard TTL at 10 s: real outage stays hidden in the body ≤ 10 s;
  frontend polls every 5–10 s, so the banner flips within one cycle.
- `refresh_in_flight` flag guarded by a single `threading.Lock` —
  compare-and-set under lock means two stale callers produce exactly
  one refresh. Validated with 8 real threads in
  `test_swr_cache_concurrent_stale_callers_only_kick_one_refresh`.
- A background probe that raises keeps the last-good value and clears
  the flag so the next caller retries.

## Expected p95 at 20 VUs × 90 s

Locust baseline: ~10 rps. One sync probe every 10 s (hard TTL) ≈ 1 %
of requests blocking on ~1 s; 99 % hit sub-ms cache → projected p95
well under the 500 ms threshold. Local smoke: cold first hit 3995 ms,
three follow-ups 0.56 / 0.36 / 0.36 ms.

## How to verify

```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000
MIP_API_URL=http://localhost:8000 bash tools/load_test/run.sh
```

`tools/load_test/results/<timestamp>_stats.csv` — `/api/health` `95%`
column must be < 500 ms.
