# shellcheck shell=bash
# Deploy step file. Step 0 (continued): separated automation credentials, tooling-dependency
# guard, SQL render and bundle validation, the pre-lease bundle plan gate (where
# --phase plan exits), signed workspace lease, OAuth and Lakebase bootstrap
# recovery, first-install journal read, and the owned rollback secret scope.
# Sourced by scripts/deploy.sh in reviewed order; it runs in the entrypoint's
# shell with its strict mode, traps, and private variables, never on its own.
# shellcheck disable=SC2034  # top-level state assigned here is read by later step files.
[[ "${BASH_SOURCE[0]}" != "$0" ]] || {
  echo "[deploy] ${BASH_SOURCE[0]} is a step file; run ./scripts/deploy.sh instead." >&2
  exit 2
}

# App-facing, release-probe, and agent-runtime automation use separated
# long-lived client credentials but no stored bearer tokens. Normal/admin
# tokens are minted per run; a quarantined rebase uses only the dedicated
# release-probe token until signed capture. The verifier client is used only
# for deployment-side Gateway proof writes and is not a member of mip-admin.
for _M2M_NAME in \
  DATABRICKS_CLIENT_ID DATABRICKS_CLIENT_SECRET \
  DATABRICKS_ADMIN_CLIENT_ID DATABRICKS_ADMIN_CLIENT_SECRET \
  DATABRICKS_RELEASE_PROBE_CLIENT_ID DATABRICKS_RELEASE_PROBE_CLIENT_SECRET \
  DATABRICKS_VERIFIER_CLIENT_ID DATABRICKS_VERIFIER_CLIENT_SECRET \
  DATABRICKS_AGENT_RUNTIME_CLIENT_ID DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET \
  DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE; do
  resolve_m2m_credential "$_M2M_NAME" shell
done
if [[ "$DRY_RUN" -eq 1 ]]; then
  # Dry-run fixtures may provide public planning values without usable secret
  # material. Live deployment deliberately ignores these legacy sources.
  for _M2M_NAME in \
    DATABRICKS_AGENT_PROXY_CLIENT_ID DATABRICKS_AGENT_PROXY_CREDENTIAL_ID; do
    resolve_m2m_credential "$_M2M_NAME" shell
  done
else
  DATABRICKS_AGENT_PROXY_CLIENT_ID=""
  DATABRICKS_AGENT_PROXY_CREDENTIAL_ID=""
  DATABRICKS_AGENT_PROXY_CLIENT_SECRET=""
fi
if [[ "$DRY_RUN" -eq 0 && -n "$DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE" ]]; then
  if ! _AGENT_PROXY_BUNDLE_FIELDS="$(
    DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE="$DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE" \
      "$PYTHON" -m tools.databricks.agent_proxy_credential_bundle all-fields
  )"; then
    echo "${RED}[deploy] ERROR: agent-proxy credential bundle is invalid.${RST}" >&2
    exit 1
  fi
  IFS=$'\t' read -r DATABRICKS_AGENT_PROXY_CLIENT_ID \
    DATABRICKS_AGENT_PROXY_CREDENTIAL_ID DATABRICKS_AGENT_PROXY_CLIENT_SECRET \
    <<< "$_AGENT_PROXY_BUNDLE_FIELDS"
  unset _AGENT_PROXY_BUNDLE_FIELDS
  export -n DATABRICKS_AGENT_PROXY_CLIENT_SECRET \
    DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE 2>/dev/null || true
fi
_GRANTS_APP_NAME="${MIP_APP_NAME:-mip-app}"
_OAUTH_RECOVERY_VALUES=(
  "${MIP_OAUTH_CREDENTIAL_RECOVERY_INTENT_PATH:-}"
  "${MIP_OAUTH_CREDENTIAL_RECOVERY_PRINCIPAL_ID:-}"
  "${MIP_OAUTH_CREDENTIAL_RECOVERY_AUTHORITY_IDENTITY:-}"
  "${MIP_OAUTH_CREDENTIAL_RECOVERY_PROVIDER_API:-}"
)
_OAUTH_ORPHAN_RECOVERY_VALUES=(
  "${MIP_OAUTH_CREDENTIAL_ORPHAN_LEASE_ID:-}"
  "${MIP_OAUTH_CREDENTIAL_ORPHAN_RECOVERY_ROOT_LEASE_ID:-}"
)
_OAUTH_RECOVERY_VALUE_COUNT=0
for _OAUTH_RECOVERY_VALUE in "${_OAUTH_RECOVERY_VALUES[@]}"; do
  if [[ -n "$_OAUTH_RECOVERY_VALUE" ]]; then
    _OAUTH_RECOVERY_VALUE_COUNT=$((_OAUTH_RECOVERY_VALUE_COUNT + 1))
  fi
done
_OAUTH_ORPHAN_RECOVERY_VALUE_COUNT=0
for _OAUTH_ORPHAN_RECOVERY_VALUE in "${_OAUTH_ORPHAN_RECOVERY_VALUES[@]}"; do
  if [[ -n "$_OAUTH_ORPHAN_RECOVERY_VALUE" ]]; then
    _OAUTH_ORPHAN_RECOVERY_VALUE_COUNT=$((_OAUTH_ORPHAN_RECOVERY_VALUE_COUNT + 1))
  fi
done
if [[ "$_OAUTH_RECOVERY_VALUE_COUNT" -ne 0 && \
      "$_OAUTH_RECOVERY_VALUE_COUNT" -ne 4 ]]; then
  echo "${RED}[deploy] OAuth credential recovery requires intent path, principal id, authority identity, and provider API together.${RST}" >&2
  exit 4
fi
if [[ "$_OAUTH_ORPHAN_RECOVERY_VALUE_COUNT" -ne 0 && \
      "$_OAUTH_ORPHAN_RECOVERY_VALUE_COUNT" -ne 2 ]]; then
  echo "${RED}[deploy] OAuth credential orphan-lease recovery requires lease id and recovery-root lease id together.${RST}" >&2
  exit 4
fi
if [[ "$_OAUTH_RECOVERY_VALUE_COUNT" -ne 0 && \
      "$_OAUTH_ORPHAN_RECOVERY_VALUE_COUNT" -ne 0 ]]; then
  echo "${RED}[deploy] OAuth credential intent recovery and orphan-lease recovery are mutually exclusive.${RST}" >&2
  exit 4
fi
# Every remaining phase — dry-run included — executes repo Python helpers with
# "$PYTHON" (the App deploy payload emitter at minimum). Without .venv the
# interpreter silently resolved to a bare python3, which would die dozens of
# steps later on its first third-party import with a raw traceback. Fail here,
# after the config gates above so their exact errors keep precedence.
if ! "$PYTHON" -c 'import dotenv, pydantic' >/dev/null 2>&1; then
  echo "${RED}[deploy] ERROR: ${PYTHON} cannot import the deploy tooling baseline (python-dotenv + pydantic).${RST}" >&2
  echo "  create the project venv first: make setup — then re-run." >&2
  exit 2
fi

# -----------------------------------------------------------------------------
# Step 1a: render SQL for the target UC catalog (before the lease)
# -----------------------------------------------------------------------------
# Rendered and validated before the signed lease and every App stop, so a render
# or validation refusal stops the deploy while the App still serves (bundle_env
# renders the same tree again for each bundle call it wraps).
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
# Step 2: validate bundle (before the lease)
# -----------------------------------------------------------------------------
step "validate direct-deployment bundle against -t ${TARGET}"
run "$PYTHON" -m tools.databricks.bundle_env validate -t "$TARGET"

if [[ "$DRY_RUN" -eq 0 ]]; then
  _M2M_MISSING=""
  for _M2M_NAME in \
    DATABRICKS_CLIENT_ID DATABRICKS_CLIENT_SECRET \
    DATABRICKS_ADMIN_CLIENT_ID DATABRICKS_ADMIN_CLIENT_SECRET \
    DATABRICKS_RELEASE_PROBE_CLIENT_ID DATABRICKS_RELEASE_PROBE_CLIENT_SECRET \
    DATABRICKS_VERIFIER_CLIENT_ID DATABRICKS_VERIFIER_CLIENT_SECRET \
    DATABRICKS_AGENT_RUNTIME_CLIENT_ID DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET \
    DATABRICKS_AGENT_PROXY_CLIENT_ID DATABRICKS_AGENT_PROXY_CLIENT_SECRET \
    DATABRICKS_AGENT_PROXY_CREDENTIAL_ID DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE \
    DATABRICKS_ACCOUNT_HOST DATABRICKS_ACCOUNT_ID \
    DATABRICKS_ACCOUNT_CLIENT_ID DATABRICKS_ACCOUNT_CLIENT_SECRET; do
    if [[ -z "${!_M2M_NAME:-}" ]]; then
      _M2M_MISSING="${_M2M_MISSING} ${_M2M_NAME}"
    fi
  done
  if [[ -n "$_M2M_MISSING" ]]; then
    echo "${RED}[deploy] ERROR: missing required per-run M2M credential(s):${_M2M_MISSING}.${RST}" >&2
    exit 1
  fi
  # shellcheck disable=SC2031  # Bounded account subshell does not change parent value.
  if [[ -n "$DATABRICKS_ACCOUNT_CLIENT_ID" ]]; then
    for _SEPARATED_CLIENT_ENV in \
      DATABRICKS_CLIENT_ID DATABRICKS_OPERATOR2_CLIENT_ID \
      DATABRICKS_ADMIN_CLIENT_ID DATABRICKS_RELEASE_PROBE_CLIENT_ID \
      DATABRICKS_VERIFIER_CLIENT_ID \
      DATABRICKS_AGENT_RUNTIME_CLIENT_ID \
      DATABRICKS_AGENT_PROXY_CLIENT_ID; do
      if [[ -n "${!_SEPARATED_CLIENT_ENV:-}" ]] && \
         same_identity_casefold \
           "$DATABRICKS_ACCOUNT_CLIENT_ID" "${!_SEPARATED_CLIENT_ENV}"; then
        echo "${RED}[deploy] ERROR: account-SCIM OAuth client must be distinct from ${_SEPARATED_CLIENT_ENV}.${RST}" >&2
        exit 1
      fi
    done
  fi
  # run_as_m2m_identity changes DATABRICKS_CLIENT_ID only in a subshell.
  # shellcheck disable=SC2031
  if ! "$PYTHON" - \
    "$DATABRICKS_CLIENT_ID" "$DATABRICKS_OPERATOR2_CLIENT_ID" \
    "$DATABRICKS_ADMIN_CLIENT_ID" "$DATABRICKS_RELEASE_PROBE_CLIENT_ID" \
    "$DATABRICKS_VERIFIER_CLIENT_ID" \
    "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
    "$DATABRICKS_AGENT_PROXY_CLIENT_ID" \
    "$DATABRICKS_ACCOUNT_CLIENT_ID" <<'PYEOF'
import sys

values = [value.strip().casefold() for value in sys.argv[1:-1]]
account_client_id = sys.argv[-1].strip().casefold()
raise SystemExit(
    0
    if (
        all(values)
        and len(values) == len(set(values))
        and account_client_id
        and account_client_id not in values
    )
    else 1
)
PYEOF
  then
    echo "${RED}[deploy] ERROR: normal, operator2, admin, release-probe, verifier, agent-runtime, and agent-proxy M2M client IDs must be pairwise distinct, and account-SCIM must be distinct from all of them.${RST}" >&2
    exit 1
  fi
  _CONFIGURED_ADMIN_IDENTITIES="$(deployment_control_value MIP_ADMIN_IDENTITIES)"
  MIP_ADMIN_IDENTITIES="$("$PYTHON" - \
    "$_CONFIGURED_ADMIN_IDENTITIES" \
    "$DATABRICKS_ADMIN_CLIENT_ID" \
    "$DATABRICKS_RELEASE_PROBE_CLIENT_ID" <<'PYEOF'
import sys

configured, admin_id, release_probe_id = sys.argv[1:]
values = []
for candidate in (*configured.split(","), admin_id, release_probe_id):
    value = candidate.strip()
    if value and value not in values:
        values.append(value)
print(",".join(values))
PYEOF
)"
  export MIP_ADMIN_IDENTITIES
  export MIP_AI_GATEWAY_VERIFIER_CLIENT_ID="$DATABRICKS_VERIFIER_CLIENT_ID"
  export BUNDLE_VAR_ai_gateway_verifier_client_id="$DATABRICKS_VERIFIER_CLIENT_ID"
  # Read-only pre-stop probes. Step 0a stops (and on the unsigned rebase path
  # quarantines) the existing App, and with no signed blue a later failure
  # leaves it stopped. These failures are visible to the workspace admin now,
  # before the lease or any other mutation: retiring a canonical Supervisor
  # the agent-runtime SP did not create without the operator naming it in
  # MIP_RETIRE_HUMAN_SUPERVISOR (or with an incomplete identity), a
  # non-runtime Supervisor on a reserved replacement name, a Genie space
  # granting a group the App, agent-runtime or verifier SP belongs to, any
  # Genie grant to the verifier, a signed rollback record that is absent,
  # tampered or drifted (or, for the unsigned rebase, present), a governed
  # owner outside the approved-owner policy, an unapproved owner-equivalent
  # grantee, App serving access beyond the record-pinned endpoints, and a
  # Genie turn that fails (one real turn). Each reuses the assertion of the
  # step it protects; any refusal exits 2 here, before this run stops the App.
  # The owner policy resolves owners in account SCIM, so the probes get the
  # bounded account credential, never the proof-signing key.
  _PRESTOP_REBASE_UNVERIFIED_APP=0
  if [[ "${MIP_REBASE_UNVERIFIED_APP:-0}" == "1" ]]; then
    _PRESTOP_REBASE_UNVERIFIED_APP=1
  fi
  step "pre-stop probes: Supervisor, Genie grants, rollback record, owners, serving and Genie budget"
  run_with_account_identity \
    "$PYTHON" -m tools.databricks.deploy_prestop_probes \
    --app-name "$_GRANTS_APP_NAME" \
    --expected-inventory-principal "$DEPLOY_INVENTORY_PRINCIPAL" \
    --runtime-application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
    --verifier-application-id "$DATABRICKS_VERIFIER_CLIENT_ID" \
    --supervisor-name "${MIP_AGENT_SUPERVISOR_NAME:-Mortgage Growth Agent}" \
    --rollback-scope "$APP_ROLLBACK_SECRET_SCOPE" \
    --lakebase-instance "$MIP_LAKEBASE_INSTANCE" \
    --catalog "${MIP_DEFAULT_CATALOG:-mip}" \
    --genie-space-name "$MIP_GENIE_SPACE_NAME" \
    --rebase-unverified-app "$_PRESTOP_REBASE_UNVERIFIED_APP" \
    --approved-owner-principals "${MIP_UC_APPROVED_OWNER_PRINCIPALS:-}" \
    --genie-manager-principals "${MIP_GENIE_MANAGER_PRINCIPALS:-}"
  # >>> pii-salt pre-stop check >>>
  # Step 4d writes the write-once pii-salt only after an authoritative "listed,
  # key absent". Run the same inventory read-only here, before the lease and
  # the App stop: a scope or key listing the deployer cannot complete refuses
  # now, while the App still serves. The tool names scopes and keys only.
  if ! "$PYTHON" -m tools.databricks.pii_salt_scope check \
      --scope "$MIP_PII_SALT_SCOPE" \
      --require-existing "$PII_SALT_REQUIRE_EXISTING"; then
    echo "${RED}[deploy] the pii-salt check was refused before the lease and the App stop (see above).${RST}" >&2
    exit 2
  fi
  # <<< pii-salt pre-stop check <<<
else
  export MIP_APP_DEPLOYMENT_LEASE_ID="dry-run-deployment-lease"
  echo "  app automation: normal/admin Bearer mint deferred by --dry-run"
  step "pre-stop probes: Supervisor, Genie grants, rollback record, owners, serving and Genie budget"
  echo "  would probe read-only: the creator of Supervisor '${MIP_AGENT_SUPERVISOR_NAME:-Mortgage Growth Agent}' (MIP_RETIRE_HUMAN_SUPERVISOR), Genie group grants, the signed rollback record in ${APP_ROLLBACK_SECRET_SCOPE}, governed owners and owner-equivalent grantees, App serving access, and one Genie turn"
  echo "  would check read-only: scope ${MIP_PII_SALT_SCOPE} and write-once pii-salt-v1"
fi
# >>> bundle plan gate: pre-lease >>>
# Read-only: plan the bundle and refuse a delete, recreate, identity rename,
# unknown kind, name collision, unadopted install or unacknowledged Lakebase
# instance update here, while the App still serves, before the signed lease and
# every App stop (tools/databricks/bundle_plan_gate.py). --phase plan ends here,
# with the lease never taken.
run_bundle_plan_gate pre-lease
if [[ "${DEPLOY_PHASE:-}" == "plan" ]]; then
  echo "${GRN}[deploy] plan phase complete: read-only, stopped before the lease; nothing was changed.${RST}"
  exit 0
fi
# <<< bundle plan gate: pre-lease <<<
if [[ "$DRY_RUN" -eq 0 ]]; then
  # The signed lease is the first persistent workspace mutation. A contender
  # must lose here before it can revoke stale grants or alter shared resources.
  APP_LEASE_RECOVERY_ENV="$(mktemp -t mip-app-lease-recovery.XXXXXX.env)"
  chmod 600 "$APP_LEASE_RECOVERY_ENV"
  run_with_proof_signing_authority \
    "$PYTHON" -m tools.databricks.app_deployment_lease recovery-root \
    --app-name "$_GRANTS_APP_NAME" \
    --out-env "$APP_LEASE_RECOVERY_ENV"
  set -a
  # shellcheck disable=SC1090
  . "$APP_LEASE_RECOVERY_ENV"
  set +a
  _APP_EXPIRED_LEASE_RECOVERY_ARGS=()
  if [[ -n "${MIP_APP_DEPLOYMENT_RECOVERY_ROOT:-}" ]]; then
    _APP_EXPIRED_LEASE_RECOVERY_ARGS=(
      --expired-recovery-lease-id "$MIP_APP_DEPLOYMENT_RECOVERY_ROOT"
    )
  fi
  APP_DEPLOYMENT_LEASE_ENV="$(mktemp -t mip-app-deployment-lease.XXXXXX.env)"
  step "acquire signed workspace lease for the exact App deployment"
  run_with_proof_signing_authority \
    "$PYTHON" -m tools.databricks.app_deployment_lease acquire \
    --app-name "$_GRANTS_APP_NAME" \
    --source-git-sha "$SOURCE_GIT_SHA" \
    --writer-application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
    "${_APP_EXPIRED_LEASE_RECOVERY_ARGS[@]}" \
    --out-env "$APP_DEPLOYMENT_LEASE_ENV"
  set -a
  # shellcheck disable=SC1090
  . "$APP_DEPLOYMENT_LEASE_ENV"
  set +a
  APP_DEPLOYMENT_LEASE_ID="${MIP_APP_DEPLOYMENT_LEASE_ID:?lease acquisition returned no id}"
  export MIP_APP_DEPLOYMENT_LEASE_ID
  OAUTH_CREDENTIAL_QUARANTINE_FILE="$(
    mktemp -t mip-oauth-credential-quarantine.XXXXXX
  )"
  chmod 600 "$OAUTH_CREDENTIAL_QUARANTINE_FILE"
  export MIP_OAUTH_CREDENTIAL_QUARANTINE_FILE="$OAUTH_CREDENTIAL_QUARANTINE_FILE"
  start_proof_signing_heartbeat \
    "$PYTHON" -m tools.databricks.app_deployment_lease heartbeat \
    --app-name "$_GRANTS_APP_NAME" \
    --source-git-sha "$SOURCE_GIT_SHA" \
    --lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
    --parent-pid "$$"
  if [[ -n "${MIP_OAUTH_CREDENTIAL_ORPHAN_LEASE_ID:-}" ]]; then
    step "recover the explicitly confirmed orphan OAuth credential lease"
    run_with_proof_signing_authority \
      "$PYTHON" -m tools.databricks.oauth_credential_recovery_cli \
      recover-orphan-lease \
      --confirm-lease-id "$MIP_OAUTH_CREDENTIAL_ORPHAN_LEASE_ID" \
      --confirm-recovery-root-lease-id \
        "$MIP_OAUTH_CREDENTIAL_ORPHAN_RECOVERY_ROOT_LEASE_ID"
  fi
  if [[ -n "${MIP_OAUTH_CREDENTIAL_RECOVERY_INTENT_PATH:-}" ]]; then
    step "recover the explicitly confirmed interrupted OAuth credential intent"
    run_with_account_identity run_with_proof_signing_authority \
      "$PYTHON" -m tools.databricks.oauth_credential_recovery_cli recover \
      --intent-path "$MIP_OAUTH_CREDENTIAL_RECOVERY_INTENT_PATH" \
      --confirm-principal-id "$MIP_OAUTH_CREDENTIAL_RECOVERY_PRINCIPAL_ID" \
      --confirm-authority-identity \
        "$MIP_OAUTH_CREDENTIAL_RECOVERY_AUTHORITY_IDENTITY" \
      --confirm-provider-api "$MIP_OAUTH_CREDENTIAL_RECOVERY_PROVIDER_API"
  fi
  step "prove or create the deterministic owned App rollback secret scope"
  run "$PYTHON" -m tools.databricks.app_rollback_secret_scope ensure \
    --app-name "$_GRANTS_APP_NAME" \
    --scope "$APP_ROLLBACK_SECRET_SCOPE"
  # Inventory and separate the immutable target before the account credential
  # can resolve owners, recover roles, or authorize any App-addressed action.
  _EXISTING_APPS_JSON="$(databricks apps list -o json)"
  _EXISTING_APP_IDENTITY="$(printf '%s' "$_EXISTING_APPS_JSON" | "$PYTHON" -c '
import json, os, sys
items = json.load(sys.stdin)
name = os.environ.get("MIP_APP_NAME", "mip-app")
matches = [item for item in items if str(item.get("name") or "") == name]
if len(matches) > 1:
    raise SystemExit(f"multiple Databricks Apps named {name!r}")
if not matches:
    print("absent")
else:
    values = tuple(str(matches[0].get(field) or "").strip() for field in (
        "id", "service_principal_client_id", "service_principal_id"
    ))
    if not all(values) or any(any(char.isspace() for char in value) for value in values):
        raise SystemExit(f"existing Databricks App {name!r} has incomplete identity")
    print("\t".join(values))
')"
  _EXISTING_APP_ID=""
  _EXISTING_APP_SP_CLIENT_ID=""
  _EXISTING_APP_SP_SCIM_ID=""
  if [[ "$_EXISTING_APP_IDENTITY" != "absent" ]]; then
    IFS=$'\t' read -r \
      _EXISTING_APP_ID _EXISTING_APP_SP_CLIENT_ID _EXISTING_APP_SP_SCIM_ID \
      <<< "$_EXISTING_APP_IDENTITY"
    if [[ -z "$_EXISTING_APP_ID" || -z "$_EXISTING_APP_SP_CLIENT_ID" || \
          -z "$_EXISTING_APP_SP_SCIM_ID" ]]; then
      echo "${RED}[deploy] existing App inventory returned an incomplete immutable identity.${RST}" >&2
      exit 4
    fi
    APP_EXPECTED_IDENTITY_ARGS=(
      --expected-app-id "$_EXISTING_APP_ID"
      --expected-client-id "$_EXISTING_APP_SP_CLIENT_ID"
      --expected-scim-id "$_EXISTING_APP_SP_SCIM_ID"
    )
    # OAuth application IDs are compared case-insensitively throughout the
    # identity policy.
    # shellcheck disable=SC2031  # Bounded account subshell does not change parent value.
    if same_identity_casefold \
      "$DATABRICKS_ACCOUNT_CLIENT_ID" "$_EXISTING_APP_SP_CLIENT_ID"; then
      echo "${RED}[deploy] account-SCIM OAuth client must be distinct from the existing target App service principal.${RST}" >&2
      exit 4
    fi
  fi
  step "preflight agent-runtime foreign UC access before Lakebase bootstrap mutation"
  run_with_account_identity run_with_proof_signing_authority \
    "$PYTHON" -m tools.databricks.audit_agent_runtime_foreign_uc_access \
    --application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
    --catalog "${MIP_DEFAULT_CATALOG:-mip}" \
    --expected-inventory-principal "$DEPLOY_INVENTORY_PRINCIPAL" \
    --allow-missing-mip-catalog
  # Recover any deterministic one-use Lakebase role creators left by a prior
  # SIGKILL immediately after the lease winner is known.
  step "recover interrupted verifier Lakebase role bootstrap"
  run_with_lakebase_bootstrap_authority \
    "$PYTHON" -m tools.databricks.converge_lakebase_oauth_role \
    --lakebase-instance "$MIP_LAKEBASE_INSTANCE" \
    --lakebase-database "$LAKEBASE_DATABASE" \
    --application-id "$DATABRICKS_VERIFIER_CLIENT_ID" \
    --role-contract verifier \
    --recover-bootstrap-only
  if [[ -n "$_EXISTING_APP_SP_CLIENT_ID" ]]; then
    step "recover interrupted App Lakebase role bootstrap"
    run_with_lakebase_bootstrap_authority \
      "$PYTHON" -m tools.databricks.converge_lakebase_oauth_role \
      --lakebase-instance "$MIP_LAKEBASE_INSTANCE" \
      --lakebase-database "$LAKEBASE_DATABASE" \
      --application-id "$_EXISTING_APP_SP_CLIENT_ID" \
      --role-contract app \
      --recover-bootstrap-only
  fi
  APP_FAIL_CLOSED_NAME="$_GRANTS_APP_NAME"
  step "read signed first-install journal at the immediate recovery boundary"
  if ! refresh_first_install_journal_status; then
    echo "${RED}[deploy] could not authenticate first-install recovery journal.${RST}" >&2
    exit 1
  fi
  if [[ -n "$FIRST_INSTALL_APP_CLIENT_ID" && \
        "$FIRST_INSTALL_APP_CLIENT_ID" != "$_EXISTING_APP_SP_CLIENT_ID" ]]; then
    step "recover interrupted Lakebase bootstrap for journaled App identity"
    recover_journaled_first_install_lakebase_bootstrap
  fi
  # Reconcile any CREATE privileges left by a prior SIGKILL at the same early
  # recovery boundary.
  _GRANTS_WAREHOUSE_ID="${DATABRICKS_WAREHOUSE_ID:-$(dotenv_value DATABRICKS_WAREHOUSE_ID)}"
  _GRANTS_CATALOG="${MIP_DEFAULT_CATALOG:-mip}"
  if [[ -z "$_GRANTS_WAREHOUSE_ID" ]]; then
    echo "${RED}[deploy] DATABRICKS_WAREHOUSE_ID is required for early privilege reconciliation.${RST}" >&2
    exit 1
  fi
  AGENT_RUNTIME_BOOTSTRAP_GRANTS_ACTIVE=1
  if ! revoke_agent_runtime_bootstrap_grants; then
    echo "${RED}[deploy] could not clear prior agent-runtime bootstrap privileges.${RST}" >&2
    exit 1
  fi
  mint_m2m_token MIP_BEARER_TOKEN DATABRICKS_CLIENT_ID DATABRICKS_CLIENT_SECRET
  mint_m2m_token MIP_ADMIN_BEARER_TOKEN \
    DATABRICKS_ADMIN_CLIENT_ID DATABRICKS_ADMIN_CLIENT_SECRET
  echo "  app automation: distinct per-run normal/admin Bearers minted"
else
  step "prove or create the deterministic owned App rollback secret scope"
  echo "  would prove/create: scope ${APP_ROLLBACK_SECRET_SCOPE}"
fi
