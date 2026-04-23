# SE onboarding — customer-workspace deploy in under an hour

**Audience.** Entrada/Databricks SE deploying Module 0 of the Mortgage
Intelligence Platform into a customer's Databricks workspace. Zero prior
familiarity with the repo assumed; end-state is a working app URL you can
demo to the customer's Head of Growth.

**Scope.** First-time deploy. For recurring operator concerns
(cold-start, Genie fallback, parity regression, credential rotation), see
[`docs/runbook.md`](runbook.md). For UC grant details, see
[`docs/security/GRANTS.md`](security/GRANTS.md). This doc links to both.

**Budget.** 45 minutes of hands-on + ~15 minutes of bundle-run wall time
(silver refresh + gold CTAS against an idle 2X-Small warehouse).

---

## 0. Prerequisites (5 minutes — do before the customer call)

On your laptop:

- [ ] `databricks --version` ≥ 0.240.0 (`brew install databricks/tap/databricks` or equivalent).
- [ ] `python --version` ≥ 3.11.
- [ ] `node --version` ≥ 20 and `npm --version` ≥ 10.
- [ ] `gh auth status` green (needed if you will rotate CI secrets — not required for deploy).
- [ ] `git clone git@github.com:skyler-myers-db/mortgage-intelligence-platform.git && cd mortgage-intelligence-platform`.

On the customer workspace, before you start the clock:

- [ ] Your user (or a paired customer admin) has **metastore admin** on
      the customer's UC metastore. Without this, §3 fails.
- [ ] A **serverless** SQL warehouse is reachable (Databricks Apps
      requires serverless — pro/classic is not supported).
- [ ] Delta Sharing is enabled on the metastore and the customer has
      already accepted the Cotality provider invite. Accepting the
      provider is a Cotality-side conversation, not something you can
      do from the CLI.
- [ ] The customer has billing enabled for Lakebase (Postgres instances
      bill independently of the warehouse).
- [ ] (Optional) FRED API key. The FRED ingest job (`jobs/fred_rates_ingest
      .py`) currently uses the **public unauthenticated** `fredgraph.csv`
      endpoint, so no key is required for first deploy. If the customer wants
      authenticated FRED access later (higher rate limits, private series),
      the key lands in `.env.local` as `FRED_API_KEY=` and is wired through
      the bundle's `fred_api_key` variable. Absent key = log-at-warn, never
      crash — deploy-day is safe without one.

---

## 1. Configure `.env.local` (2 minutes)

Create `.env.local` at the repo root (gitignored — never commit):

```bash
# Copy template
cat > .env.local <<'EOF'
DATABRICKS_HOST=https://<customer-workspace>.cloud.databricks.com
DATABRICKS_TOKEN=<PAT from workspace User Settings -> Developer -> Access Tokens>
DATABRICKS_WAREHOUSE_ID=<serverless warehouse id from Compute -> SQL warehouses>
# GENIE_SPACE_ID is written by step 4; leave blank on first deploy.
GENIE_SPACE_ID=
# OPTIONAL: set the UC catalog name if the customer uses a non-default name
# (default is "mip"). scripts/deploy.sh step 1a renders sql/_rendered/**
# for this catalog before the bundle runs, so CTAS lands in the right place
# on first deploy. See docs/runbook-multi-catalog.md for details.
# MIP_DEFAULT_CATALOG=summit_mortgage
EOF
```

Env-var names are authoritative in
[`backend/config/settings.py`](../backend/config/settings.py) lines 84–94.
The `BUNDLE_VAR_*` mapping (`DATABRICKS_WAREHOUSE_ID` →
`BUNDLE_VAR_sql_warehouse_id`, `GENIE_SPACE_ID` →
`BUNDLE_VAR_genie_space_id`) happens inside
`tools/databricks/bundle_env.py` — no extra exports needed.

---

## 2. Point the Databricks CLI at the customer workspace (1 minute)

```bash
databricks auth login --host "$DATABRICKS_HOST" --profile DEFAULT
# Browser flow — sign in as yourself (SE) or as the customer's admin.
databricks current-user me | jq .userName
# Expect: your email address or the admin SP name.
```

The `DEFAULT` profile is what every `databricks bundle` command binds to
by default. If the customer uses a different profile name, edit the
`profile:` line in [`databricks.yml`](../databricks.yml) lines 32 / 52
to match, or pass `--profile` explicitly.

---

## 3. Apply UC grants (5 minutes — BEFORE `bundle deploy`)

Open [`docs/security/GRANTS.md`](security/GRANTS.md) and execute §§1–8 in
order against the customer workspace (Databricks SQL editor, any
warehouse). Sections 1, 2, 3, 5a, 6, 7 are required; 4 and 5b are
conditional. The whole section is copy-paste-able; budget 5 minutes
actual work + a few minutes if the metastore admin needs to switch
seats.

**Do not skip this step.** `databricks bundle deploy` does not need the
grants (it runs as your admin user) but the app's first boot does —
skipping means §6 fails with `PERMISSION_DENIED` and you waste the
warehouse warm-up time diagnosing it.

---

## 4. Deploy (one command, ~15 minutes wall time)

**Two-step deploy gotcha — read first.** The Databricks App lifecycle has
two phases:

1. `databricks bundle deploy -t dev` — **uploads** source and provisions
   every non-app resource (warehouse, Lakebase, jobs, pipelines,
   dashboards, MLflow experiment). The app resource is registered but
   its compute is not started.
2. `databricks apps deploy mip-app` — **promotes** the uploaded source
   to the running app compute. Until this runs, the app URL serves the
   previous revision (or 404 on first ever deploy).

`./scripts/deploy.sh` runs phase 1. Phase 2 is a separate command the SE
runs after the first bundle deploy succeeds. Forgetting phase 2 is the
most common first-deploy mistake — you see a green CLI log and a 404 at
the app URL.

```bash
# Phase 1: bundle deploy + jobs + silver/gold refresh + Genie provision
./scripts/deploy.sh
# Expected last line: "[deploy.sh] OK — all 10 steps complete."

# Phase 2: promote uploaded source to running app compute
databricks apps deploy mip-app
# Expected: "deployment_id: ..." + state SUCCEEDED within ~2 min.
```

What `scripts/deploy.sh` does is enumerated in
[`docs/runbook.md`](runbook.md) §4 (steps 1–10). Re-running is
idempotent — safe after a partial failure.

On first deploy, step 9 (Genie provisioning) writes
`genie/space_id.txt`. Append it to `.env.local`:

```bash
echo "GENIE_SPACE_ID=$(cat genie/space_id.txt)" >> .env.local
```

---

## 5. First-boot verification (3 minutes)

```bash
# 1. Find the app URL
databricks apps get mip-app | jq -r .url
# Example: https://mip-app-<id>.<region>.databricksapps.com

export MIP_APP_URL=$(databricks apps get mip-app | jq -r .url)

# 2. Health probe (cold-start: retry 3x, 10 s apart)
for i in 1 2 3; do
  curl -sSf "$MIP_APP_URL/api/health" | jq '{status, warehouse, lakebase, genie}'
  sleep 10
done
# Expected final state:
#   {"status": "ok", "warehouse": "up", "lakebase": "up", "genie": "up"}

# 3. End-to-end smoke
./scripts/smoke_live.sh
# Expected last line: "smoke_live.sh: OK — 5/5 endpoints green."
```

Open the app URL in a browser. The Portfolio page loads on first hit,
then Segments, Leads, Borrower 360, Approvals, Audit, Genie. Click any
evidence chip to prove the drawer opens and cites `mip.gold.*` rows.

---

## 6. Known first-boot quirks

### 6.1 Warehouse warm-start (~30 s)

The 2X-Small serverless warehouse auto-stops after 15 min idle. The
first query after deploy is a cold start — 30–60 s. `/api/health` may
flap `warehouse: "down"` → `"up"` during this window; the circuit
breaker opens and closes once. **Do not redeploy.** The retry loop in §5
handles it.

### 6.2 Lakebase cold start

Lakebase Postgres also has a cold start (~10 s). First
`POST /api/outreach/approve` after a cold window may return 503 once;
the frontend retries automatically.

### 6.3 Genie first-ask

First `/api/genie/ask` call after Genie space creation takes 10–30 s.
The safe corpus in `backend/services/genie_answers.py` answers invisibly
with `source: "fallback"` during that window. To prime the space before
a demo, see [`docs/runbook.md`](runbook.md) §1.3.

### 6.4 Frontend shell caches stale JS

The Databricks App edge caches the SPA bundle aggressively. After a
`databricks apps deploy`, browsers that had the app open may serve the
previous bundle from cache. Hard-refresh (Cmd-Shift-R / Ctrl-Shift-R)
once after §4 phase 2 to evict it.

### 6.5 Dashboards show blanks on day 0

`delta_vs_prior_*` widgets, approval-rate, and outreach-rate cells show
`0` / `NULL` / "pending" on a fresh deploy — they need ≥ 2 daily
snapshot rows separated by ≥ 7 days. This is documented behavior. See
[`docs/dashboards.md`](dashboards.md) before explaining it to the
customer.

---

## 7. If X fails

### 7.1 "PERMISSION_DENIED" on `mip.gold.*` or `mip.silver.*`

You skipped §3 or one of the `GRANT` statements ran under a non-
metastore-admin identity. Re-run [`docs/security/GRANTS.md`](security/GRANTS.md)
§§1–5 as a metastore admin and re-run §5 verification here. No
redeploy needed — grants take effect on the next SQL statement.

### 7.2 `/api/health` reports `warehouse: "down"` for > 60 s

Check `DATABRICKS_WAREHOUSE_ID` in `.env.local` matches the actual
warehouse id:

```bash
databricks warehouses list | jq -r '.[] | select(.name=="mip_serverless_sql") | .id'
# Compare to: grep DATABRICKS_WAREHOUSE_ID .env.local
```

If the values differ, fix `.env.local`, re-run `./scripts/deploy.sh`
(phase 1), and `databricks apps deploy mip-app` (phase 2). A wrong
warehouse id is the single most common cause of a persistent red
health probe.

### 7.3 `/api/audit/events` returns 503; POST `/api/outreach/approve` fails

Lakebase creds are missing or the Lakebase role has not been
provisioned. Run:

```bash
databricks bundle run mip_lakebase_migrate -t dev
```

Then re-probe `/api/health` — `lakebase` should flip to `"up"` within
30 s. If it stays down, check that the Lakebase instance is RUNNING
(`databricks database list-instances`) and bounce if STOPPED
(customer-side billing can auto-stop instances).

### 7.4 `/api/genie/ask` always returns `source: "fallback"`

Three possible causes, in order of likelihood:

1. **`GENIE_SPACE_ID` not set.** Re-run
   `echo "GENIE_SPACE_ID=$(cat genie/space_id.txt)" >> .env.local`,
   re-run phase 1 deploy, re-run phase 2.
2. **Service principal missing `CAN RUN` on the space.** Fix per
   [`docs/security/GRANTS.md`](security/GRANTS.md) §7.
3. **Semantics views unbound.** Re-run
   `databricks bundle run mip_refresh_scores -t dev` — the
   `refresh_semantics_views` task rebinds Genie's trusted assets.

### 7.5 App URL 404 or serves old JS after phase 2

Phase 2 (`databricks apps deploy mip-app`) was skipped, errored, or
completed but the browser cached the prior bundle.

```bash
# Verify phase 2 actually ran
databricks apps get mip-app | jq '{state, active_deployment_id, pending_deployment_id}'
# Expect: state SUCCEEDED, active_deployment_id populated.

# If the state is CREATED but never DEPLOYED, phase 2 never ran:
databricks apps deploy mip-app
```

If phase 2 shows SUCCEEDED but the browser still serves stale JS, hard-
refresh (§6.4). If still stale, open in an incognito window to rule out
service-worker caching.

---

## 8. Handover to the customer

Before ending the SE session:

- [ ] Hand the customer admin the app URL and the `mip-admin` workspace
      group. Add their admin's email to the `MIP_ADMIN_EMAILS` env var
      (see [`docs/runbook.md`](runbook.md) §11) or workspace group per
      the RBAC gate in `backend/services/rbac.py`.
- [ ] Walk through the 6–8 minute talk track in
      [`docs/module0-talk-track.md`](module0-talk-track.md).
- [ ] Schedule the 90-day OAuth rotation on the customer's calendar
      (see [`docs/security/m2m-oauth-setup.md`](security/m2m-oauth-setup.md)
      "Rotation cadence") if they opted in to nightly CI.

---

*Owner: SE-lead + governance-security-reviewer. Review cadence: after
every customer deploy — real-world failures go in §7 as new subsections
until this doc covers them.*
