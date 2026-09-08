# shellcheck shell=bash
# Deploy step file. Step 0a: prove signed-blue state before any workspace mutation.
# Sourced by scripts/deploy.sh in reviewed order; it runs in the entrypoint's
# shell with its strict mode, traps, and private variables, never on its own.
# shellcheck disable=SC2034  # top-level state assigned here is read by later step files.
[[ "${BASH_SOURCE[0]}" != "$0" ]] || {
  echo "[deploy] ${BASH_SOURCE[0]} is a step file; run ./scripts/deploy.sh instead." >&2
  exit 2
}

_GRANTS_WAREHOUSE_ID="${DATABRICKS_WAREHOUSE_ID:-$(dotenv_value DATABRICKS_WAREHOUSE_ID)}"
_GRANTS_CATALOG="${MIP_DEFAULT_CATALOG:-mip}"
if [[ -z "$_GRANTS_WAREHOUSE_ID" ]]; then
  echo "${RED}[deploy] DATABRICKS_WAREHOUSE_ID missing (env or .env.local) — cannot govern treatment access.${RST}" >&2
  exit 4
fi
# The exact target was discovered at the immediate post-lease recovery boundary
# before any failure-prone build or bundle work.
if [[ "$DRY_RUN" -eq 0 ]]; then
  if [[ -n "$_EXISTING_APP_SP_CLIENT_ID" ]]; then
    APP_UPGRADE_STATE="unverified_existing"
  fi
  # The signed journal and any journal-only client ID were authenticated and
  # recovered immediately after lease acquisition, before grant cleanup.
  if [[ "$FIRST_INSTALL_JOURNAL_STATUS" == "orphan_unclaimed" ]]; then
    step "clear signed first-install intent whose App creation never committed"
    run_with_proof_signing_authority \
      "$PYTHON" -m tools.databricks.app_first_install_journal clear-absent \
      --app-name "$_GRANTS_APP_NAME" \
      --lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
      --source-git-sha "$SOURCE_GIT_SHA" \
      --warehouse-id "$_GRANTS_WAREHOUSE_ID"
    FIRST_INSTALL_JOURNAL_STATUS="absent"
  elif [[ "$FIRST_INSTALL_JOURNAL_STATUS" == "orphan_claimed" ]]; then
    step "retire claimed first-install journal after authenticated App deletion"
    run_with_proof_signing_authority \
      "$PYTHON" -m tools.databricks.app_first_install_journal delete \
      --app-name "$_GRANTS_APP_NAME" \
      --lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
      --source-git-sha "$SOURCE_GIT_SHA" \
      --rollback-scope "$APP_ROLLBACK_SECRET_SCOPE" \
      --lakebase-instance "$MIP_LAKEBASE_INSTANCE"
    FIRST_INSTALL_JOURNAL_STATUS="absent"
  elif [[ "$FIRST_INSTALL_JOURNAL_STATUS" == "recover" ]]; then
    if [[ -z "$_EXISTING_APP_SP_CLIENT_ID" ]]; then
      echo "${RED}[deploy] signed first-install journal names an App absent from inventory.${RST}" >&2
      exit 4
    fi
    recover_interrupted_first_install_app
  elif [[ "$FIRST_INSTALL_JOURNAL_STATUS" == "unclaimed" ]]; then
    APP_FAIL_CLOSED_NAME="$_GRANTS_APP_NAME"
    step "bind interrupted first-install App to its authoritative create audit event"
    run_with_proof_signing_authority \
      "$PYTHON" -m tools.databricks.app_first_install_journal recover-claim \
      --app-name "$_GRANTS_APP_NAME" \
      --lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
      --source-git-sha "$SOURCE_GIT_SHA" \
      --warehouse-id "$_GRANTS_WAREHOUSE_ID"
    refresh_first_install_journal_status
    if [[ "$FIRST_INSTALL_JOURNAL_STATUS" != "recover" ]]; then
      echo "${RED}[deploy] audited first-install recovery did not converge to recoverable state.${RST}" >&2
      exit 4
    fi
    recover_interrupted_first_install_app
  elif [[ "$FIRST_INSTALL_JOURNAL_STATUS" != "absent" && \
          "$FIRST_INSTALL_JOURNAL_STATUS" != "signed" ]]; then
    echo "${RED}[deploy] unrecognized first-install journal state.${RST}" >&2
    exit 4
  fi
  if [[ -n "$_EXISTING_APP_SP_CLIENT_ID" ]]; then
    APP_UPGRADE_STATE="unverified_existing"
    _EXISTING_APP_URL="$(printf '%s' "$_EXISTING_APPS_JSON" | "$PYTHON" -c '
import json, os, sys
items = json.load(sys.stdin)
name = os.environ.get("MIP_APP_NAME", "mip-app")
matches = [item for item in items if str(item.get("name") or "") == name]
print(str(matches[0].get("url") or "").strip() if len(matches) == 1 else "")
')"
    if [[ -z "${MIP_APP_URL:-}" && -n "$_EXISTING_APP_URL" ]]; then
      export MIP_APP_URL="$_EXISTING_APP_URL"
    fi
    APP_FAIL_CLOSED_NAME="$_GRANTS_APP_NAME"
    if [[ "${MIP_REBASE_UNVERIFIED_APP:-0}" == "1" && \
          "$FIRST_INSTALL_JOURNAL_STATUS" != "signed" ]]; then
      step "prove the unsigned legacy App has no existing last-good rollback record"
      run "$PYTHON" -m tools.databricks.app_rollback_bootstrap_gate \
        --app-name "$_GRANTS_APP_NAME" \
        --scope "$APP_ROLLBACK_SECRET_SCOPE"
      APP_FAIL_CLOSED_ARMED=1
      step "explicitly stop an unverified legacy App before fail-closed rebase"
      run "$PYTHON" -m tools.databricks.stop_app_fail_closed \
        --app-name "$_GRANTS_APP_NAME" \
        "${APP_EXPECTED_IDENTITY_ARGS[@]}"
      step "quarantine all non-manager App access for the unsigned rebase"
      # shellcheck disable=SC2031  # Parent-shell normal client ID is unchanged by mint subshells.
      run "$PYTHON" -m tools.databricks.converge_app_release_access \
        --mode quarantine \
        --app-name "$_GRANTS_APP_NAME" \
        --release-probe-application-id "$DATABRICKS_RELEASE_PROBE_CLIENT_ID" \
        --normal-application-id "$DATABRICKS_CLIENT_ID" \
        --operator2-application-id "$DATABRICKS_OPERATOR2_CLIENT_ID" \
        --admin-application-id "$DATABRICKS_ADMIN_CLIENT_ID" \
        "${APP_EXPECTED_IDENTITY_ARGS[@]}"
      APP_ACCESS_QUARANTINED=1
      step "quiesce the stopped legacy App treatment grant before fail-closed rebase"
      run_with_account_identity run_with_proof_signing_authority \
        "$PYTHON" -m tools.databricks.converge_campaign_treatment_access \
        --warehouse-id "$_GRANTS_WAREHOUSE_ID" \
        --catalog "$_GRANTS_CATALOG" \
        --principal "$_EXISTING_APP_SP_CLIENT_ID" \
        --mode quiesce
      TREATMENT_RUNTIME_QUIESCED=1
      APP_UPGRADE_STATE="first_install"
    else
      APP_FAIL_CLOSED_ARMED=1
      step "quiesce treatment authority before signed-blue App reconciliation"
      run_with_account_identity run_with_proof_signing_authority \
        "$PYTHON" -m tools.databricks.converge_campaign_treatment_access \
        --warehouse-id "$_GRANTS_WAREHOUSE_ID" \
        --catalog "$_GRANTS_CATALOG" \
        --principal "$_EXISTING_APP_SP_CLIENT_ID" \
        --mode quiesce
      TREATMENT_RUNTIME_QUIESCED=1
      mint_m2m_token MIP_BEARER_TOKEN DATABRICKS_CLIENT_ID DATABRICKS_CLIENT_SECRET
      step "prove or reconcile the signed last-good App before non-App mutations"
      APP_ROLLBACK_BINDING_ENV="$(mktemp -t mip-app-blue-binding.XXXXXX.env)"
      run_with_account_identity \
        run_with_proof_signing_authority \
          "$PYTHON" -m tools.databricks.app_deployment_rollback ensure \
        --app-name "$_GRANTS_APP_NAME" \
        --scope "$APP_ROLLBACK_SECRET_SCOPE" \
        --base-url "${MIP_APP_URL:?existing App URL is required}" \
        --token-env MIP_BEARER_TOKEN \
        --deployment-lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
        --deployment-source-git-sha "$SOURCE_GIT_SHA" \
        --treatment-warehouse-id "$_GRANTS_WAREHOUSE_ID" \
        --treatment-catalog "$_GRANTS_CATALOG" \
        --out-env "$APP_ROLLBACK_BINDING_ENV"
      set -a
      # shellcheck disable=SC1090
      . "$APP_ROLLBACK_BINDING_ENV"
      set +a
      APP_SIGNED_BLUE_AVAILABLE=1
      TREATMENT_RUNTIME_QUIESCED=1
      APP_UPGRADE_STATE="blue_quiesced"
      if [[ "$FIRST_INSTALL_JOURNAL_STATUS" == "signed" ]]; then
        step "retire completed first-install journal after signed-blue verification"
        run_with_proof_signing_authority \
          "$PYTHON" -m tools.databricks.app_first_install_journal complete \
          --app-name "$_GRANTS_APP_NAME" \
          --lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
          --source-git-sha "$SOURCE_GIT_SHA" \
          --rollback-scope "$APP_ROLLBACK_SECRET_SCOPE" \
          --lakebase-instance "$MIP_LAKEBASE_INSTANCE"
        FIRST_INSTALL_JOURNAL_STATUS="absent"
      fi
    fi
    step "keep existing App treatment writes quiesced through non-App release work"
  else
    step "prove absent or converge governed treatment table before first App creation"
    run "$PYTHON" -m tools.databricks.ensure_campaign_treatment_table \
      --warehouse-id "$_GRANTS_WAREHOUSE_ID" \
      --catalog "$_GRANTS_CATALOG" \
      --allow-absent
  fi
  if [[ "${MIP_REMEDIATE_FOREIGN_CATALOG_BINDINGS:-0}" == "1" ]]; then
    if [[ "$FOREIGN_CATALOG_REMEDIATION_COMPLETE" -eq 0 ]]; then
      step "stop the exact admitted App before foreign-catalog remediation"
      run "$PYTHON" -m tools.databricks.stop_app_fail_closed \
        --app-name "$_GRANTS_APP_NAME" \
        "${APP_EXPECTED_IDENTITY_ARGS[@]}"
      run_foreign_catalog_binding_remediation
      step "preflight remediated agent-runtime foreign UC access while App is stopped"
      run_with_account_identity run_with_proof_signing_authority \
        "$PYTHON" -m tools.databricks.audit_agent_runtime_foreign_uc_access \
        --application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
        --catalog "${MIP_DEFAULT_CATALOG:-mip}" \
        --expected-inventory-principal "$DEPLOY_INVENTORY_PRINCIPAL"
      FOREIGN_CATALOG_REMEDIATION_COMPLETE=1
      if [[ "$APP_SIGNED_BLUE_AVAILABLE" -eq 1 ]]; then
        step "restart and re-prove only the exact signed-blue App after UC preflight"
        run_with_account_identity \
          run_with_proof_signing_authority \
            "$PYTHON" -m tools.databricks.app_deployment_rollback ensure \
          --app-name "$_GRANTS_APP_NAME" \
          --scope "$APP_ROLLBACK_SECRET_SCOPE" \
          --base-url "${MIP_APP_URL:?existing App URL is required}" \
          --token-env MIP_BEARER_TOKEN \
          --deployment-lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
          --deployment-source-git-sha "$SOURCE_GIT_SHA" \
          --treatment-warehouse-id "$_GRANTS_WAREHOUSE_ID" \
          --treatment-catalog "$_GRANTS_CATALOG" \
          --out-env "$APP_ROLLBACK_BINDING_ENV"
        TREATMENT_RUNTIME_QUIESCED=1
        APP_UPGRADE_STATE="blue_quiesced"
      fi
    fi
    step "recover interrupted verifier Lakebase role bootstrap after UC preflight"
    run_with_lakebase_bootstrap_authority \
      "$PYTHON" -m tools.databricks.converge_lakebase_oauth_role \
      --lakebase-instance "$MIP_LAKEBASE_INSTANCE" \
      --lakebase-database "$LAKEBASE_DATABASE" \
      --application-id "$DATABRICKS_VERIFIER_CLIENT_ID" \
      --role-contract verifier \
      --recover-bootstrap-only
    step "recover interrupted App Lakebase role bootstrap after UC preflight"
    run_with_lakebase_bootstrap_authority \
      "$PYTHON" -m tools.databricks.converge_lakebase_oauth_role \
      --lakebase-instance "$MIP_LAKEBASE_INSTANCE" \
      --lakebase-database "$LAKEBASE_DATABASE" \
      --application-id "$_EXISTING_APP_SP_CLIENT_ID" \
      --role-contract app \
      --recover-bootstrap-only
  fi
else
  echo "[deploy] dry-run: existing app treatment writes remain live until treatment DDL"
fi
APP_FAIL_CLOSED_NAME="$_GRANTS_APP_NAME"
APP_FAIL_CLOSED_ARMED=1
