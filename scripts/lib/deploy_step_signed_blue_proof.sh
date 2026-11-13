# shellcheck shell=bash
# Deploy step file. Step 0a: prove signed-blue state before any workspace mutation.
# Sourced by scripts/deploy.sh in reviewed order; it runs in the entrypoint's
# shell with its strict mode, traps, and private variables, never on its own.
# shellcheck disable=SC2034  # top-level state assigned here is read by later step files.
[[ "${BASH_SOURCE[0]}" != "$0" ]] || {
  echo "[deploy] ${BASH_SOURCE[0]} is a step file; run ./scripts/deploy.sh instead." >&2
  exit 2
}

# _GRANTS_WAREHOUSE_ID: the warehouse the preflight resolved from this
# deployer's bundle state (or a first install's bootstrap warehouse; M1-PR3).
_GRANTS_CATALOG="${MIP_DEFAULT_CATALOG:-mip}"
if [[ -z "${_GRANTS_WAREHOUSE_ID:-}" ]]; then
  echo "${RED}[deploy] no SQL warehouse was resolved — cannot govern treatment access.${RST}" >&2
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
    # >>> first install: resume after the owner bootstrap >>>
    # A --phase release that stopped for the owner bootstrap (exit 3, M1-PR6)
    # resumes its kept App; any other interrupted first install is deleted.
    if first_install_awaits_owner_bootstrap; then
      resume_first_install_after_owner_bootstrap
    else
      recover_interrupted_first_install_app
    fi
    # <<< first install: resume after the owner bootstrap <<<
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
  # A resumed first install (M1-PR6) takes the first-install branch below.
  if [[ -n "$_EXISTING_APP_SP_CLIENT_ID" && "${FIRST_INSTALL_RESUMED:-0}" -ne 1 ]]; then
    APP_UPGRADE_STATE="unverified_existing"
    # >>> app url: the existing App's, masked at resolve >>>
    _EXISTING_APP_URL="$(printf '%s' "$_EXISTING_APPS_JSON" | "$PYTHON" -c '
import json, os, sys
items = json.load(sys.stdin)
name = os.environ.get("MIP_APP_NAME", "mip-app")
matches = [item for item in items if str(item.get("name") or "") == name]
print(str(matches[0].get("url") or "").strip() if len(matches) == 1 else "")
')"
    # A routine release's first sight of the URL: a runner mask before any
    # --base-url echo or the closing summary prints it (mask_app_url).
    if ! mask_app_url "$_EXISTING_APP_URL"; then
      echo "${RED}[deploy] the existing App's URL could not be registered as a runner mask; refusing to print it or hand it to a command.${RST}" >&2
      exit 4
    fi
    if [[ -z "${MIP_APP_URL:-}" && -n "$_EXISTING_APP_URL" ]]; then
      export MIP_APP_URL="$_EXISTING_APP_URL"
    fi
    # <<< app url: the existing App's, masked at resolve <<<
    APP_FAIL_CLOSED_NAME="$_GRANTS_APP_NAME"
    if [[ "${MIP_REBASE_UNVERIFIED_APP:-0}" == "1" && \
          "$FIRST_INSTALL_JOURNAL_STATUS" != "signed" ]]; then
      # The pre-stop probe proved this absence before the lease
      # (prestop_rollback_record). A record is written only under the lease,
      # so the same proof runs again here, under it, to close the window
      # between that probe and the lease (time of check, time of use).
      step "re-prove under the lease that the unsigned legacy App has no last-good rollback record"
      run "$PYTHON" -m tools.databricks.app_rollback_bootstrap_gate \
        --app-name "$_GRANTS_APP_NAME" \
        --scope "$APP_ROLLBACK_SECRET_SCOPE"
      APP_FAIL_CLOSED_ARMED=1
      step "explicitly stop an unverified legacy App before fail-closed rebase"
      run stop_unsigned_rebase_app
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
      # A dead-man restart from an earlier window is re-pinned first (M0-PR9),
      # before the fail-closed arm and the treatment quiesce: a refusal exits
      # with the App exactly as the dead-man left it, never stopped.
      mint_m2m_token MIP_BEARER_TOKEN DATABRICKS_CLIENT_ID DATABRICKS_CLIENT_SECRET
      recover_deadman_quiesce_window
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
      if tier_is_agentic; then
        prove_or_restore_signed_blue "$APP_ROLLBACK_BINDING_ENV"
      else
        # The core tier stands on a v7 core record and its core resource proof (M2-PR7b).
        prove_or_restore_core_blue "$APP_ROLLBACK_BINDING_ENV"
      fi
      set -a
      # shellcheck disable=SC1090
      . "$APP_ROLLBACK_BINDING_ENV"
      set +a
      mask_deployment_ids
      APP_SIGNED_BLUE_AVAILABLE=1
      TREATMENT_RUNTIME_QUIESCED=1
      APP_UPGRADE_STATE="blue_quiesced"
      # Proven signed blue serves from here on (M0-PR8): the release stops it
      # only inside the planned quiesce window.
      QUIESCE_WINDOW_STATE="blue_serving"
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
else
  echo "[deploy] dry-run: existing app treatment writes remain live until treatment DDL"
fi
APP_FAIL_CLOSED_NAME="$_GRANTS_APP_NAME"
APP_FAIL_CLOSED_ARMED=1
