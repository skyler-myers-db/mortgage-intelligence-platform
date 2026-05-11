> **Internal implementation artifact. Not approved for public release.**

# Audit holes — round 4 (2026-04-23)

> **Note:** This document records a past state. `MIP_MOCK_MODE` has since been removed in the live-data cutover (commit `2f09424`). The text below is preserved for audit traceability.

**Scope.** Rounds 1/2/3 surfaced 50+ findings across scoring parity, error
paths, copy, tests, a11y, admin PUT contract, runbook, CTAS ordering.
Round 4 deliberately targets eight beats those rounds did NOT cover:
first-customer deploy, on-call diagnostic story, test theater, version
skew, data-contract vs real-data drift, undocumented env vars,
governance invariants vs current code, and SE onboarding.

All file references are absolute.

Severity key: **BLOCKER** = ships broken; **MAJOR** = real on-call or
governance impact; **MINOR** = polish or operator-friction.

---

## 1. First-customer-deploy end-to-end

### R4-01 — `.env.example` filename mismatch vs `deploy.sh` and runbook — **MINOR**

- `scripts/deploy.sh:149` tells the operator: `copy .env.local.example
  if present`.
- `backend/config/settings.py:26` points operators at `.env.example`.
- Runbook §4.0 preflight just says ".env.local populated".
- Actual template file is `/Users/entrada-mac/repos/mortgage-intelligence-platform/.env.example`.
  There is **no** `.env.local.example`.

A fresh operator sees three different filenames in three different
places. Low consequence (the real file is easy to find), but it
undermines the "zero-click, obvious" promise.

Fix: rename to `.env.local.example` (matches convention) OR update
deploy.sh + settings.py to consistently say `.env.example`.

### R4-02 — `.env.example` sets `LAKEBASE_DATABASE=mip`; code default is `mip_app_state` — **MAJOR**

- `/Users/entrada-mac/repos/mortgage-intelligence-platform/.env.example:22`: `LAKEBASE_DATABASE=mip`.
- `backend/config/settings.py:104`: default `lakebase_database = "mip_app_state"`.
- `jobs/sync_lifecycle_state.py:55,119`: defaults to `mip_app_state`.
- `tests/integration/test_lakebase_round_trip.py:51`: defaults to `mip_app_state`.

An SE who copies `.env.example` → `.env.local` and does not change the
line will fail Lakebase connect: the Lakebase bundle provisions the
`mip_app_state` database, not `mip`. `/api/audit/event` returns 503; the
approve path 503s. Recovery is "edit the .env.local line you just set";
discovery is not obvious because `/api/health` reports lakebase `up`
(the connect succeeds against the wrong db name if that db exists, or
fails with a low-level psycopg error the operator has to read).

Fix: change `.env.example:22` to `LAKEBASE_DATABASE=mip_app_state`.

### R4-03 — Runbook §1.2 uses `$LAKEBASE_DATABASE_NAME`; code reads `LAKEBASE_DATABASE` — **MAJOR**

- `docs/runbook.md:60`: `psql "host=$LAKEBASE_HOST user=$LAKEBASE_USER
  dbname=$LAKEBASE_DATABASE_NAME sslmode=require"`.
- No code path reads `LAKEBASE_DATABASE_NAME`; everywhere uses
  `LAKEBASE_DATABASE`.
- `docs/module0-real-data-plan.md:108,170` propagates the same wrong
  name.
- `.github/workflows/README.md:40` also lists `LAKEBASE_DATABASE_NAME`.

An on-call who follows the runbook recovery command runs `psql` with
an empty `dbname=` and gets `fatal: database "" does not exist`. They
debug a symptom, not the root.

Fix: replace `LAKEBASE_DATABASE_NAME` with `LAKEBASE_DATABASE` in those
three docs.

### R4-04 — `tools/verify_scaffold.py` is a smoke test, not a mis-setup catcher — **MINOR**

`verify_scaffold.py` (57 lines) only checks that 13 committed files
exist and that `.env` / `.env.local` / `secrets` are NOT tracked. It
does not verify:

- that `.env.local` exists locally (the thing an SE actually forgets);
- that required env vars are set;
- that `GENIE_SPACE_ID` was written by `provision_genie_space.py`;
- that `databricks` CLI is reachable;
- that the venv is activated.

`scripts/deploy.sh` §preflight does half of these; `verify_scaffold.py`
is redundant-lite and its pass tells an SE nothing about deploy
readiness. Recommend either deprecating it or merging into deploy.sh
preflight.

### R4-05 — Runbook §4 lists numbered steps that don't survive partial failure — **MINOR**

§4 lists 10 post-deploy steps invoked by `deploy.sh`. Steps 8 (Genie
provision) and 9 (smoke test) assume steps 1-7 succeeded. But the
`--skip-silver` flag (runbook §4 table) skips steps 4-5; if the first
gold refresh runs against an empty silver (because `--skip-silver` was
set to rerun a partial deploy where silver failed on step 5), step 7
writes a snapshot row with zero counts and the dashboard lights up
with all zeros for the rest of the day until the next snapshot. The
flag is a footgun. Recommend: `--skip-silver` should also `--skip-gold`.

---

## 2. On-call diagnostic story

### R4-06 — No runbook section for "KPIs are stuck at 0" — **MAJOR**

Runbook covers warehouse cold, Lakebase cold, Genie cold, degraded
banner, whole-UI-gone, parity red, deploy-from-scratch, PAT rotation,
stale silver, smoke check, kill drill, CI triage, accuracy evidence.
**Missing:** the single most common "customer pinged us in Slack"
complaint is "my dashboard KPIs are all zero."

A first-time on-call has no script. The diagnostic branches are:
- Gold stale (silver refresh didn't run): `SELECT MAX(refreshed_at)
  FROM mip.gold.lead_population`.
- Lakebase empty (no approvals yet): `SELECT COUNT(*) FROM
  mip_app.approvals`. Expected on day-1 deploy per runbook §4 — but
  no diagnostic query is named.
- Lifecycle-sync job dropped: `SELECT COUNT(*) FROM
  mip.gold.borrower_lifecycle_state`. Should equal `gold.borrower_360`
  count; if zero, the sync job hasn't fired.
- Funnel snapshot missing: `SELECT MAX(snapshot_date) FROM
  mip.gold.funnel_snapshot_daily`. If > 2 days old, WoW deltas stay 0.

These four queries should be a named §2.4 in the runbook. None exist.

### R4-07 — No "approvals aren't showing up" diagnostic section — **MAJOR**

Same gap. `POST /api/outreach/approve` writes to `mip_app.approvals` +
`mip_app.action_audit`. The audit list endpoint reads `action_audit`
only. An on-call query path:

- Check `mip_app.action_audit` for `event_type='APPROVE'` in the last
  hour. Missing? Lakebase write is failing silently (it wouldn't — it
  503s — but the UI's retry banner could be masked).
- Check `/api/outreach/approve` BackgroundTask — `trigger_lifecycle_sync`
  runs fire-and-forget; a failing sync leaves the approvals landed in
  Lakebase but never mirrored to `gold.borrower_lifecycle_state`.
- Check frontend cache: the approval row is appended client-side via
  `AppContext` — stale frontend JS not refreshing could hide new rows.

None documented. Fix: §2.5 runbook with these three queries.

### R4-08 — No "map won't drill into states" diagnostic section — **MAJOR**

Map uses `/api/geo/state-rollups` → `gold.funnel_snapshot_daily` +
`gold.state_top_segment`. Common failure modes:

- Zero-state snapshot (day-1, no sync run yet) → map grey.
- `state_top_segment` refresh failed → hover tooltip shows segment
  but click yields empty county list.
- Client-side state filter not plumbed — round-2 #10 found AbortSignal
  leaks, round-3 #7 found CURRENT_TIMESTAMP non-determinism.

No runbook section walks an on-call through the specific geo paths.

### R4-09 — No single health-dashboard; `/api/health` misses read paths — **MAJOR**

`/api/health` probes warehouse/Lakebase/Genie for reachability but does
NOT probe the data-plane freshness. An on-call with "KPIs are 0" has
to manually query `MAX(refreshed_at)` across four tables. A `/api/health/data`
endpoint that returns `{"lead_population_refreshed_at": ..., "funnel_snapshot_date": ..., "lifecycle_state_row_count": ...}`
would cut on-call minutes-to-diagnosis from ~10 minutes to ~30 seconds.

Recommend: add the data-plane health endpoint, wire `/admin/sources`
to surface the same.

### R4-10 — `_probe_lakebase` returns `up` when Lakebase is unconfigured — **MAJOR**

`backend/api/health.py:115-120`: if `lakebase_host` or `lakebase_user`
is empty, the probe returns True ("up"). Rationale in comment:
"dev environments where Lakebase isn't expected to be available."

But in production: an SE who forgets to set `LAKEBASE_HOST` in
`.env.local` will see `/api/health` report `lakebase: up` **and**
`status: ok`. The first `POST /api/outreach/approve` will 503. The
on-call checks `/api/health`, sees green, concludes the app is fine,
and chases ghosts.

Fix: treat unconfigured Lakebase as `down` in any non-local environment
(check `settings.app_env != "local"`).

---

## 3. Test theater vs real coverage

### R4-11 — `tests/unit/test_api_routes.py` unchanged since round-3 flagged it — **MAJOR**

Round 3 #3 surfaced this: 19 routes get hit, each asserts only
`status == 200`. A silent schema regression (missing `rationale`, typo
in `approval_status` literal, removed field) passes.

Fix (concrete): replace each check with
`TypeAdapter(Schema).validate_python(response.json())`. Routes without
a `response_model` (the POST `/api/portfolio/*` endpoints) also need
their response models declared — this is low-cost work for a future
slice, but the current test is actively reporting green on shape
regressions.

### R4-12 — No unit test pins `LeadSummary.approval_status` literal set — **MAJOR**

If a future refactor adds `"hold"` to the `ApprovalStatus` Literal (see
§5 below), no test catches the shift. Sibling: no test pins the
`OfferType` literal set against `NBO_PRODUCT_LABELS.keys()` — they
diverge silently.

Fix: add `tests/unit/test_literal_contracts.py` asserting
`typing.get_args(ApprovalStatus) == ("pending", "approved", "rejected")`
and `set(typing.get_args(OfferType)) == set(NBO_PRODUCT_LABELS) | {"recapture"}`.

### R4-13 — Skipped/xfailed audit — **CLEAN**

`grep -rn "pytest.mark.skip\|pytest.mark.xfail" tests/` returns only
the two creds-gated integration tests (`test_lakebase_round_trip.py`,
`test_approve_stress.py`). Both correctly use `skipif` conditions on
env vars and are intentional. No lingering literal `@skip` / `@xfail`
decorators. Clean — a Round-4 negative finding worth logging so Round
5 doesn't redo this beat.

### R4-14 — Genie regression tests intentionally gated on live creds — **MINOR**

`tests/integration/test_genie_regression.py` + `test_genie_live.py` +
`test_genie_fuzz.py` all skip without creds. What runs in PR CI for
Genie is `tests/unit/test_genie_fallback.py` and
`test_genie_repository.py`. Current policy is stricter than this April
finding: production modules must not contain any local analytic response path.
The replacement guard asserts `backend/services/genie_answers.py` only carries
wire models and prompt-suggestion helpers.

---

## 4. Rolling deploy + version skew

### R4-15 — Backend does not tolerate a renamed `LeadSummary` field — **MAJOR**

A client with stale JS (commit A) still reads an older response shape.
If commit B renames `equity_estimate` to `equity_dollar_amount`, the
old JS reads `undefined` and renders blank cells. There is no
deprecation window; the rename is effective immediately.

This is latent — no recent rename. But no CI rule enforces
"deprecate-then-remove" on public response fields. A future
refactor-happy PR can silently break users mid-session.

Recommend: add `CONTRIBUTING.md` §schema-versioning rule + a
`tests/unit/test_public_schema_frozen.py` that snapshots every
`response_model` schema to JSON and fails on removal without a
deprecation marker.

### R4-16 — `X-Truncated-At` header ships without cached-client regression test — **MINOR**

`backend/api/leads.py:76` sets `X-Truncated-At` when `len(leads) >=
limit`. Old frontends don't read it (correct — extra header is fine).
But the edge case `len(leads) == limit and no truncation happened`
fires a false-positive `X-Truncated-At`. Comment on line 73-76
acknowledges this. Minor, but undocumented in the response-model
docstring. Recommend: add a `Response` body field
`truncated: bool = False` AS WELL AS the header, so new clients can
distinguish "exactly N rows" vs "capped at N".

### R4-17 — `data_refreshed_at` tz fix: verify no stored-state dependency — **MINOR**

The tz fix affects response payload only (not a persisted column).
No migration needed. Round 4 confirms: the `refreshed_at` columns in
`mip.gold.*` tables are already `TIMESTAMP` with UC time-zone semantics;
the fix was in `backend/services/repositories/databricks_repo.py`'s
UTC normalization layer. No version skew. Clean.

### R4-18 — `X-Forwarded-Email` fallback counter in `/api/health` lacks alert threshold — **MINOR**

`backend/api/health.py:256`: `fallback_identity_fallbacks_total` is
surfaced but there's no documentation on what value crosses from "ok"
(dev local, header absent) to "broken" (Databricks Apps should always
send the header). Without a threshold or alert, operators will never
look at it. Recommend: add to `docs/observability.md` a "what to alert
on" line: `fallback_identity_fallbacks_total > 0 AND app_env !=
"local"` is a regression signal.

---

## 5. Data contracts vs actual data

### R4-19 — `ApprovalStatus` Literal rejects `"hold"` but downstream jobs write it — **BLOCKER (latent)**

Concrete evidence chain:

- `backend/schemas/lead.py:9`: `ApprovalStatus = Literal["pending",
  "approved", "rejected"]` — **3 values**.
- `sql/ddl/003_gold_tables.sql:274`: column comment says `'pending /
  approved / rejected / hold'` — **4 values**.
- `sql/metric_views/lead_generation_metric_view.sql:24`: same 4.
- `lakebase/schema.sql:54`: `CHECK (action IN ('approve', 'reject',
  'hold'))` — **3 write actions, one emits `hold`**.
- `lakebase/seed_campaigns.sql:81`: seeds an `'hold'` row.
- `jobs/sync_lifecycle_state.py:160`: writes `approval_status='hold'`
  into `mip.gold.borrower_lifecycle_state`.

**Current safety.** `gold.borrower_360.approval_status` (line 468 of
`gold_borrower_360.sql`) is hard-coded `'pending'`. `gold.lead_population`
reads `approval_status` from `borrower_360`, NOT from
`borrower_lifecycle_state`. The API's redactor
(`backend/services/pii_redaction.py:428`) reads the lead_population
column. So today, `'hold'` never reaches the `LeadSummary` validator.

**Latent BLOCKER.** The next slice that joins `borrower_lifecycle_state`
into `borrower_360` (the documented roadmap direction — lifecycle is
the source of truth for live status) will silently produce `'hold'`
rows, and every `/api/leads` call that hits one will 500 with a
Pydantic `ValidationError`. The test suite misses it (see R4-12).

Fix: extend `ApprovalStatus = Literal["pending", "approved",
"rejected", "hold"]` **now**, before the join is wired.

### R4-20 — `segment_codes: list[SegmentCode]` has no empty-list safety in backend — **MINOR**

`backend/schemas/lead.py:35`: typed as `list[SegmentCode]` (could be
empty). Frontend `borrower-360.tsx:136` uses `b.segment_codes[0]` with
`?. ?? fallback` — safe. But `segment-intelligence.tsx:159` does
`l.segment_codes.some(...)` — safe for empty. `LeadTable.tsx:73,598`
maps/slices — safe for empty. Clean on the frontend.

However, `gold_borrower_360.sql` does not constrain `segment_codes` to
non-empty; a borrower with `opportunity_score >= 50` but no fired
segment still lands in `lead_population`. The Segment Intelligence
counts will miss them (they're filtered by active segment). Small
discrepancy — "total leads" across the 6 segment cards ≠ "total rows
in `/api/leads`".

Recommend: drop into segment_intelligence a "no segment" category OR
exclude segmentless rows from `gold.lead_population`. Either fixes the
count parity.

### R4-21 — `opportunity_score` clamped at Python boundary; SQL UDF also clamped; parity OK — **CLEAN**

`backend/services/scoring.py:130`: `max(0, min(100, round(weighted_sum)))`.
`sql/uc_functions/fn_lead_score.sql:49-51`: `LEAST(100, GREATEST(0,
round(...)))`. Matched. Round-4 negative finding; no drift.

### R4-22 — Redactor `_FORBIDDEN_OUTPUT_KEYS` excludes `owner_name_hash` itself — **MAJOR**

`backend/services/pii_redaction.py:239-253`: forbidden-keys set includes
`owner_name_hash_raw` but NOT bare `owner_name_hash`.

`owner_name_hash` is SHA-256 of `LOWER(TRIM(owner_1_full_name)) || ':'
|| salt`. If the salt is workspace-stable (per `silver_property_master.sql:16`
"sha2 of LOWER(TRIM(name)) || ':' || salt"), then the hash is a **stable
pseudonymous identifier across borrowers** — a cross-tenant correlation
vector.

Governance §1 says "no raw owner names in API responses." Hashes aren't
raw names, but leaking them on `/api/borrowers/*` or `/api/leads`
would let a malicious actor with two different workspaces' exports
link "the same owner" across them. Currently no redactor emits
`owner_name_hash` to output (both redactors consume it and emit
synthesized `display_name`), BUT a future refactor that forgets to
drop the key won't be caught by `_enforce_no_forbidden_keys`.

Fix: add `"owner_name_hash"` to `_FORBIDDEN_OUTPUT_KEYS`. Defense in
depth — costs nothing.

### R4-23 — `admin/rules` PUT mutates `_RULES_OVERRIDE` before audit write — **MAJOR**

`backend/api/admin.py:105-112`:
```
_RULES_OVERRIDE.update(payload.overrides)   # mutates in-memory
audit.write(...)                             # can raise LakebaseError
```

If Lakebase is down, audit.write raises, the override is already
applied but the HTTP response is 500, and no audit row exists for the
change. Governance §4 (every write is audited) is violated silently.

Fix: swap the order — audit.write first, then apply the mutation; OR
wrap in a try/except that reverts the override on audit failure.

---

## 6. Undocumented env vars

### R4-24 — `.env.example` has `MIP_DEMO_LENDER`; code reads `MIP_LENDER_NAME` — **MAJOR**

- `.env.example:3`: `MIP_DEMO_LENDER=Summit Mortgage`.
- `backend/config/settings.py:60`: field `mip_lender_name` → env var
  `MIP_LENDER_NAME`.
- `grep -rn "MIP_DEMO_LENDER" backend/ frontend/` → **zero hits**.

An operator who sets `MIP_DEMO_LENDER` sees the lender banner stay at
the default "Summit Mortgage" forever — the env var is a no-op.
`.env.example` is lying.

Fix: rename line 3 to `MIP_LENDER_NAME=Summit Mortgage`.

### R4-25 — 14 env vars read by Settings but absent from `.env.example` — **MAJOR**

Missing from the template but referenced in code:

| Env var | Settings field | Referenced at |
|---|---|---|
| `MIP_MARKET_RATE` | `mip_market_rate` | settings.py:68 |
| `MIP_MIN_SPREAD_BPS` | `mip_min_spread_bps` | settings.py:69 |
| `MIP_MIN_EQUITY_PCT` | `mip_min_equity_pct` | settings.py:70 |
| `MIP_HELOC_EQUITY_MIN_PCT` | `mip_heloc_equity_min_pct` | settings.py:78 |
| `MIP_CASHOUT_EQUITY_MIN_PCT` | `mip_cashout_equity_min_pct` | settings.py:79 |
| `MIP_RETENTION_MIN_SPREAD_BPS` | `mip_retention_min_spread_bps` | settings.py:80 |
| `MIP_ADMIN_GROUP_NAME` | `admin_group_name` | settings.py:123 |
| `MIP_DEFAULT_ACTOR` | `default_actor` | settings.py:115 |
| `MIP_CACHE_TTL_S` | `mip_cache_ttl_s` | settings.py:129 |
| `MIP_PORTFOLIO_PREVIEW_TTL_S` | `mip_portfolio_preview_ttl_s` | settings.py:159 |
| `MIP_OTEL_ENDPOINT` | `mip_otel_endpoint` | settings.py:149 |
| `MIP_OTEL_HEADERS` | `mip_otel_headers` | settings.py:150 |
| `MIP_BYPASS_STARTUP_CHECKS` | (runtime) | settings.py:44 |
| `LAKEBASE_SSLMODE` | `lakebase_sslmode` | settings.py:107 |

All have sane defaults, so an SE can ship without setting them. But
an SE who wants to wire the OTLP exporter or use a non-`mip-admin`
workspace group name has no template to follow. Governance posture says
these are knobs; the template says they don't exist.

Fix: append a commented-out block to `.env.example` with each var, its
default, and a one-line explanation.

### R4-26 — Secrets not loggable via verbose-debug — **CLEAN**

Grep for `log.debug.*token`, `print.*token_val`, `Authorization.*{self.*token}`
across `backend/`. Only hit: `settings.py:233` logs `token_len=N` (safe).
No debug path exfiltrates the PAT or workspace identity. Good; round-4
negative finding confirmed.

---

## 7. Governance invariants vs current code

### R4-27 — Governance §1 `owner_name_hash` leak vector — **MAJOR** (covered by R4-22)

See R4-22.

### R4-28 — Governance §4 approval audit: approve 503s correctly, `admin_rules` PUT does NOT — **MAJOR** (covered by R4-23)

`POST /api/outreach/approve`: correct. 503 on Lakebase down, no state
change.

`PUT /api/admin/rules`: incorrect. State changes before audit, and a
Lakebase-down audit fails with 500 after mutation has landed. Silent
governance §4 break.

### R4-29 — Governance §5 rationale: every `/api/offers/recommend` path has non-None rationale — **CLEAN**

`backend/api/offers.py:305-319`: `rationale=_rationale_for(code, ...)`.
`_rationale_for` has a fallback `return "No active trigger..."` on
line 135 when no branch matches (shouldn't happen given
`_VALID_OFFER_TYPES` gate at line 264, but it's there). No code path
returns `rationale=None`.

But the schema at `backend/schemas/offer.py:121` has
`OfferAlternative.rationale: str | None = None` — `OfferAlternative`,
not `OfferRecommendation`. The main rec contract is `rationale: str`
(non-optional, line 53). Clean.

### R4-30 — `sql/ddl/001_catalogs_schemas.sql` GRANT section references retired `MIP_MOCK_MODE=false` — **MINOR**

Lines 26-29: comment says "no app path reads from these schemas until
Slice 4 flips MIP_MOCK_MODE=false". Per CLAUDE.md, `MIP_MOCK_MODE` is
retired — the app always reads live UC. The comment misleads a future
reader about the RBAC posture.

Fix: strike the sentence; replace with a pointer to the actual GRANT
policy (which doesn't exist; see R4-31).

---

## 8. Customer SE onboarding

### R4-31 — No `GRANTS.md` / RBAC matrix — **MAJOR**

The commercial product promise is: an Entrada SE can deploy a customer
workspace in < 1 hour. `sql/ddl/001_catalogs_schemas.sql:20-29` defers
the GRANT/REVOKE policy to "a future slice"; that slice never landed.

A customer SE opens `.env.local`, fills in DATABRICKS_HOST + TOKEN,
runs `./scripts/deploy.sh`, and the bundle deploy succeeds. But when
the deployed app queries gold, the app's service principal needs:

- SELECT on `mip.gold.*` and `mip.semantics.*` (for routers);
- SELECT on `mip.silver.*` (for the data-sources readiness endpoint);
- SELECT on `mip.ref.lender_dictionary` (for PII redactor's lender
  resolver);
- USE CATALOG `mip`, USE SCHEMA `mip.gold`, etc.
- CONNECT + CREATE + USAGE on the Lakebase instance;
- READ + MODIFY on `mip_app_state` database.

**None of this is documented.** An SE who installs the bundle gets
`permission denied` on first page load and has to grep the codebase to
figure out which GRANTs to run.

Fix: author `docs/security/GRANTS.md` with a copy-pasteable SQL block
that an SE runs once per customer workspace. Add to `scripts/deploy.sh`
as an optional step-0 "issue GRANTs" prompt. This is the last mile
blocking the "zero-click" promise.

### R4-32 — No explicit "< 1 hour SE onboarding" doc — **MAJOR**

Runbook §4 is the closest thing. It assumes familiarity with the bundle,
the CLI, and the repo layout. A new SE joining the team (or a partner
SE at the customer) wants: (a) which permissions they need in the
workspace (R4-31), (b) what values to put in `.env.local` (incomplete,
R4-25), (c) how to verify a green deploy (`smoke_live.sh` — not named
in any "new SE" doc), (d) what to show the customer first after
deploy.

`docs/partner-review-checklist.md` exists but is a review gate, not an
onboarding doc. Recommend: add `docs/se-onboarding.md` — single-page,
30-minute read, ends with a green `smoke_live.sh` output.

---

## Summary of severities

| Finding | Severity | Beat |
|---|---|---|
| R4-01 env template filename mismatch | MINOR | 1 |
| R4-02 `.env.example` LAKEBASE_DATABASE wrong default | **MAJOR** | 1 |
| R4-03 runbook `$LAKEBASE_DATABASE_NAME` wrong | **MAJOR** | 1 |
| R4-04 verify_scaffold.py is redundant | MINOR | 1 |
| R4-05 --skip-silver footgun | MINOR | 1 |
| R4-06 no "KPIs stuck at 0" runbook section | **MAJOR** | 2 |
| R4-07 no "approvals missing" runbook section | **MAJOR** | 2 |
| R4-08 no "map drill broken" runbook section | **MAJOR** | 2 |
| R4-09 no data-plane health endpoint | **MAJOR** | 2 |
| R4-10 `_probe_lakebase` false-green when unconfigured | **MAJOR** | 2 |
| R4-11 test_api_routes.py still status-only | **MAJOR** | 3 |
| R4-12 no literal-set contract test | **MAJOR** | 3 |
| R4-13 skip/xfail audit clean | CLEAN | 3 |
| R4-14 Genie regression gated; fallback-corpus not shape-tested | MINOR | 3 |
| R4-15 no schema-freeze rule for public response fields | **MAJOR** | 4 |
| R4-16 X-Truncated-At false-positive at exact-N | MINOR | 4 |
| R4-17 tz fix: no migration needed | CLEAN | 4 |
| R4-18 fallback-identity counter threshold undocumented | MINOR | 4 |
| R4-19 `ApprovalStatus` literal will reject `hold` | **BLOCKER** (latent) | 5 |
| R4-20 segment_codes can be empty (count parity) | MINOR | 5 |
| R4-21 opportunity_score clamping parity | CLEAN | 5 |
| R4-22 `_FORBIDDEN_OUTPUT_KEYS` omits `owner_name_hash` | **MAJOR** | 5 |
| R4-23 admin/rules PUT mutates before audit | **MAJOR** | 5, 7 |
| R4-24 `MIP_DEMO_LENDER` is a dead env var | **MAJOR** | 6 |
| R4-25 14 env vars undocumented in `.env.example` | **MAJOR** | 6 |
| R4-26 no token leak via log debug paths | CLEAN | 6 |
| R4-27 owner_name_hash not in forbidden set (= R4-22) | — | 7 |
| R4-28 admin PUT audit gap (= R4-23) | — | 7 |
| R4-29 rationale always non-None | CLEAN | 7 |
| R4-30 DDL comment references retired MIP_MOCK_MODE | MINOR | 7 |
| R4-31 no `GRANTS.md` / RBAC matrix | **MAJOR** | 8 |
| R4-32 no SE onboarding doc | **MAJOR** | 8 |

Total **novel** findings: 28 (plus 4 "clean" negative confirmations).

**BLOCKER-tier (latent):** R4-19 — ship the Literal extension BEFORE
the next lifecycle-join slice.

**MAJOR, urgent for customer deploys:** R4-02, R4-03, R4-10, R4-22,
R4-23, R4-24, R4-25, R4-31.

**MAJOR, on-call quality:** R4-06, R4-07, R4-08, R4-09, R4-11, R4-12,
R4-15, R4-32.

---

## Fixed inline

None. The uncommitted working tree (13 files modified by other agents;
6 unit tests currently failing in that tree) prevents safe inline
edits without colliding with in-progress work. All findings flagged for
the master agent to route.

## Gates (state at round-4 start; pre-existing, not introduced here)

- `.venv/bin/ruff check backend tests tools` → 1 finding (I001
  unsorted imports in `backend/services/pii_redaction.py:135`). Pre-existing in the uncommitted tree.
- `.venv/bin/python -m pytest tests/unit -q` → 6 failures in
  `test_admin_rules.py`, `test_genie_fallback.py`, `test_geo_repository.py`
  (4 cases). Pre-existing in the uncommitted tree.
- Frontend lint/build not run this round (no frontend edits).

The uncommitted state is another agent's active work; this round-4
report does not touch those files.

---

*Owner: qa-test-engineer. Intended consumer: master implementation
agent, for routing into the next sprint.*
