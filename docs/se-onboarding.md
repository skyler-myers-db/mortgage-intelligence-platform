# SE onboarding — customer-workspace deploy in under an hour

**Audience.** Entrada/Databricks SE deploying Module 0 of the Mortgage
Intelligence Platform into a customer's Databricks workspace. Zero prior
familiarity with the repo assumed; end-state is a working app URL you can
demo to the customer's Head of Growth.

**Scope.** First-time deploy. For recurring operator concerns
(cold-start, Genie fallback, parity regression, credential rotation), see
[`docs/runbook.md`](runbook.md). Customer-facing security posture is summarized
in [`docs/security-and-compliance.md`](security-and-compliance.md); detailed
workspace grant inventories stay in the internal implementation packet.

**Budget.** 45 minutes of hands-on + ~15 minutes of bundle-run wall time
(silver refresh + gold CTAS against an idle 2X-Small warehouse).

**Tenancy posture.** Module 0 is a per-deployment product, not a shared
row-level multi-tenant SaaS. One customer workspace maps to one UC catalog,
one Lakebase state database, one Genie space, one app URL, and one configured
lender identity. Isolation is enforced at the Databricks deployment boundary;
`mip.ref.lender_dictionary` is the tenant-lender override point for gold
transformations, and `MIP_LENDER_NAME`, `MIP_LENDER_NMLS_ID`, and optional
`MIP_TENANT_ID` drive the app label and governed disclosure namespace.
`MIP_DEFAULT_CATALOG` drives the
SQL renderer, backend `qualify()` calls, Spark Python jobs, and Genie
provisioning, so keep it equal to the bundle `uc_catalog` variable. A future
shared-SaaS deployment would need explicit row-level tenant predicates and RLS;
that is out of scope for Module 0.

**API paths.** Operator commands use canonical `/api/v1/*` paths. Deprecated
`/api/*` aliases still work during the Module 0 transition window, but new
customer procedures should not depend on them.

---

## 0. Prerequisites (5 minutes — do before the customer call)

On your laptop:

- [ ] `databricks --version` ≥ 0.240.0 (`brew install databricks/tap/databricks` or equivalent).
- [ ] `python --version` ≥ 3.11.
- [ ] `node --version` ≥ 20 and `npm --version` ≥ 10.
- [ ] `gh auth status` green (needed if you will rotate CI secrets — not required for deploy).
- [ ] `git clone git@github.com:skyler-myers-db/mortgage-intelligence-platform.git && cd mortgage-intelligence-platform`.

Supported demo browsers:

- Chrome / Edge 111+.
- Safari 16.4+.
- Firefox 121+.

The SPA intentionally uses modern CSS (`container` queries, `:has()`,
`color-mix()`, logical sizing, and native `accent-color`) rather than a
legacy transpilation chain. If a customer mandates an older locked-down
browser or Firefox ESR below 121, run the browser matrix in §5 before
the customer demo and treat any CSS fallback work as a customer-specific
deployment requirement.

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
# GENIE_SPACE_ID may be blank on first deploy; scripts/deploy.sh will
# provision the space before applying the app resource and write
# genie/space_id.txt.
GENIE_SPACE_ID=
# OPTIONAL: set the UC catalog name if the customer uses a non-default name
# (default is "mip"). scripts/deploy.sh step 1a renders sql/_rendered/**
# for this catalog before the bundle runs, so CTAS lands in the right place
# on first deploy. See docs/runbook-multi-catalog.md for details.
# MIP_DEFAULT_CATALOG=summit_mortgage
# Customer-facing legal identity shown in the app and used by governed copy.
# The exact name/NMLS pair must first be added to the source-controlled registry
# in backend/schemas/lender_identity.py through an independently reviewed PR;
# runtime configuration cannot create a new lender-text exemption.
MIP_LENDER_NAME=<exact reviewed customer legal lender name>
MIP_LENDER_NMLS_ID=<matching reviewed customer lender NMLS id>
# Optional: override the Lakebase disclosure namespace. If unset, the app
# derives it from MIP_LENDER_NAME; Summit dev keeps the seeded "summit"
# namespace for backwards compatibility.
# MIP_TENANT_ID=acme_mortgage
EOF
```

Env-var names are authoritative in
[`backend/config/settings.py`](../backend/config/settings.py).
The `BUNDLE_VAR_*` mapping (`DATABRICKS_WAREHOUSE_ID` →
`BUNDLE_VAR_sql_warehouse_id`, `GENIE_SPACE_ID` →
`BUNDLE_VAR_genie_space_id`, and the lender/NMLS/tenant values → their
Lakebase migration variables) happens inside
`tools/databricks/bundle_env.py` — no extra exports needed.

The migration atomically installs reviewed generic email, direct-mail, and SMS
disclosures for this exact lender/NMLS/tenant identity. State-specific legal
rows remain explicit reviewed overrides; a customer deployment no longer
depends on the Summit sample disclosure namespace.

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

If this is a customer fork, rebind the bundle's single workspace-host anchor
once so every target points at the customer workspace:

```bash
./scripts/configure-workspace.sh "$DATABRICKS_HOST"
```

The helper normalizes the URL, updates only the `&default_host` line in
[`databricks.yml`](../databricks.yml), and runs `make check-workspace-host`.

---

## 3. Confirm the governed deployer (5 minutes — BEFORE deployment)

Use the customer-approved metastore administrator as the deploying identity and
set `MIP_UC_APPROVED_OWNER_PRINCIPALS` when an approved owner other than that
identity already owns the target catalog or schema. Keep every App and M2M
identity out of those owner principals and groups.

Do not apply a separate manual grant packet. The command-of-record creates and
verifies the minimal pipeline namespace before bundle apply, runs the complete
catalog DDL after apply, converges the exact App/runtime grants, and performs an
authoritative grants postflight. An inconclusive owner, group-membership, or
grant proof fails the deployment closed.

---

## 4. Deploy (one command, ~15 minutes wall time)

**Deploy lifecycle.** The Databricks App lifecycle still has two API
phases — direct bundle resource deploy, then app snapshot promotion —
but `./scripts/deploy.sh` runs both. Use the script for customer first
deploys because it provisions/rebinds Genie, maps `.env.local` to
`BUNDLE_VAR_*`, and runs the direct deployment plan before apply. The
internal bundle apply is selector-bounded to non-App resources; unrestricted
bundle mutation and direct App promotion are unsupported because they bypass
the signed deployment, rollback, migration, and proof contract.

```bash
# One command: env-aware direct bundle validate/plan/deploy, app promotion, jobs, refreshes, and Genie provision
./scripts/deploy.sh -t dev
# Expected last line: "[deploy] complete."
```

What `scripts/deploy.sh` does is enumerated in
[`docs/runbook.md`](runbook.md) §4 (steps 1–10). Re-running is
idempotent — safe after a partial failure.

On first deploy, Genie provisioning writes `genie/space_id.txt`.
Keeping the value in `.env.local` makes later manual wrapper invocations
more explicit:

```bash
echo "GENIE_SPACE_ID=$(cat genie/space_id.txt)" >> .env.local
```

---

## 5. First-boot verification (3 minutes)

```bash
# 1. Find the app URL
databricks apps get mip-app --profile DEFAULT | jq -r .url
# Example: https://mip-app-<id>.<region>.databricksapps.com

export MIP_APP_URL=$(databricks apps get mip-app --profile DEFAULT | jq -r .url)
export MIP_BEARER_TOKEN=$(databricks auth token --profile DEFAULT -o json | jq -r .access_token)

# 2. Authenticated health probe (cold-start: retry 3x, 10 s apart)
for i in 1 2 3; do
  curl -sSf -H "Authorization: Bearer $MIP_BEARER_TOKEN" "$MIP_APP_URL/api/v1/health" \
    | jq -e '{
      status,
      warehouse: .dependencies.warehouse,
      lakebase: .dependencies.lakebase,
      genie: .dependencies.genie,
      warehouse_breaker: .circuit_breakers.warehouse,
      lakebase_breaker: .circuit_breakers.lakebase,
      genie_breaker: .circuit_breakers.genie
    }'
  sleep 10
done
# Expected final state:
#   {"status": "ok", "warehouse": "up", "lakebase": "up", "genie": "up",
#    "warehouse_breaker": "closed", "lakebase_breaker": "closed", "genie_breaker": "closed"}

# 3. End-to-end smoke
./scripts/smoke_live.sh
# Expected health line includes:
# "[smoke] health ok · warehouse/lakebase/genie all up · breaker states present"
# Expected last line: "[smoke] PASS · <target app url>"
```

Open the app URL in a browser. The Portfolio page loads on first hit,
then Segments, Leads, Borrower 360, Approvals, Audit, Genie. Click any
evidence chip to prove the drawer opens and cites `mip.gold.*` rows.

---

## 6. Known first-boot quirks

### 6.1 Warehouse warm-start (~30 s)

The 2X-Small serverless warehouse auto-stops after 10 min idle. The
first query after deploy is a cold start — 30–60 s. `/api/v1/health` may
flap `warehouse: "down"` → `"up"` during this window; the circuit
breaker opens and closes once. **Do not redeploy.** The retry loop in §5
handles it.

### 6.2 Lakebase cold start

Lakebase Postgres also has a cold start (~10 s). First
`POST /api/v1/outreach/approve` after a cold window may return 503 once;
the frontend retries automatically.

### 6.3 Genie first-ask

First `/api/v1/genie/message` call after Genie space creation takes 10–30 s.
The app returns `source: "degraded"` with no fabricated metrics during that
window. To prime the space before a demo, see [`docs/runbook.md`](runbook.md)
§1.3.

### 6.4 Frontend shell caches stale JS

The Databricks App edge caches the SPA bundle aggressively. After the governed
deployment script promotes a new snapshot, browsers that had the app open may
serve the previous bundle from cache. Hard-refresh
(Cmd-Shift-R / Ctrl-Shift-R) once after deployment to evict it.

### 6.5 Dashboards show blanks on day 0

`delta_vs_prior_*` widgets, approval-rate, and outreach-rate cells show
`0` / `NULL` / "pending" on a fresh deploy — they need ≥ 2 daily
snapshot rows separated by ≥ 7 days. This is documented behavior. See
[`docs/dashboards.md`](dashboards.md) before explaining it to the
customer.

---

## 7. If X fails

### 7.1 "PERMISSION_DENIED" on `mip.gold.*` or `mip.silver.*`

The command-of-record was run under a non-approved owner/metastore identity, or
its automated grant convergence/postflight did not complete. Correct that
identity or configuration and re-run `./scripts/deploy.sh -t dev`; do not patch
runtime grants manually, because the deployment verifies their exact shape.

### 7.2 `/api/v1/health` reports `warehouse: "down"` for > 60 s

Check `DATABRICKS_WAREHOUSE_ID` in `.env.local` matches the actual
warehouse id:

```bash
databricks warehouses list | jq -r '.[] | select(.name=="mip_serverless_sql") | .id'
# Compare to: grep DATABRICKS_WAREHOUSE_ID .env.local
```

If the values differ, fix `.env.local` and re-run `./scripts/deploy.sh -t dev`.
That command is the only supported promotion path because it preserves the
governed App payload and every migration, proof, refresh, and smoke gate. A
wrong warehouse id is the single most common cause of a persistent red health
probe.

### 7.3 `/api/v1/audit/events` returns 503; POST `/api/v1/outreach/approve` fails

Lakebase creds are missing or the Lakebase role has not been
provisioned. Run:

```bash
databricks bundle run mip_lakebase_migrate -t dev
```

Then re-probe `/api/v1/health` — `lakebase` should flip to `"up"` within
30 s. If it stays down, check that the Lakebase instance is RUNNING
(`databricks database list-instances`) and bounce if STOPPED
(customer-side billing can auto-stop instances).

### 7.4 `/api/v1/genie/message` always returns `source: "degraded"`

Three possible causes, in order of likelihood:

1. **`GENIE_SPACE_ID` not set.** Re-run
   `echo "GENIE_SPACE_ID=$(cat genie/space_id.txt)" >> .env.local`,
   re-run phase 1 deploy, re-run phase 2.
2. **Service principal missing `CAN RUN` on the space.** Re-run
   `./scripts/deploy.sh -t dev`; its identity-access convergence grants and
   verifies that exact permission.
3. **Semantics views unbound.** Re-run
   `databricks bundle run mip_refresh_scores -t dev` — the
   `refresh_semantics_views` task rebinds Genie's trusted assets.

### 7.5 App URL 404 or serves old JS after deployment

The governed deployment script failed before snapshot promotion, or the browser
cached the prior bundle.

```bash
# Verify the governed snapshot promotion actually ran
databricks apps get mip-app --profile DEFAULT -o json \
  | jq '{app_status: .app_status.state, compute_status: .compute_status.state, active_deployment: .active_deployment.deployment_id}'
# Expect: app_status RUNNING, compute_status ACTIVE, active_deployment populated.
```

If no active deployment is populated, inspect the command-of-record failure and
re-run `./scripts/deploy.sh -t dev`; never recover with a bare App deployment.
If the deployment shows SUCCEEDED but the browser still serves stale JS,
hard-refresh (§6.4). If still stale, open in an incognito window to rule out
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
- [ ] Show the Admin **Data operations** panel and confirm the customer
      understands refreshes are operator-triggered from the app by default.
      FRED, lifecycle fallback, and Growth Agent monitor schedules deploy
      paused; unpause them only after the customer approves a recurring
      cadence and catalog isolation. The Growth Agent scheduler drafts
      Slack/Teams review messages only; it never sends them.
- [ ] Schedule the 90-day OAuth rotation on the customer's calendar
      (see [`docs/security/m2m-oauth-setup.md`](security/m2m-oauth-setup.md)
      "Rotation cadence") if they opted in to manual live validation.

---

*Owner: SE-lead + governance-security-reviewer. Review cadence: after
every customer deploy — real-world failures go in §7 as new subsections
until this doc covers them.*
