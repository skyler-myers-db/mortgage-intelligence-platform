# shellcheck shell=bash
# Deploy step file. Steps 5-10: App snapshot promotion and signed capture, silver/gold refresh,
# stale-rate guard, lifecycle sync, KPI snapshot backfill, Genie rebind.
# Sourced by scripts/deploy.sh in reviewed order; it runs in the entrypoint's
# shell with its strict mode, traps, and private variables, never on its own.
# shellcheck disable=SC2034  # top-level state assigned here is read by later step files.
[[ "${BASH_SOURCE[0]}" != "$0" ]] || {
  echo "[deploy] ${BASH_SOURCE[0]} is a step file; run ./scripts/deploy.sh instead." >&2
  exit 2
}

APP_NAME="${MIP_APP_NAME:-mip-app}"

# Make the snapshot deploy deterministic (2026-06-10 audit fix). Two platform
# races were observed in the wild, each failing this step on a fresh run:
#   1. App STOPPED (idle auto-stop / manual stop) -> "Cannot deploy app ...
#      as it is not in RUNNING state."
#   2. A prior interrupted governed promotion can leave an active/pending
#      deployment in progress. The selected non-App bundle apply above cannot
#      create one, but retry recovery must still wait for the existing state.
# Both are waitable states, not errors. Start the app if needed, then poll
# until no deployment is in flight before promoting the signed snapshot.
wait_for_app_deployable() {
  local compute pend active i
  assert_expected_app_identity "$APP_NAME"
  compute="$(databricks apps get "$APP_NAME" -o json 2>/dev/null | "$PYTHON" -c 'import json,sys; print((json.load(sys.stdin).get("compute_status") or {}).get("state",""))' || true)"
  if [[ "$compute" == "STOPPED" || "$compute" == "STOPPING" ]]; then
    step "app compute is ${compute} — starting before snapshot deploy"
    assert_expected_app_identity "$APP_NAME"
    run databricks apps start "$APP_NAME"
    assert_expected_app_identity "$APP_NAME"
  fi
  for i in $(seq 1 90); do
    assert_expected_app_identity "$APP_NAME"
    pend="$(databricks apps get "$APP_NAME" -o json 2>/dev/null | "$PYTHON" -c 'import json,sys; d=json.load(sys.stdin); print(((d.get("pending_deployment") or {}).get("status") or {}).get("state","NONE"))' || echo "UNKNOWN")"
    active="$(databricks apps get "$APP_NAME" -o json 2>/dev/null | "$PYTHON" -c 'import json,sys; d=json.load(sys.stdin); print(((d.get("active_deployment") or {}).get("status") or {}).get("state","NONE"))' || echo "UNKNOWN")"
    if [[ "$pend" == "NONE" && "$active" != "IN_PROGRESS" ]]; then
      return 0
    fi
    echo "  waiting for in-flight app deployment to settle (pending=${pend}, active=${active}) [${i}/90]"
    sleep 10
  done
  echo "${RED}[deploy] app deployment still in flight after 15 minutes; aborting snapshot deploy.${RST}" >&2
  return 1
}

emit_app_deploy_payload() {
  local destination="$1" source_code_path="$2" git_sha="$3"
  MIP_GIT_SHA="$git_sha" "$PYTHON" -m tools.databricks.app_deploy_payload \
    --source-code-path "$source_code_path" \
    --install-class "$CLIENT_INSTALL_CLASS" \
    --current-user-email "$APP_CURRENT_USER" \
    --app-env "$APP_RUNTIME_ENV" \
    --catalog "${MIP_DEFAULT_CATALOG:-mip}" \
    --schema "${MIP_DEFAULT_SCHEMA:-gold}" \
    --lakebase-instance "$MIP_LAKEBASE_INSTANCE" \
    --enable-campaign-treatment-runtime \
    "${OTEL_DEPLOY_PAYLOAD_ARGS[@]}" \
    > "$destination"
}

deploy_app_snapshot() {
  local label="$1"
  step "$label"
  APP_DEPLOY_PAYLOAD="$(mktemp -t mip-app-deploy.XXXXXX.json)"
  emit_app_deploy_payload "$APP_DEPLOY_PAYLOAD" "$APP_SOURCE_PATH" "$APP_GIT_SHA"
  assert_expected_app_identity "$APP_NAME"
  run "$PYTHON" -m tools.databricks.converge_static_app_source \
    --source-code-path "$APP_SOURCE_PATH" \
    --release-id "$APP_RELEASE_ID" \
    --release-manifest "$APP_RELEASE_MANIFEST" \
    --expected-git-sha "$APP_GIT_SHA" \
    --expected-principal "$APP_RELEASE_PRINCIPAL" \
    --expected-target "$TARGET" \
    --expected-root-leaf "$APP_RELEASE_ROOT_LEAF"
  run databricks apps deploy "$APP_NAME" --json "@$APP_DEPLOY_PAYLOAD" --timeout 20m
  assert_expected_app_identity "$APP_NAME"
  if [[ -n "${APP_LAST_DEPLOY_PAYLOAD:-}" ]]; then
    rm -f "$APP_LAST_DEPLOY_PAYLOAD"
  fi
  APP_LAST_DEPLOY_PAYLOAD="$APP_DEPLOY_PAYLOAD"
  APP_DEPLOY_PAYLOAD=""

  if [[ "$DRY_RUN" -eq 0 && -z "${MIP_APP_URL:-}" ]]; then
    DEPLOYED_APP_URL="$(databricks apps get "$APP_NAME" -o json | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin).get("url",""))')"
    if [[ -n "$DEPLOYED_APP_URL" ]]; then
      export MIP_APP_URL="$DEPLOYED_APP_URL"
      echo "  app url:    ${MIP_APP_URL}"
    else
      echo "${RED}[deploy] deployed app URL could not be resolved; refusing to run a local smoke while claiming deployed proof.${RST}" >&2
      exit 1
    fi
  fi
}

capture_last_good_app() {
  local binding="${1:-}"
  local -a args
  args=(
    -m tools.databricks.app_deployment_rollback capture
    --app-name "$APP_NAME"
    --scope "$APP_ROLLBACK_SECRET_SCOPE"
    --base-url "${MIP_APP_URL:?App URL is required for exact last-good capture}"
    --token-env MIP_BEARER_TOKEN
    --payload "${APP_LAST_DEPLOY_PAYLOAD:?App deployment payload is required}"
    --app-resource-payload "${APP_RESOURCE_BINDING_PAYLOAD:?Resolved App resources are required}"
    --expected-git-sha "$APP_GIT_SHA"
    --deployment-lease-id "${MIP_APP_DEPLOYMENT_LEASE_ID:?App deployment lease is required}"
    --genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}"
    --treatment-warehouse-id "$_GRANTS_WAREHOUSE_ID"
    --treatment-catalog "$_GRANTS_CATALOG"
  )
  if [[ -n "$binding" ]]; then
    args+=(--expected-gateway-binding "$binding")
  fi
  if [[ -z "$APP_ROLLBACK_BINDING_ENV" ]]; then
    APP_ROLLBACK_BINDING_ENV="$(mktemp -t mip-app-blue-binding.XXXXXX.env)"
  fi
  args+=(--out-env "$APP_ROLLBACK_BINDING_ENV")
  run_with_account_identity \
    run_with_proof_signing_authority "$PYTHON" "${args[@]}"
  set -a
  # shellcheck disable=SC1090
  . "$APP_ROLLBACK_BINDING_ENV"
  set +a
}

# The bundle files root (workspace.file_path) is where the per-release folder
# root is derived from: <bundle root>/app-src is its sibling. The bundle's own
# apps.mip_app.source_code_path (`.`) must still resolve to that root; it is
# never activated. When it names anything else, the line is empty and the
# deploy stops here.
if [[ "$DRY_RUN" -eq 0 ]]; then
  APP_BUNDLE_SUMMARY="$(mktemp -t mip-bundle-summary.XXXXXX.json)"
  databricks bundle summary -t "$TARGET" -o json > "$APP_BUNDLE_SUMMARY"
  APP_DEPLOY_META="$("$PYTHON" -c 'import json,sys; data=json.load(open(sys.argv[1], encoding="utf-8")); ws=data.get("workspace") or {}; files=ws.get("file_path") or ""; app=(data.get("resources") or {}).get("apps", {}).get("mip_app", {}).get("source_code_path") or files; print(files if app == files else ""); print((ws.get("current_user") or {}).get("userName") or "")' "$APP_BUNDLE_SUMMARY")"
else
  APP_DEPLOY_META=$'/Workspace/dry-run/mortgage-intelligence-platform\ndry-run-deployer@example.invalid'
  APP_BUNDLE_SUMMARY="/tmp/mip-dry-run-bundle-summary.json"
fi
APP_BUNDLE_FILES_PATH="$(printf '%s\n' "$APP_DEPLOY_META" | sed -n '1p')"
APP_CURRENT_USER="$(printf '%s\n' "$APP_DEPLOY_META" | sed -n '2p')"
if [[ -z "$APP_BUNDLE_FILES_PATH" ]]; then
  echo "${RED}[deploy] bundle summary did not expose the bundle files root, or apps.mip_app.source_code_path is not it.${RST}" >&2
  exit 1
fi
if [[ -z "$APP_CURRENT_USER" || \
      "$APP_CURRENT_USER" != "$DEPLOY_INVENTORY_PRINCIPAL" ]]; then
  echo "${RED}[deploy] bundle identity does not match the preflighted workspace-admin inventory principal.${RST}" >&2
  exit 1
fi
# >>> app release principal: check >>>
# The client target's root is the profile deployer's folder plus the client
# key (databricks.yml): every App release check names that principal and that
# leaf, and the bundle must run as that deployer.
APP_RELEASE_PRINCIPAL="$CLIENT_DEPLOYER"
APP_RELEASE_ROOT_LEAF="$CLIENT_KEY"
if [[ "$DRY_RUN" -eq 0 ]] && ! same_identity_casefold "$APP_CURRENT_USER" "$APP_RELEASE_PRINCIPAL"; then
  echo "${RED}[deploy] the bundle runs as another principal than the client profile's deployer.${RST}" >&2
  exit 1
fi
# <<< app release principal: check <<<

# >>> app release source: publish >>>
# After the Lakebase migration and reviewed-grant proofs, before the first
# activation: publish the folder step 1 staged to <bundle root>/app-src/<id>
# (tools/databricks/app_release_source.py). Every local check runs before any
# workspace call; then one create-only AUTO import per file (an object that
# exists is reused only with equal bytes), an exact listing and a sha256
# download of every file. It never overwrites or deletes, so a re-run of the
# same release uploads nothing. A failure here restores signed blue on an
# upgrade and leaves a first install stopped and quiesced. APP_SOURCE_PATH is
# assigned only here: all three activations promote this one folder, and
# converge re-checks it against the manifest before each of them.
step "publish App release source ${APP_RELEASE_ID} (create-only, byte-verified)"
run "$PYTHON" -m tools.databricks.app_release_source publish \
  --release-id "$APP_RELEASE_ID" \
  --stage-dir "$APP_RELEASE_STAGE_ROOT/source" \
  --manifest "$APP_RELEASE_MANIFEST" \
  --files-root "$APP_BUNDLE_FILES_PATH" \
  --expected-commit "$SOURCE_GIT_SHA" \
  --expected-principal "$APP_RELEASE_PRINCIPAL" \
  --expected-target "$TARGET" \
  --expected-root-leaf "$APP_RELEASE_ROOT_LEAF"
APP_SOURCE_PATH="${APP_BUNDLE_FILES_PATH%/files}/app-src/${APP_RELEASE_ID}"
# <<< app release source: publish <<<

# Roll-forward env continuity (external audits 2026-07-07 tripped on this
# twice): the first snapshot deploy used to ship WITHOUT the agentic env,
# leaving a window until the post-provisioning redeploy where the live app
# reported ai_gateway/agent_* as not provisioned. The agentic provisioner's
# --out-env is persisted under .databricks/ (gitignored) at the end of each
# run; source it here so a re-deploy never forgets what is already
# provisioned. First-ever deploys have no file and keep the two-phase flow.
AGENTIC_ENV_CACHE=".databricks/mip-agentic.env"
if [[ "$DRY_RUN" -eq 0 && -f "$AGENTIC_ENV_CACHE" && \
      "${MIP_REBASE_UNVERIFIED_APP:-0}" != "1" ]]; then
  echo "[deploy] carrying forward agentic env from $AGENTIC_ENV_CACHE (last provisioning)"
  set -a
  # shellcheck disable=SC1090
  . "$AGENTIC_ENV_CACHE"
  set +a
  restore_deployment_sync_contract "$AGENTIC_ENV_CACHE"
fi
if [[ "$APP_UPGRADE_STATE" == "first_install" ]]; then
  if [[ "$DRY_RUN" -eq 0 ]]; then
    wait_for_app_deployable
  fi
  deploy_app_snapshot "deploy first-install Databricks App snapshot from uploaded bundle source"
else
  step "preserve prior App source and runtime binding until green activation"
fi

# -----------------------------------------------------------------------------
# Step 6: silver refresh (FRED + Cotality share)
# -----------------------------------------------------------------------------
if [[ "$SKIP_SILVER" -eq 1 ]]; then
  step "silver refresh — SKIPPED (--skip-silver)"
else
  step "refresh silver — FRED MORTGAGE30US rates"
  run_job_with_retry databricks bundle run mip_fred_rates_ingest -t "$TARGET"

  step "refresh silver — Cotality share (data-driven geography coverage)"
  run_job_with_retry databricks bundle run mip_refresh_silver -t "$TARGET"
fi

# -----------------------------------------------------------------------------
# (Step 7 removed: Lakebase migration moved to Step 4b, before the app
#  snapshot restart, so the restarted app never races the schema work.)

# -----------------------------------------------------------------------------
# Step 8: gold refresh (CTAS chain, ends with refresh_semantics_views)
# -----------------------------------------------------------------------------
refresh_gold_and_reconcile_function_grants

# Stale-rate guard (audit C1): gold must have scored against the rate silver
# currently publishes. A standalone FRED run after gold silently skews every
# spread 20 bps; this makes the skew a loud deploy failure instead.
step "smoke — gold market rate matches silver is_latest (stale-rate guard)"
run "$PYTHON" -m tools.databricks.verify_market_rate_alignment \
  --warehouse-id "$_GRANTS_WAREHOUSE_ID" \
  --catalog "${MIP_DEFAULT_CATALOG:-mip}"

# -----------------------------------------------------------------------------
# Step 9: lifecycle sync + funnel snapshot (approval / outreach rates)
# -----------------------------------------------------------------------------
step "sync lifecycle state from Lakebase + record daily funnel snapshot"
run "$PYTHON" -m tools.sync_lifecycle_warehouse \
  --catalog "${MIP_DEFAULT_CATALOG:-mip}" \
  --lakebase-instance "$MIP_LAKEBASE_INSTANCE" \
  --lakebase-database "$LAKEBASE_DATABASE"

# -----------------------------------------------------------------------------
# Step 9b: KPI snapshot backfill (S3)
# -----------------------------------------------------------------------------
# Upsert today's headline-KPI snapshot into Lakebase mip_app.kpi_snapshots so
# S4's "since your last login" deltas never see an empty table on a fresh
# install. Requires step 4b (Lakebase schema) + step 8 (gold refresh landed
# semantics.portfolio_headline_metric_view). Idempotent: the job keys on
# snapshot_date, so re-deploying the same day refreshes the day's row.
step "record headline KPI snapshot — mip_app.kpi_snapshots backfill (idempotent per-day)"
run_job_with_retry databricks bundle run mip_kpi_snapshot -t "$TARGET"

# -----------------------------------------------------------------------------
# Step 9c: weekly scheduled refresh postflight (read-only)
# -----------------------------------------------------------------------------
# A validate cannot prove what the Jobs API stored: read the applied
# mip_scheduled_refresh back and fail when its pause status differs from the
# preflight decision, its failure email is not exactly the client profile's
# recipients (MIP_SCHEDULED_REFRESH_RECIPIENTS when the refresh is on, nobody
# when it is off; compared, never printed), its task graph is not FRED -> gold
# -> lifecycle -> KPI plus the outcome gate after gold, or its live
# run_job_task closure is not exactly those four jobs (never the
# reviewed-function publisher). The summary goes in as a here-string (no pipe
# to break); `run` skips it in a dry-run, whose offline summary has no job ids.
step "verify mip_scheduled_refresh — ${BUNDLE_VAR_scheduled_refresh_pause_status:-unset}, chain and failure email"
# An unset pause status reaches the tool as "" and is refused (exit 2).
run "$PYTHON" -m tools.databricks.verify_scheduled_refresh_state \
  --expect "${BUNDLE_VAR_scheduled_refresh_pause_status:-}" \
  --recipients-from-profile <<< "$BUNDLE_SUMMARY_JSON"

# -----------------------------------------------------------------------------
# Step 10: rebind the Genie space after gold/semantic assets exist
# -----------------------------------------------------------------------------
step "rebind Genie space — bind trusted assets from genie/mortgage_lead_intelligence_space.yml"
# The same pin resolution as step 0a: the space step 0a pinned, never a title.
run_with_proof_signing_authority "$PYTHON" -m tools.databricks.provision_genie_space \
  "${GENIE_PIN_ARGS[@]}"
if [[ "$DRY_RUN" -eq 0 ]]; then
  if [[ ! -s genie/space_id.txt ]]; then
    echo "${RED}[deploy] Genie rebind did not write genie/space_id.txt.${RST}" >&2
    exit 2
  fi
  GENIE_SPACE_ID="$(< genie/space_id.txt)"
  if ! is_real_bundle_value "$GENIE_SPACE_ID"; then
    echo "${RED}[deploy] governed Genie rebind returned an invalid id.${RST}" >&2
    exit 2
  fi
  export GENIE_SPACE_ID
fi
