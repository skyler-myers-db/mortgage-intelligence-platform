# Module 0 — Pre-Walkthrough Dry-Run Checklist

> *(Filename `module0-rehearsal-checklist.md` is retained so existing links
> resolve; content has been renamed to "pre-walkthrough dry-run".)*

**Who runs this:** the operator (or backup walkthrough lead).
**When:** 10 minutes before every session.
**Why:** the app is live Unity Catalog — serverless warehouses cold-start, Genie
spaces cold-start, Lakebase needs a fresh auth token. Catching these one at a
time in quiet beats catching all of them during a live walkthrough.

If any step below fails, stop and fix before starting the walkthrough. The
talk track is truthful about resilience, but the reviewer should see
*resilience*, not *recovery*.

## 1. Warm the serverless SQL warehouse

```bash
databricks warehouses start $DATABRICKS_WAREHOUSE_ID
```

Expect `RUNNING` within ~30 seconds. If it's already running, the command is
a no-op. First query after warmup is fastest if you run a trivial `SELECT 1`
against `mip.gold.borrower_360` immediately after.

## 2. Probe app health — expect full green

```bash
curl -s "$MIP_APP_URL/api/health" | jq
```

(The deployed App authenticates via workspace-identity Bearer — no PAT
needed in the runtime. If you're probing from a workstation, the `curl`
above just hits the public Databricks App URL.)

Expect:
- `"status": "ok"`
- `"mode": "live"`
- `"dependencies": { "warehouse": "up", "lakebase": "up", "genie": "up" }`
- `"circuit_breakers": { "warehouse": "closed", "lakebase": "closed", "genie": "closed" }`

Any `down` dependency or non-`closed` breaker means do not start the walkthrough yet.
The probe is fronted by a stale-while-revalidate cache (2 s soft TTL,
10 s hard TTL, background refresh on a shared ThreadPoolExecutor). A
cold first request may take ~1 s while it spins up the dependency probes;
every subsequent call in the next 2 s returns instantly from the cache
while a background refresh runs off the request thread. Warm p95 is
**130 ms** against the deployed app (see [docs/load-baseline.md](load-baseline.md) —
the full Module 0 perf arc dropped `/api/health` p95 from 1,100 ms to
130 ms, -93 % from baseline). If the probe is returning >500 ms on a
second call, something's off — investigate before going on stage.

## 3. Cold-start probe the Genie space

```bash
curl -s -X POST https://<databricks-app-host>/api/genie/message \
  -H 'content-type: application/json' \
  -d '{"question":"How many borrowers across the current Cotality data coverage are currently in-the-money, and what is the average rate spread?"}'
```

First call on a cold Genie space can take 10–30 s. A second call against the
same question after the first returns will be snappy — that's the warm path
the audience sees. Verify the response `source` is `"genie"`, not `"degraded"`.

## 4. Verify Lakebase write path via a benign audit row

Load the app in an incognito tab, open Lead Queue, choose the first available
borrower row, open its Borrower 360 dossier, and confirm a row appears in
`/api/audit` within a second or two. This proves the Lakebase write path is
open; an expired auth token will cause a quiet 500 if you find out during the
approval beat instead.

```bash
curl -s https://<databricks-app-host>/api/audit/events?limit=5 | jq '.[0]'
```

## 5. Frontend sanity check — no DegradedBanner

Open the app in an incognito / private tab at **1440×900, dark theme,
compact density**. Expect:

- KPI row animates in; hero renders without a `DegradedBanner` strip.
- Right-rail Console footer shows *Warehouse up · Genie up* with a probe
  latency near **130 ms** (warm path through the SWR cache — see
  [docs/load-baseline.md](load-baseline.md) for the full endpoint p95 table).
- Floating Genie FAB is visible bottom-right.
- Map choropleth shades the refreshed coverage states, and drill-down copy
  discloses the county count discovered from gold rollups.
- Opening a borrower dossier from the first Lead Queue row returns in
  ~1.2 s — that's the `mip.gold.borrower_dossier` CTAS (pre-joined
  borrower_360 × top-20 evidence events) replacing the old two-query
  fan-out. If you're seeing 3-second-plus dossier loads, the CTAS may
  not have refreshed; re-run `databricks bundle run mip_refresh_scores -t dev`.

If the DegradedBanner is showing, something upstream failed the `/api/health`
probe — step back to (2) and do not start the walkthrough.

## 6. Dry-run the Genie degraded path (once)

On a cold or unavailable Genie space, the app must return an honest
`source: "degraded"` reconnecting response with no fabricated numbers.
Prime one of these canonical questions from `genie/sample_questions.md`:

- "How many borrowers across the current Cotality data coverage are currently in-the-money?"
- "Show the top 10 cash-out candidates in Florida by estimated equity."
- "How big is the 2020–2022 sub-3% lock-in cohort across the current Cotality data coverage?"
  — compare the answer against the current `mip.gold.lockin_cohort` count
  during rehearsal; do not use a stale fixed count in the walkthrough.

Ask one verbatim. For the live demo, wait until you get a structured
`source: "genie"` answer with proof metadata and trusted-asset chips. If the
answer returns with `source: "degraded"`, do not present it as an answer; it
means the breaker is open and the app correctly refused to fabricate data.
Re-run step (3) in a minute and the Genie space will have warmed up.

## 7. Second-monitor backup — have the API queries ready

If the frontend dies mid-walkthrough, the API still works. Pre-load these in a second
terminal or browser tab:

- `curl "$MIP_APP_URL/api/leads?limit=5" | jq`
- `BORROWER_ID="$(curl -s "$MIP_APP_URL/api/leads?limit=1" | jq -r '.[0].borrower_id')"`
- `curl "$MIP_APP_URL/api/borrowers/$BORROWER_ID" | jq`
- `curl "$MIP_APP_URL/api/segments" | jq`

The dossier endpoint contains everything the Borrower 360 page renders — you
can narrate from JSON if the UI goes dark.

## 8. Clear the click path

Walk the 13-step click path from the talk track Appendix once, silently, before
the reviewer arrives. Muscle memory is the difference between a session that
takes 7 minutes and one that takes 10.

---

## If something lights up red mid-session

| Signal | What to do | What to say |
|---|---|---|
| DegradedBanner appears at top of page | Keep going; the retry + breaker logic re-arms within 30 s | *"The banner tells you the warehouse is warming up — real-time honesty, not a cover-up."* |
| Genie answer returns `source: "degraded"` | Pause Genie answers until it warms | *"The app is being honest: Genie is reconnecting, and we do not show fabricated analytics while it is down."* |
| Approval click shows an error toast | Don't re-click | *"Lakebase write path is flagged; we'd rather fail visibly than fake success. Audit guarantee working as designed."* |
| Page outright blanks | Swap to the second monitor and narrate from API JSON | *"The UI is the skin, not the substance — here's the same answer from the API."* |

---

*Owner: storyteller subagent. Review cadence: every Slice PR into
`feature/module0-real-data`; always re-run before a customer meeting.*
