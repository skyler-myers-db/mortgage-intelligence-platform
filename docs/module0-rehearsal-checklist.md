# Module 0 — Pre-Session Rehearsal Checklist

**Who runs this:** the operator (or backup presenter).
**When:** 10 minutes before every session.
**Why:** the app is live Unity Catalog — serverless warehouses cold-start, Genie
spaces cold-start, Lakebase needs a fresh auth token. Catching these one at a
time in quiet beats catching all of them on stage.

If any step below fails, stop and fix before starting the pitch. The talk
track is truthful about resilience, but the audience should see *resilience*,
not *recovery*.

## 1. Warm the serverless SQL warehouse

```bash
databricks warehouses start $DATABRICKS_WAREHOUSE_ID
```

Expect `RUNNING` within ~30 seconds. If it's already running, the command is
a no-op. First query after warmup is fastest if you run a trivial `SELECT 1`
against `mip.gold.borrower_360` immediately after.

## 2. Probe app health — expect full green

```bash
curl -s https://<databricks-app-host>/api/health | jq
```

Expect:
- `"status": "ok"`
- `"mode": "live"`
- `"dependencies": { "warehouse": "up", "lakebase": "up", "genie": "up" }`
- `"circuit_breakers": { "warehouse": "closed", "lakebase": "closed", "genie": "closed" }`

Any `down` dependency or non-`closed` breaker means do not go on stage yet.
The probe itself runs in under 250 ms when everything is warm.

## 3. Cold-start probe the Genie space

```bash
curl -s -X POST https://<databricks-app-host>/api/genie/ask \
  -H 'content-type: application/json' \
  -d '{"question":"How many borrowers across the 6-state footprint are currently in-the-money, and what is the average rate spread?"}'
```

First call on a cold Genie space can take 10–30 s. A second call against the
same question after the first returns will be snappy — that's the warm path
the audience sees. Verify the response `source` is `"genie"`, not `"fallback"`.

## 4. Verify Lakebase write path via a benign audit row

Load the app in an incognito tab, click into a borrower dossier (e.g.
`/borrower-360/B-48291`) and confirm a row appears in `/api/audit` within a
second or two. This proves the Lakebase write path is open; an expired auth
token will cause a quiet 500 if you find out during the approval beat
instead.

```bash
curl -s https://<databricks-app-host>/api/audit/events?limit=5 | jq '.[0]'
```

## 5. Frontend sanity check — no DegradedBanner

Open the app in an incognito / private tab at **1440×900, dark theme,
compact density**. Expect:

- KPI row animates in; hero renders without a `DegradedBanner` strip.
- Right-rail Console footer shows *Warehouse up · Genie up* with a probe
  latency under 500 ms.
- Floating Genie FAB is visible bottom-right.
- Map choropleth shades all six real states (IL, CA, FL, TX, WA, CO).

If the DegradedBanner is showing, something upstream failed the `/api/health`
probe — step back to (2) and do not start the pitch.

## 6. Rehearse the Genie safe-corpus fallback (once)

Even on a cold Genie, these three canonical questions from
`genie/sample_questions.md` resolve deterministically through the safe-corpus
in `backend/services/genie_answers.py`:

- "How many borrowers across the 6-state footprint are currently in-the-money?"
- "Show the top 10 cash-out candidates in Florida by estimated equity."
- "How big is the 2020–2022 sub-3% lock-in cohort across the 6-state footprint?"

Ask one verbatim, confirm you get a structured answer (metric, table, follow-ups,
trusted-asset chips). If the answer returns with `source: "fallback"`, that's
fine — it means the breaker is open and the safe corpus caught it. Re-run step (3)
in a minute and the Genie space will have warmed up.

## 7. Second-monitor backup — have the API queries ready

If the frontend dies on stage, the API still works. Pre-load these in a second
terminal or browser tab:

- `curl /api/leads?limit=5 | jq`
- `curl /api/borrowers/B-48291 | jq`
- `curl /api/segments | jq`

The dossier endpoint contains everything the Borrower 360 page renders — you
can narrate from JSON if the UI goes dark.

## 8. Clear the click path

Walk the 13-step click path from the talk track Appendix once, silently, before
the audience arrives. Muscle memory is the difference between a session that
takes 7 minutes and one that takes 10.

---

## If something lights up red mid-session

| Signal | What to do | What to say |
|---|---|---|
| DegradedBanner appears at top of page | Keep going; the retry + breaker logic re-arms within 30 s | *"The banner tells you the warehouse is warming up — real-time honesty, not a stage trick."* |
| Genie answer returns `source: "fallback"` | Keep going; safe corpus is the guarantee | *"The circuit breaker opened and our safe corpus took over — you just watched resilience engineering instead of a spinner."* |
| Approval click shows an error toast | Don't re-click | *"Lakebase write path is flagged; we'd rather fail visibly than fake success. Audit guarantee working as designed."* |
| Page outright blanks | Swap to the second monitor and narrate from API JSON | *"The UI is the skin, not the substance — here's the same answer from the API."* |

---

*Owner: storyteller subagent. Review cadence: every Slice PR into
`feature/module0-real-data`; always re-run before a customer meeting.*
