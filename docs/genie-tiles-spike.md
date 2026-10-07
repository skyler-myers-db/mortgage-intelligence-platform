# Living Genie tiles: spike (audit 2026-09-21 `wow-ai-5`)

**Status: prototype-first measurement only. Living tiles are NOT built.** Pinned
insights on Home stay static pins (`frontend/src/lib/pinnedInsights.ts`,
actor-scoped local storage, at most six, a short summary) and nothing
re-executes a Genie query after the answer was shown. This page records how the
spike is measured, what the offline run found, and the pass criteria a later
build must meet. The build itself (an `execute_attachment_query` client, a
`mip_app.genie_tiles` table, routes, a server-backed pin store and a refresh
job) is deferred to W5c `w5-genie-stop-context`, and only if every criterion
below passes; otherwise it is recorded as not built with the measured reason.

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

## Integrator checklist (after the W5b deploy)

- [ ] Re-run `python -m tools.genie_tiles_spike --offline` on the deployed
      commit and confirm the counters match this page within noise.
- [ ] The live probe (`--live`: message status by age bucket 1/7/30 days,
      `execute-message-attachment-query` only while `COMPLETED` or
      `EXECUTING_QUERY`, warehouse state before the call, App identity versus
      another identity) moved to W5c `w5-genie-stop-context` with the
      conditional build. Its output may carry only ids, states, counts,
      durations and HTTP status, never question text, SQL text or row values.
- [ ] Record the verdict for criteria 2-4 here, and size the build into the
      `w5-genie-stop-context` brief, or record it as not built with the
      measured reason (criterion 1 already fails as measured above).
