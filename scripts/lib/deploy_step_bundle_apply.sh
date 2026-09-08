# shellcheck shell=bash
# Deploy step file. Steps 0a-4: governed Genie space resolution, runtime secret bindings, SQL
# render, frontend build, bundle validate/plan/apply without App activation,
# Lakebase OAuth role convergence, and identity isolation audits.
# Sourced by scripts/deploy.sh in reviewed order; it runs in the entrypoint's
# shell with its strict mode, traps, and private variables, never on its own.
# shellcheck disable=SC2034  # top-level state assigned here is read by later step files.
[[ "${BASH_SOURCE[0]}" != "$0" ]] || {
  echo "[deploy] ${BASH_SOURCE[0]} is a step file; run ./scripts/deploy.sh instead." >&2
  exit 2
}

# The app resource binding validates the Genie space during
# `databricks bundle deploy`. A merely well-formed GENIE_SPACE_ID is not proof
# that it names the governed space in this workspace: CI variables and local
# dotenv files can survive a workspace change. Always resolve by the reviewed
# MIP_GENIE_SPACE_NAME, then replace the ambient id with the provisioner's
# authoritative result before any runtime secret or bundle mutation.
step "resolve governed Genie space before App secret and bundle mutation"
run "$PYTHON" -m tools.databricks.provision_genie_space \
  --space-name "$MIP_GENIE_SPACE_NAME" \
  --catalog "$MIP_DEFAULT_CATALOG" \
  --no-smoke-test
if [[ "$DRY_RUN" -eq 0 ]]; then
  if [[ ! -s genie/space_id.txt ]]; then
    echo "${RED}[deploy] Genie provisioner did not write genie/space_id.txt.${RST}" >&2
    exit 2
  fi
  GENIE_SPACE_ID="$(< genie/space_id.txt)"
  if ! is_real_bundle_value "$GENIE_SPACE_ID"; then
    echo "${RED}[deploy] governed Genie space resolution returned an invalid id.${RST}" >&2
    exit 2
  fi
  export GENIE_SPACE_ID
fi

# Provision runtime HMAC values directly into Databricks Secrets only after
# the governed Genie binding has been resolved. The later Apps deploy payload
# carries only value_from resource names, never raw secret values.
RUNTIME_SECRET_SCOPE="${MIP_RUNTIME_SECRET_SCOPE:-mip-runtime}"
export BUNDLE_VAR_runtime_secret_scope="$RUNTIME_SECRET_SCOPE"
step "provision Databricks App runtime secret bindings"
run "$PYTHON" -m tools.databricks.provision_runtime_secrets \
  --scope "$RUNTIME_SECRET_SCOPE"

# -----------------------------------------------------------------------------
# Step 1a: render SQL for the target UC catalog
# -----------------------------------------------------------------------------
# The bundle's SQL tasks read from sql/_rendered/**/*.sql. The canonical
# sources under sql/** hardcode the default `mip.*` catalog prefix for
# readability + code review; tools/render_sql.py substitutes the governed
# catalog/schema DDL and three-part UC prefixes for the target catalog before bundle
# validate/deploy read the rendered tree. The renderer also materializes the
# first-party demo-feed switch as a SQL literal because Databricks SQL does not
# allow parameter markers in this DDL path. The stand-alone renderer defaults to
# disabled; this wrapper explicitly opts the Summit dev demo in while keeping
# prod/customer deploys fail-closed.
DEMO_FEEDS_FROM_ENV="${MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS:-}"
if [[ -z "$DEMO_FEEDS_FROM_ENV" ]]; then
  DEMO_FEEDS_FROM_ENV="$(dotenv_value MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS)"
fi
if [[ -z "$DEMO_FEEDS_FROM_ENV" ]]; then
  if [[ "$TARGET" == "dev" ]]; then
    DEMO_FEEDS_FROM_ENV=1
  else
    DEMO_FEEDS_FROM_ENV=0
  fi
fi
if [[ "$TARGET" != "dev" ]]; then
  DEMO_FEEDS_NORMALIZED="$(printf '%s' "$DEMO_FEEDS_FROM_ENV" | tr '[:upper:]' '[:lower:]')"
  if [[ "$DEMO_FEEDS_NORMALIZED" =~ ^(1|true|yes|y|on)$ && "${MIP_ALLOW_DEMO_FIRST_PARTY_IN_PROD:-0}" != "1" ]]; then
    echo "${RED}[deploy] refusing to enable Summit demo first-party feeds for target ${TARGET}.${RST}" >&2
    echo "  Set MIP_ALLOW_DEMO_FIRST_PARTY_IN_PROD=1 only for an approved demo workspace; never for a customer production workspace." >&2
    exit 2
  fi
fi
export MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS="$DEMO_FEEDS_FROM_ENV"
step "render SQL for target UC catalog (MIP_DEFAULT_CATALOG=${MIP_DEFAULT_CATALOG:-mip}, MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS=${MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS})"
run "$PYTHON" tools/render_sql.py --catalog "${MIP_DEFAULT_CATALOG:-mip}"
if [[ "$MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS" =~ ^(1|true|TRUE|yes|YES|y|Y|on|ON)$ ]]; then
  RESTORE_RENDERED_SQL_FAIL_CLOSED=1
fi

# -----------------------------------------------------------------------------
# Step 1: build the frontend
# -----------------------------------------------------------------------------
step "build frontend (frontend/dist/** is uploaded by the bundle sync.include)"
run npm --prefix frontend run build

# -----------------------------------------------------------------------------
# Step 2: validate bundle
# -----------------------------------------------------------------------------
step "validate direct-deployment bundle against -t ${TARGET}"
run "$PYTHON" -m tools.databricks.bundle_env validate -t "$TARGET"

# -----------------------------------------------------------------------------
# Step 3: plan bundle
# -----------------------------------------------------------------------------
step "plan direct deployment against -t ${TARGET}"
run "$PYTHON" -m tools.databricks.bundle_env plan -t "$TARGET"

# -----------------------------------------------------------------------------
# Step 4: deploy bundle
# -----------------------------------------------------------------------------

verify_exact_deploy_source
# A pipeline resource cannot be created until its target catalog/schema exists,
# while the full catalog DDL is itself uploaded as a bundle job. Break that
# first-install cycle under the signed deployment lease by creating only the
# empty managed namespace needed by the pipeline. Governed tables remain in the
# post-bundle mip_init_catalog_schemas job after the App identity is quiesced.
step "ensure managed UC pipeline namespace exists before bundle apply"
_PIPELINE_NAMESPACE_ARGS=(
  --catalog "${MIP_DEFAULT_CATALOG:-mip}"
  --schema silver
)
# shellcheck disable=SC2031  # Parent-shell M2M ids survive bounded subshells.
for _FORBIDDEN_OWNER in \
  "$DATABRICKS_CLIENT_ID" "$DATABRICKS_OPERATOR2_CLIENT_ID" \
  "$DATABRICKS_ADMIN_CLIENT_ID" "$DATABRICKS_RELEASE_PROBE_CLIENT_ID" \
  "$DATABRICKS_VERIFIER_CLIENT_ID" \
  "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID"; do
  _PIPELINE_NAMESPACE_ARGS+=(--forbidden-owner-principal "$_FORBIDDEN_OWNER")
done
if [[ -n "${_EXISTING_APP_SP_CLIENT_ID:-}" ]]; then
  _PIPELINE_NAMESPACE_ARGS+=(
    --forbidden-owner-principal "$_EXISTING_APP_SP_CLIENT_ID"
  )
fi
run_with_account_identity run_with_proof_signing_authority \
  "$PYTHON" -m tools.databricks.ensure_pipeline_namespace \
  "${_PIPELINE_NAMESPACE_ARGS[@]}"
verify_exact_deploy_source
step "deploy non-App bundle resources without activating an App candidate"
if [[ "$DRY_RUN" -eq 0 ]]; then
  BUNDLE_SUMMARY_JSON="$("$PYTHON" -m tools.databricks.bundle_env summary -t "$TARGET" -o json)"
else
  # Preserve offline dry-run semantics: derive only resource keys from the
  # checked-in bundle and never authenticate to resolve a live summary.
  BUNDLE_SUMMARY_JSON="$("$PYTHON" -c '
import json, sys, yaml

body = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}
print(json.dumps({"resources": body.get("resources") or {}}))
' databricks.yml)"
fi
BUNDLE_NON_APP_SELECTORS="$(printf '%s' "$BUNDLE_SUMMARY_JSON" | "$PYTHON" -c '
import json, sys

body = json.load(sys.stdin)
resources = body.get("resources") or {}
selectors = sorted(
    f"{kind}.{name}"
    for kind, entries in resources.items()
    if kind != "apps" and isinstance(entries, dict)
    for name in entries
)
if not selectors:
    raise SystemExit("bundle summary exposed no non-App resources")
print("\n".join(selectors))
')"
BUNDLE_NON_APP_ARGS=()
while IFS= read -r _bundle_selector; do
  [[ -n "$_bundle_selector" ]] || continue
  BUNDLE_NON_APP_ARGS+=(--select "$_bundle_selector")
done <<< "$BUNDLE_NON_APP_SELECTORS"
run "$PYTHON" -m tools.databricks.bundle_env deploy -t "$TARGET" "${BUNDLE_NON_APP_ARGS[@]}"
if [[ "$DRY_RUN" -eq 0 ]]; then
  # Refresh after apply: on a true first install this is the first summary
  # that can contain concrete ids for every App resource binding.
  BUNDLE_SUMMARY_JSON="$("$PYTHON" -m tools.databricks.bundle_env summary -t "$TARGET" -o json)"
fi

# The App's Lakebase role exists only after its database resource binding is
# applied. A DAB bind records state but does not update the live App, while a
# full App bundle deploy would also activate source_code_path. Resolve the
# now-created non-App resource ids and use the Apps API to apply only resource
# bindings. First installs create the App stopped with those bindings; upgrades
# update the existing App and prove its active/pending source deployment and
# compute state did not change.
if [[ "$DRY_RUN" -eq 0 ]]; then
  APP_RESOURCE_BINDING_SUMMARY="$(mktemp -t mip-app-resource-summary.XXXXXX.json)"
  APP_RESOURCE_BINDING_PAYLOAD="$(mktemp -t mip-app-resource-payload.XXXXXX.json)"
  APP_RESOURCE_BINDING_AFTER="$(mktemp -t mip-app-resource-after.XXXXXX.json)"
  chmod 600 \
    "$APP_RESOURCE_BINDING_SUMMARY" \
    "$APP_RESOURCE_BINDING_PAYLOAD" \
    "$APP_RESOURCE_BINDING_AFTER"
  printf '%s\n' "$BUNDLE_SUMMARY_JSON" > "$APP_RESOURCE_BINDING_SUMMARY"
  "$PYTHON" -m tools.databricks.app_resource_bindings build \
    --bundle-summary "$APP_RESOURCE_BINDING_SUMMARY" \
    --app-name "$_GRANTS_APP_NAME" \
    "${OTEL_RESOURCE_BINDING_ARGS[@]}" \
    --out "$APP_RESOURCE_BINDING_PAYLOAD"
else
  APP_RESOURCE_BINDING_PAYLOAD="/tmp/mip-dry-run-app-resource-bindings.json"
fi
# App resource binding can create or replace its OAuth role. From this point
# until the migration grant postflight succeeds, no signed-blue restoration is
# authorized: a failed LOGIN-only role rotation is not transactionally
# reversible across the Apps API, Lakebase control plane, and PostgreSQL.
LAKEBASE_RUNTIME_ACCESS_PROVEN=0
if [[ -z "${_EXISTING_APP_SP_CLIENT_ID:-}" ]]; then
  if [[ "$DRY_RUN" -eq 0 ]]; then
    FIRST_INSTALL_MARKED_PAYLOAD="$(mktemp -t mip-app-first-install-payload.XXXXXX.json)"
    chmod 600 "$FIRST_INSTALL_MARKED_PAYLOAD"
    step "persist signed first-install ownership before remote App creation"
    run_with_proof_signing_authority \
      "$PYTHON" -m tools.databricks.app_first_install_journal prepare \
      --app-name "$_GRANTS_APP_NAME" \
      --lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
      --source-git-sha "$SOURCE_GIT_SHA" \
      --payload "$APP_RESOURCE_BINDING_PAYLOAD" \
      --out-payload "$FIRST_INSTALL_MARKED_PAYLOAD"
    FIRST_INSTALL_JOURNAL_STATUS="prepared"
  else
    FIRST_INSTALL_MARKED_PAYLOAD="$APP_RESOURCE_BINDING_PAYLOAD"
  fi
  step "create stopped Databricks App with resolved resource bindings and no source deployment"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    APP_CREATE_RESULT="$(mktemp -t mip-app-first-install-create.XXXXXX.json)"
    chmod 600 "$APP_CREATE_RESULT"
  else
    APP_CREATE_RESULT=""
  fi
  if [[ "$DRY_RUN" -eq 0 ]]; then
    # Arm the signed-journal compensation boundary before the remote POST. A
    # nonzero CLI result can still mean that Apps committed creation and only
    # the response was lost. Until the journal binds immutable IDs, the exit
    # trap must refuse every name-based App stop or treatment mutation.
    FIRST_INSTALL_APP_CREATED=1
  fi
  run_json_to_file "$APP_CREATE_RESULT" databricks apps create \
    --json "@$FIRST_INSTALL_MARKED_PAYLOAD" \
    --no-compute \
    -o json
  if [[ "$DRY_RUN" -eq 0 ]]; then
    databricks apps get "$_GRANTS_APP_NAME" -o json > "$APP_RESOURCE_BINDING_AFTER"
    "$PYTHON" -m tools.databricks.app_resource_bindings verify \
      --expected "$FIRST_INSTALL_MARKED_PAYLOAD" \
      --after "$APP_RESOURCE_BINDING_AFTER" \
      --require-stopped-without-deployment
    step "bind signed first-install recovery to the created App service-principal identity"
    run_with_proof_signing_authority \
      "$PYTHON" -m tools.databricks.app_first_install_journal claim \
      --app-name "$_GRANTS_APP_NAME" \
      --lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
      --source-git-sha "$SOURCE_GIT_SHA" \
      --created-app "$APP_CREATE_RESULT"
    FIRST_INSTALL_JOURNAL_STATUS="recover"
  fi
  step "bind the stopped source-free App into bundle deployment state"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    # Arm compensation before the remote command: a nonzero exit can still mean
    # the bind committed and its response was lost.
    FIRST_INSTALL_APP_BOUND=1
  fi
  run "$PYTHON" -m tools.databricks.bundle_env deployment bind \
    mip_app "$_GRANTS_APP_NAME" -t "$TARGET" --auto-approve
else
  step "update existing Databricks App resource bindings without source activation"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    APP_RESOURCE_BINDING_BEFORE="$(mktemp -t mip-app-resource-before.XXXXXX.json)"
    chmod 600 "$APP_RESOURCE_BINDING_BEFORE"
    databricks apps get "$_GRANTS_APP_NAME" -o json > "$APP_RESOURCE_BINDING_BEFORE"
    read -r _BINDING_APP_ID _BINDING_APP_CLIENT_ID _BINDING_APP_SCIM_ID < <(
      "$PYTHON" - "$APP_RESOURCE_BINDING_BEFORE" <<'PYEOF'
import json, sys

app = json.load(open(sys.argv[1], encoding="utf-8"))
print(
    str(app.get("id") or "").strip(),
    str(app.get("service_principal_client_id") or "").strip(),
    str(app.get("service_principal_id") or "").strip(),
)
PYEOF
    )
    if [[ "$_BINDING_APP_ID" != "$_EXISTING_APP_ID" || \
          "$_BINDING_APP_CLIENT_ID" != "$_EXISTING_APP_SP_CLIENT_ID" || \
          "$_BINDING_APP_SCIM_ID" != "$_EXISTING_APP_SP_SCIM_ID" ]]; then
      echo "${RED}[deploy] existing App identity drifted before resource-binding update.${RST}" >&2
      exit 4
    fi
    step "stop and identity-pin existing App before Lakebase binding update"
    run "$PYTHON" -m tools.databricks.stop_app_fail_closed \
      --app-name "$_GRANTS_APP_NAME" \
      --expected-app-id "$_BINDING_APP_ID" \
      --expected-client-id "$_BINDING_APP_CLIENT_ID" \
      --expected-scim-id "$_BINDING_APP_SCIM_ID"
    # The mutation verifier compares resource bindings while independently
    # pinning compute state. Stopping is intentional, so establish the stopped
    # state as the authoritative pre-mutation baseline.
    databricks apps get "$_GRANTS_APP_NAME" -o json > "$APP_RESOURCE_BINDING_BEFORE"
  fi
  assert_expected_app_identity "$_GRANTS_APP_NAME"
  run databricks apps update "$_GRANTS_APP_NAME" \
    --json "@$APP_RESOURCE_BINDING_PAYLOAD"
  assert_expected_app_identity "$_GRANTS_APP_NAME"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    databricks apps get "$_GRANTS_APP_NAME" -o json > "$APP_RESOURCE_BINDING_AFTER"
    "$PYTHON" -m tools.databricks.app_resource_bindings verify \
      --expected "$APP_RESOURCE_BINDING_PAYLOAD" \
      --before "$APP_RESOURCE_BINDING_BEFORE" \
      --after "$APP_RESOURCE_BINDING_AFTER"
  fi
fi

# A true first install has no service principal to quiesce before App identity
# creation. Resolve the newly created (or retained) identity immediately and
# apply the same authoritative identity/metastore/UC boundary before any
# migration, catalog bootstrap, or general data grant can run.
if [[ "$DRY_RUN" -eq 0 ]]; then
  APP_RESOURCE_JSON="$(databricks apps get "$_GRANTS_APP_NAME" -o json 2>/dev/null || true)"
  APP_OBJECT_ID="$(printf '%s' "$APP_RESOURCE_JSON" | "$PYTHON" -c 'import json,sys; print(str(json.load(sys.stdin).get("id") or "").strip())' 2>/dev/null || true)"
  APP_SP_CLIENT_ID="$(printf '%s' "$APP_RESOURCE_JSON" | "$PYTHON" -c 'import json,sys; print((json.load(sys.stdin).get("service_principal_client_id") or "").strip())' 2>/dev/null || true)"
  APP_SP_SCIM_ID="$(printf '%s' "$APP_RESOURCE_JSON" | "$PYTHON" -c 'import json,sys; print(str(json.load(sys.stdin).get("service_principal_id") or "").strip())' 2>/dev/null || true)"
  if [[ -z "$APP_OBJECT_ID" || -z "$APP_SP_CLIENT_ID" || -z "$APP_SP_SCIM_ID" ]]; then
    echo "${RED}[deploy] could not resolve the immutable identity triplet for app '$_GRANTS_APP_NAME' after stopped identity bootstrap.${RST}" >&2
    exit 4
  fi
  if [[ -n "${_EXISTING_APP_ID:-}" ]] && \
     [[ "$APP_OBJECT_ID" != "$_EXISTING_APP_ID" || \
        "$APP_SP_CLIENT_ID" != "$_EXISTING_APP_SP_CLIENT_ID" || \
        "$APP_SP_SCIM_ID" != "$_EXISTING_APP_SP_SCIM_ID" ]]; then
    echo "${RED}[deploy] existing App identity drifted after resource-binding convergence.${RST}" >&2
    exit 4
  fi
  APP_EXPECTED_IDENTITY_ARGS=(
    --expected-app-id "$APP_OBJECT_ID"
    --expected-client-id "$APP_SP_CLIENT_ID"
    --expected-scim-id "$APP_SP_SCIM_ID"
  )
  export MIP_DEPLOYMENT_APP_OBJECT_ID="$APP_OBJECT_ID"
  export MIP_DEPLOYMENT_APP_APPLICATION_ID="$APP_SP_CLIENT_ID"
  export MIP_DEPLOYMENT_APP_SCIM_ID="$APP_SP_SCIM_ID"
  # shellcheck disable=SC2031  # Bounded account subshell does not change parent value.
  if same_identity_casefold \
    "$DATABRICKS_ACCOUNT_CLIENT_ID" "$APP_SP_CLIENT_ID"; then
    echo "${RED}[deploy] account-SCIM OAuth client must be distinct from the target App service principal.${RST}" >&2
    exit 4
  fi
else
  # A true first-install dry run has no App identity yet. Use visibly inert
  # placeholders so the printed plan remains complete without making a live
  # lookup or accidentally substituting an operator credential.
  APP_SP_CLIENT_ID="dry-run-app-client-id"
  APP_SP_SCIM_ID="dry-run-app-scim-id"
fi
# The legacy Database Instances role-create path and the App database binding
# can materialize OAuth roles with PostgreSQL REPLICATION even though their
# API-visible CREATEDB/CREATEROLE/BYPASSRLS attributes are all false.  At this
# stopped/quiesced boundary, replace only that exact unsafe profile through the
# documented databricks_create_role SQL function, which creates LOGIN-only
# roles.  The helper proves zero ownership, memberships, and non-ACL shared
# dependencies before any replacement and verifies the resulting live profile.
step "converge App Lakebase OAuth role to exact LOGIN-only profile"
run_with_lakebase_bootstrap_authority \
  "$PYTHON" -m tools.databricks.converge_lakebase_oauth_role \
  --lakebase-instance "$MIP_LAKEBASE_INSTANCE" \
  --lakebase-database "$LAKEBASE_DATABASE" \
  --application-id "$APP_SP_CLIENT_ID" \
  --role-contract app \
  --app-name "$_GRANTS_APP_NAME" \
  --stop-app-for-mutation \
  --repair-legacy-replication
step "converge verifier Lakebase OAuth role to exact LOGIN-only profile"
run_with_lakebase_bootstrap_authority \
  "$PYTHON" -m tools.databricks.converge_lakebase_oauth_role \
  --lakebase-instance "$MIP_LAKEBASE_INSTANCE" \
  --lakebase-database "$LAKEBASE_DATABASE" \
  --application-id "$DATABRICKS_VERIFIER_CLIENT_ID" \
  --role-contract verifier \
  --repair-legacy-replication
# Credentials-only bootstrap intentionally cannot touch an App that does not
# exist yet. As soon as bundle apply has created/resolved the App, converge the
# three App-facing identities by their reserved role and immutable client ID.
# Secret minting remains a separate pre-App operation; deploy never rotates it.
if [[ "$APP_ACCESS_QUARANTINED" -eq 1 ]]; then
  step "keep normal, second-operator, and admin App access quarantined until signed capture"
else
  step "reconcile normal operator access to the deployed App"
  # shellcheck disable=SC2031  # Parent-shell identity is unchanged by M2M mint subshells.
  run "$PYTHON" -m tools.databricks.provision_m2m_oauth \
    --identity-role normal \
    --expected-application-id "$DATABRICKS_CLIENT_ID" \
    --app-name "$_GRANTS_APP_NAME" \
    --no-mint-secret
  step "reconcile second-operator access to the deployed App"
  run "$PYTHON" -m tools.databricks.provision_m2m_oauth \
    --identity-role operator2 \
    --expected-application-id "$DATABRICKS_OPERATOR2_CLIENT_ID" \
    --app-name "$_GRANTS_APP_NAME" \
    --no-mint-secret
  step "reconcile admin identity and reviewed group access to the deployed App"
  run "$PYTHON" -m tools.databricks.provision_m2m_oauth \
    --identity-role admin \
    --expected-application-id "$DATABRICKS_ADMIN_CLIENT_ID" \
    --app-name "$_GRANTS_APP_NAME" \
    --no-mint-secret
fi
step "audit the dedicated release probe remains isolated before candidate activation"
run "$PYTHON" -m tools.databricks.provision_m2m_oauth \
  --identity-role release_probe \
  --expected-application-id "$DATABRICKS_RELEASE_PROBE_CLIENT_ID" \
  --app-name "$_GRANTS_APP_NAME" \
  --no-grant-can-use \
  --no-mint-secret
# The first migration grants the dedicated verifier's proof-ledger role. On a
# fresh workspace that Lakebase OAuth role does not exist merely because the
# workspace service principal exists, so create/reconcile it before the job's
# grant postflight. Endpoint and warehouse grants stay in the later agentic
# convergence step, after those concrete resources are known.
step "bootstrap dedicated AI Gateway verifier Lakebase OAuth role"
run "$PYTHON" -m tools.databricks.provision_m2m_oauth \
  --identity-role verifier \
  --expected-application-id "$DATABRICKS_VERIFIER_CLIENT_ID" \
  --lakebase-instance "$MIP_LAKEBASE_INSTANCE" \
  --no-mint-secret
step "re-audit dedicated agent-runtime isolation before resource ownership"
run "$PYTHON" -m tools.databricks.provision_m2m_oauth \
  --identity-role agent_runtime \
  --expected-application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
  --no-mint-secret
step "re-audit dedicated Supervisor proxy-caller identity isolation"
run "$PYTHON" -m tools.databricks.provision_m2m_oauth \
  --identity-role agent_proxy \
  --expected-application-id "$DATABRICKS_AGENT_PROXY_CLIENT_ID" \
  --no-mint-secret
