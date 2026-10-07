# Living Genie tiles: spike (audit 2026-09-21 `wow-ai-5`)

**Status: prototype-first measurement only. Living tiles are NOT built.** Pinned
insights on Home stay static pins (`frontend/src/lib/pinnedInsights.ts`,
actor-scoped local storage, at most six, a short summary) and nothing
re-executes a Genie query after the answer was shown. This page records how the
spike is measured, what the offline run found, and the pass criteria a later
build must meet. The build itself (an `execute_attachment_query` client, a
`mip_app.genie_tiles` table, routes, a server-backed pin store and a refresh
job) is deferred to W5d `w5-genie-provenance-tiles`, and only if every
criterion below passes; otherwise it is recorded as not built with the
measured reason. The `--live` probe for criteria 2-4 is built (W5c).

## Method

`python -m tools.genie_tiles_spike --offline` is deterministic and talks to no
service. It builds six tile-sized, aggregate-shaped synthetic row sets at 10,
50 and 200 rows (at most 12 columns, with a masked `B-` id column, a PII-keyed
contact column and, on one tile, a phone-shaped value) and runs the row path a
refresh would have to re-run, fail-closed, on every refresh:

1. the SQL trust policy (`_trusted_sql_policy` in
   `backend/services/repositories/databricks_genie_trust.py`);
2. row redaction (`_redact_genie_rows` in `databricks_genie_policy_helpers.py`);
3. the cell-surface output guard (`genie_visible_text_unsafe`, structured, on
   every visible key and cell) and `governed_row_literals` in
   `backend/services/genie_message_policy.py`.

It imports and calls those guards read-only; it never edits or patches a guard
module, so no tree-swap differential is needed. Work is counted with
`sys.setprofile` as Python calls into `backend/` (plus the C calls made from
backend frames) and as characters handed to the scanners. One representative
answer's prose scan (a nine-sentence narrative plus three follow-up questions,
about 970 characters) on the same counters is the narrative baseline. Wall time
is printed with the load average and is informational only.

The baseline covers exactly one function: `genie_visible_text_unsafe` in its
default prose mode (`structured_value=False`, no governed cell values), called
once on the narrative and once on each follow-up question. It does not include
the rest of a real answer's path: that answer's own SQL trust check, row
redaction and cell scan, `governed_row_literals`, the claims verifier, or the
prompt-side guards. The ratio below therefore compares the tile row path with
one prose scan, not with the cost of a whole answer, and must not be read as
"a tile refresh costs 104 answers".

## Offline numbers (2026-10-01, load average about 33-44)

| Rows per tile (6 tiles) | Guard calls | Calls per row | C calls | Characters scanned |
|---:|---:|---:|---:|---:|
| 10 | 286,579 | 4,776 | 617,802 | 15,304 |
| 50 | 1,420,388 | 4,735 | 3,022,590 | 73,524 |
| 200 | 5,685,497 | 4,738 | 12,073,477 | 292,444 |
| Narrative baseline (one answer) | 54,496 | n/a | 63,086 | 968 |

The row path is linear in rows (calls per row stay within 1% from 10 to 200
rows), but six maximum-size tiles cost about 104 times one answer's prose scan
on the same counter. Every synthetic tile also tripped the cell guard on its
PII-shaped cells, so a refresh of such a tile would withhold its rows, which is
the fail-closed behaviour the build must keep.

## Pass criteria (binding for the build)

1. Row-path guard work is linear in rows, and for six maximum-size tiles is no
   more than one answer's prose scan on the same counter (the
   `genie_visible_text_unsafe` baseline above). **Offline: FAIL** (linear, but
   104x that one-function baseline). A build must cap rows per tile or
   prove a cheaper fail-closed row scan before it may proceed.
2. COMPLETED attachments stay executable for at least 7 days. Otherwise tiles
   can only be dated snapshots, which is a FAIL. *Live probe, not yet run.*
3. The App identity can execute its own attachments, and a tile is never
   refreshed under another actor's identity. *Live probe, not yet run.*
4. Refresh happens only on an explicit Refresh or a post-gold-refresh job, at
   most once per gold refresh stamp, never from render, hover or poll, and a
   warm-warehouse refresh fits the Genie budget. *Live probe, not yet run.*

## The live probe (`--live`, W5c)

```bash
python -m tools.genie_tiles_spike --live --profile P --space-id S \
  [--other-profile P2] [--max-executions 2] [--allow-wake] [--app-identity] \
  [--json out.json]
```

`tools/genie_tiles_live.py` uses the Databricks SDK's `WorkspaceClient`
(profile `P`) for READ calls: `genie.get_space(S).warehouse_id`,
`genie.list_conversations` and `genie.list_conversation_messages` (at most
200 conversations). It buckets COMPLETED messages that carry a query
attachment by age (<= 1 day, 1-7 days, 7-30 days, > 30 days). Per bucket it
then makes at most `--max-executions` calls of
`execute_message_attachment_query`, each only after `warehouses.get(id)`
(state and size) and `genie.get_message` show the warehouse RUNNING (or
`--allow-wake`; otherwise `skipped_cold`) and the message COMPLETED or
EXECUTING_QUERY (otherwise `skipped_state`). With `--other-profile` it tries
ONE execute of the first identity's attachment under the other identity.
`--app-identity` asserts that `P` is the App's service principal.

It records ids, states, the HTTP status or exception class, durations, row
counts and the warehouse state and size only: never question text,
conversation titles, SQL text, row values, emails or headers (an exception's
message is never recorded). Each execute ends `ok`, `refused` (403/404: the
identity may not run it), `error` (any other answer: a measured failure) or
`unavailable` (401, 429, 5xx, a timeout or a transport error: nothing was
measured). A `warehouses.get` or `genie.get_message` read that fails mid-run
is `unavailable` with its `failed_read`, and an unexpected exception after
start-up returns INCONCLUSIVE (`phase: measure`). Only `ok`, `refused` and
`error` executes count towards a verdict, so an auth or network failure
mid-run is never a FAIL. Verdicts:

- criterion 2 PASS when a 7-30 day attachment executed with 200, FAIL when
  every measured 7-30 day execute failed, otherwise INCONCLUSIVE;
- criterion 3 needs the same identity's 200, the other identity refused (a
  401 under the other identity is `unavailable`, not a refusal), and
  the App service principal's half: INCONCLUSIVE without `--other-profile`,
  and INCONCLUSIVE for the App half unless run with `--app-identity` under
  the App's own identity (the reason is recorded in the JSON);
- criterion 4 PASS when a warm (RUNNING) execute returned within 15 s (the
  docs/load-baseline.md Genie p95); each refresh is one Genie API call, so
  one Genie-budget call.

Exit codes: 0 PASS, 1 FAIL, 2 INCONCLUSIVE (auth or network, or nothing
measurable).

| Criterion | Live verdict |
| --- | --- |
| 2. COMPLETED attachments executable for at least 7 days | pending integrator run (after the W5c deploy) |
| 3. Own-identity execute, other identity refused, App identity | pending integrator run (after the W5c deploy) |
| 4. Warm refresh within the Genie p95, one Genie-budget call | pending integrator run (after the W5c deploy) |

## Integrator checklist (after the W5c deploy)

- [ ] Re-run `python -m tools.genie_tiles_spike --offline` on the deployed
      commit and confirm the counters match this page within noise.
- [ ] Run `python -m tools.genie_tiles_spike --live --profile <dev profile>
      --space-id <space> --other-profile <second identity> --json
      live.json` (add `--allow-wake` only when a warehouse start is
      acceptable) and record the three verdicts in the table above.
- [ ] Criterion 1 already FAILS offline (104x one prose scan). The
      conditional living-tiles build (an `execute_attachment_query` client,
      `mip_app.genie_tiles`, routes, a server pin store, a refresh job)
      belongs to W5d `w5-genie-provenance-tiles`, and only on a 'build'
      verdict; it must first cap rows per tile or prove a cheaper
      fail-closed row scan. Otherwise record it here as not built with the
      measured reason.
