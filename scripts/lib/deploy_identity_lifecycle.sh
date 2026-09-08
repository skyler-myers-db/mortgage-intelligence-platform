# shellcheck shell=bash
# Deployment-control resolution, workspace auth binding, M2M and App token
# minting, and bounded-identity execution wrappers (run_as_m2m_identity,
# run_with_*). Private credentials stay unexported in the parent shell.
# Sourced by scripts/deploy.sh; this file must not change shell options or traps.

is_real_bundle_value() {
  local value="${1:-}"
  [[ -n "$value" ]] || return 1
  [[ "$value" != "00000000PLACEHOLDER" ]] || return 1
  [[ ! ( "$value" == \<* && "$value" == *\> ) ]] || return 1
  return 0
}

dotenv_value() {
  local key="$1"
  "$PYTHON" - "$key" <<'PY'
import sys
from pathlib import Path

key = sys.argv[1]
path = Path(".env.local")
if not path.exists():
    print("")
else:
    try:
        from dotenv import dotenv_values
    except ModuleNotFoundError:
        # Preflight must also work in isolated release-contract fixtures where
        # the repository venv has not been created yet. This intentionally
        # supports only the literal KEY=value forms used for deploy secrets;
        # it never evaluates shell syntax or expands variables.
        value = ""
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            name, separator, candidate = line.partition("=")
            if separator and name.strip() == key:
                candidate = candidate.strip()
                if (
                    len(candidate) >= 2
                    and candidate[0] == candidate[-1]
                    and candidate[0] in {"'", '"'}
                ):
                    candidate = candidate[1:-1]
                value = candidate
        print(value.strip())
    else:
        print((dotenv_values(path).get(key) or "").strip())
PY
}

deployment_control_value() {
  local name="$1" default_value="${2:-}" value
  value="${!name:-}"
  if [[ -z "$value" ]]; then
    value="$(dotenv_value "$name")"
  fi
  printf '%s' "${value:-$default_value}"
}

resolve_m2m_credential() {
  local name="$1" scope="${2:-export}" value
  value="${!name:-}"
  if [[ -z "$value" ]]; then
    value="$(dotenv_value "$name")"
  fi
  printf -v "$name" '%s' "$value"
  if [[ "$scope" == "shell" ]]; then
    # Keep app-facing OAuth credentials available to explicit mint/subshell
    # calls without letting bare deployment-side SDK clients auto-select them.
    export -n "${name?}" 2>/dev/null || true
  elif [[ "$scope" == "export" ]]; then
    export "${name?}"
  else
    echo "${RED}[deploy] invalid credential scope '$scope' for $name.${RST}" >&2
    return 2
  fi
}

bind_deployment_workspace_auth() {
  local bundle_host dotenv_host dotenv_token host token profile profile_host
  dotenv_host="$(dotenv_value DATABRICKS_HOST)"
  dotenv_token="$(dotenv_value DATABRICKS_TOKEN)"
  unset MIP_DEPLOYER_DATABRICKS_HOST MIP_DEPLOYER_DATABRICKS_TOKEN \
    MIP_DEPLOYER_DATABRICKS_PROFILE MIP_DATABRICKS_WORKSPACE_HOST
  if [[ "$DRY_RUN" -eq 1 ]]; then
    host="${dotenv_host:-${DATABRICKS_HOST:-}}"
    if [[ -z "$host" ]]; then
      echo "${RED}[deploy] could not resolve the planned deployment workspace host.${RST}" >&2
      return 2
    fi
    export MIP_DATABRICKS_WORKSPACE_HOST="${host%/}"
    export -n DATABRICKS_CLIENT_ID DATABRICKS_CLIENT_SECRET 2>/dev/null || true
    return 0
  fi
  if [[ -n "${DATABRICKS_CONFIG_PROFILE:-}" ]]; then
    profile="$DATABRICKS_CONFIG_PROFILE"
    profile_host="$($PYTHON - "$profile" <<'PY'
import configparser
import os
import sys
from pathlib import Path

path = Path(os.environ.get("DATABRICKS_CONFIG_FILE") or Path.home() / ".databrickscfg")
parser = configparser.ConfigParser(interpolation=None)
parser.read(path, encoding="utf-8")
profile = sys.argv[1]
print(parser.get(profile, "host", fallback="").strip().rstrip("/"))
PY
)"
    if [[ -z "$profile_host" ]]; then
      echo "${RED}[deploy] Databricks profile '$profile' has no workspace host.${RST}" >&2
      return 2
    fi
    if [[ -n "$dotenv_host" && "${dotenv_host%/}" != "$profile_host" ]]; then
      echo "${RED}[deploy] .env.local workspace host does not match selected Databricks profile '$profile'.${RST}" >&2
      return 2
    fi
    host="$profile_host"
    export DATABRICKS_CONFIG_PROFILE="$profile"
    unset DATABRICKS_HOST DATABRICKS_TOKEN DATABRICKS_AUTH_TYPE
    export MIP_DEPLOYER_DATABRICKS_PROFILE="$profile"
  else
    if [[ -n "$dotenv_token" ]]; then
      if [[ -z "$dotenv_host" ]]; then
        echo "${RED}[deploy] .env.local DATABRICKS_TOKEN requires its own DATABRICKS_HOST.${RST}" >&2
        return 2
      fi
      host="$dotenv_host"
      token="$dotenv_token"
    elif [[ -n "${DATABRICKS_TOKEN:-}" ]]; then
      host="${DATABRICKS_HOST:-}"
      token="$DATABRICKS_TOKEN"
      if [[ -n "$dotenv_host" && "${dotenv_host%/}" != "${host%/}" ]]; then
        echo "${RED}[deploy] refusing to combine an ambient PAT with a different .env.local host.${RST}" >&2
        return 2
      fi
    else
      host="${dotenv_host:-${DATABRICKS_HOST:-}}"
      token=""
    fi
    if [[ -n "$token" ]]; then
      if [[ -z "$host" ]]; then
        echo "${RED}[deploy] DATABRICKS_TOKEN requires DATABRICKS_HOST for deployer auth.${RST}" >&2
        return 2
      fi
      DATABRICKS_HOST="$host"
      DATABRICKS_TOKEN="$token"
      DATABRICKS_AUTH_TYPE="pat"
      export DATABRICKS_HOST DATABRICKS_TOKEN DATABRICKS_AUTH_TYPE
      unset DATABRICKS_CONFIG_PROFILE
      export MIP_DEPLOYER_DATABRICKS_HOST="$host"
      export MIP_DEPLOYER_DATABRICKS_TOKEN="$token"
    else
      profile="DEFAULT"
      profile_host="$($PYTHON - "$profile" <<'PY'
import configparser
import os
import sys
from pathlib import Path

path = Path(os.environ.get("DATABRICKS_CONFIG_FILE") or Path.home() / ".databrickscfg")
parser = configparser.ConfigParser(interpolation=None)
parser.read(path, encoding="utf-8")
profile = sys.argv[1]
print(parser.get(profile, "host", fallback="").strip().rstrip("/"))
PY
)"
      if [[ -z "$profile_host" ]]; then
        echo "${RED}[deploy] DATABRICKS_TOKEN is absent and DEFAULT profile has no workspace host.${RST}" >&2
        return 2
      fi
      if [[ -n "$host" && "${host%/}" != "$profile_host" ]]; then
        echo "${RED}[deploy] .env.local workspace host does not match DEFAULT Databricks profile.${RST}" >&2
        return 2
      fi
      host="$profile_host"
      export DATABRICKS_CONFIG_PROFILE="$profile"
      unset DATABRICKS_HOST DATABRICKS_TOKEN DATABRICKS_AUTH_TYPE
      export MIP_DEPLOYER_DATABRICKS_PROFILE="$profile"
    fi
  fi
  if [[ -z "$host" ]]; then
    echo "${RED}[deploy] could not resolve the deployment workspace host.${RST}" >&2
    return 2
  fi
  bundle_host="$($PYTHON - "$TARGET" <<'PY'
import sys
from pathlib import Path

import yaml

data = yaml.safe_load(Path("databricks.yml").read_text(encoding="utf-8")) or {}
target = sys.argv[1]
targets = data.get("targets") or {}
if target not in targets:
    raise SystemExit(f"unknown Databricks bundle target: {target}")
workspace = targets[target].get("workspace") or {}
top_workspace = data.get("workspace") or {}
print(str(workspace.get("host") or top_workspace.get("host") or "").strip().rstrip("/"))
PY
)"
  if [[ -z "$bundle_host" || "${host%/}" != "$bundle_host" ]]; then
    echo "${RED}[deploy] authenticated workspace host does not match databricks.yml target '$TARGET'.${RST}" >&2
    return 2
  fi
  export MIP_DATABRICKS_WORKSPACE_HOST="${host%/}"
  # These names represent the normal App user, never the UC/App deployment
  # authority. Preserve their shell values but remove them from child envs.
  export -n DATABRICKS_CLIENT_ID DATABRICKS_CLIENT_SECRET 2>/dev/null || true
}

mint_m2m_token() {
  local output_name="$1" client_id_env="$2" client_secret_env="$3"
  local client_id="${!client_id_env}" client_secret="${!client_secret_env}"
  local token_file token
  token_file="$(mktemp -t mip-m2m-token.XXXXXX)"
  chmod 600 "$token_file"
  echo "${DIM}\$ $PYTHON tools/oauth_m2m_mint.py --client-id-env $client_id_env --client-secret-env $client_secret_env --output-file [secure-temp]${RST}"
  # The identity override is intentionally confined to this mint subprocess.
  # shellcheck disable=SC2030
  if ! (
    unset DATABRICKS_TOKEN DATABRICKS_CONFIG_PROFILE \
      MIP_DEPLOYER_DATABRICKS_HOST MIP_DEPLOYER_DATABRICKS_TOKEN \
      MIP_DEPLOYER_DATABRICKS_PROFILE \
      MIP_BEARER_TOKEN MIP_OPERATOR2_BEARER_TOKEN MIP_ADMIN_BEARER_TOKEN \
      DATABRICKS_ADMIN_CLIENT_ID DATABRICKS_ADMIN_CLIENT_SECRET \
      DATABRICKS_OPERATOR2_CLIENT_ID DATABRICKS_OPERATOR2_CLIENT_SECRET \
      DATABRICKS_RELEASE_PROBE_CLIENT_ID DATABRICKS_RELEASE_PROBE_CLIENT_SECRET \
      DATABRICKS_VERIFIER_CLIENT_ID DATABRICKS_VERIFIER_CLIENT_SECRET \
      DATABRICKS_AGENT_RUNTIME_CLIENT_ID DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET \
      DATABRICKS_AGENT_PROXY_CLIENT_ID DATABRICKS_AGENT_PROXY_CLIENT_SECRET \
      DATABRICKS_AGENT_PROXY_CREDENTIAL_ID \
      DATABRICKS_ACCOUNT_CLIENT_ID DATABRICKS_ACCOUNT_CLIENT_SECRET
    export DATABRICKS_HOST="${MIP_DATABRICKS_WORKSPACE_HOST:?}"
    export DATABRICKS_AUTH_TYPE="oauth-m2m"
    export DATABRICKS_CLIENT_ID="$client_id"
    export DATABRICKS_CLIENT_SECRET="$client_secret"
    "$PYTHON" tools/oauth_m2m_mint.py \
      --client-id-env DATABRICKS_CLIENT_ID \
      --client-secret-env DATABRICKS_CLIENT_SECRET \
      --output-file "$token_file"
  ); then
    rm -f "$token_file"
    return 1
  fi
  if ! IFS= read -r token < "$token_file" || [[ -z "$token" ]]; then
    rm -f "$token_file"
    echo "${RED}[deploy] M2M mint returned an empty bearer for $client_id_env.${RST}" >&2
    return 1
  fi
  rm -f "$token_file"
  printf -v "$output_name" '%s' "$token"
  export "${output_name?}"
}

mint_app_automation_tokens() {
  if [[ "$APP_ACCESS_QUARANTINED" -eq 1 ]]; then
    mint_m2m_token MIP_BEARER_TOKEN \
      DATABRICKS_RELEASE_PROBE_CLIENT_ID DATABRICKS_RELEASE_PROBE_CLIENT_SECRET
    MIP_ADMIN_BEARER_TOKEN="$MIP_BEARER_TOKEN"
    export MIP_ADMIN_BEARER_TOKEN
  else
    mint_m2m_token MIP_BEARER_TOKEN DATABRICKS_CLIENT_ID DATABRICKS_CLIENT_SECRET
    mint_m2m_token MIP_ADMIN_BEARER_TOKEN \
      DATABRICKS_ADMIN_CLIENT_ID DATABRICKS_ADMIN_CLIENT_SECRET
  fi
}

run_as_m2m_identity() {
  local label="$1" client_id_env="$2" client_secret_env="$3"
  local client_id="${!client_id_env}" client_secret="${!client_secret_env}"
  local verifier_signing_key="${MIP_AI_GATEWAY_PROOF_SIGNING_KEY:-}"
  local verifier_verify_key="${MIP_AI_GATEWAY_PROOF_VERIFY_KEY:-}"
  local verifier_previous_key="${MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY:-}"
  local verifier_historical_keys="${MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS:-}"
  local model_signing_key="${MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY:-}"
  local model_verify_key="${MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY:-}"
  local model_previous_key="${MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY:-}"
  local allow_runtime_model_signing="${MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING:-0}"
  local cutover_signed_blue_gateway_pin="${MIP_CUTOVER_SIGNED_BLUE_GATEWAY_PIN_JSON:-}"
  local cutover_signed_blue_supervisor_pin="${MIP_CUTOVER_SIGNED_BLUE_SUPERVISOR_PIN_JSON:-}"
  local lakebase_instance="${MIP_LAKEBASE_INSTANCE:-${LAKEBASE_INSTANCE_NAME:-mip-app-state}}"
  local lakebase_database="${LAKEBASE_DATABASE:-${MIP_LAKEBASE_DATABASE_NAME:-mip_app_state}}"
  local allowed_name
  shift 3
  echo "${DIM}\$ $* (${label} M2M identity)${RST}"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    return 0
  fi
  # Build the clean environment with shell builtins so OAuth and signing
  # secrets never appear in `env KEY=value ...` process arguments.
  # shellcheck disable=SC2030,SC2031  # Isolation is intentionally subshell-local.
  (
    local inherited_name
    while IFS= read -r inherited_name; do
      export -n "${inherited_name?}" 2>/dev/null || true
    done < <(compgen -e)
    export HOME="${HOME:-}" PATH="${PATH:-/usr/bin:/bin}"
    export DATABRICKS_HOST="${MIP_DATABRICKS_WORKSPACE_HOST:?}"
    export DATABRICKS_AUTH_TYPE="oauth-m2m"
    export DATABRICKS_CLIENT_ID="$client_id"
    export DATABRICKS_CLIENT_SECRET="$client_secret"
    export MIP_DISABLE_DOTENV=1
    export MIP_LAKEBASE_INSTANCE="$lakebase_instance"
    export LAKEBASE_INSTANCE_NAME="$lakebase_instance"
    export LAKEBASE_DATABASE="$lakebase_database"
    export MIP_LAKEBASE_DATABASE_NAME="$lakebase_database"
    for allowed_name in TMPDIR LANG LC_ALL SSL_CERT_FILE REQUESTS_CA_BUNDLE \
      CURL_CA_BUNDLE HTTPS_PROXY HTTP_PROXY NO_PROXY; do
      if [[ -n "${!allowed_name:-}" ]]; then
        export "${allowed_name}=${!allowed_name}"
      fi
    done
    if [[ "$label" == "verifier" || "$label" == "agent-runtime" ]]; then
      if [[ -n "$verifier_verify_key" ]]; then
        export MIP_AI_GATEWAY_PROOF_VERIFY_KEY="$verifier_verify_key"
      fi
      if [[ -n "$verifier_previous_key" ]]; then
        export MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY="$verifier_previous_key"
      fi
      if [[ -n "$verifier_historical_keys" ]]; then
        export MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS="$verifier_historical_keys"
      fi
    fi
    if [[ "$label" == "verifier" && -n "$verifier_signing_key" ]]; then
      export MIP_AI_GATEWAY_PROOF_SIGNING_KEY="$verifier_signing_key"
    fi
    if [[ "$label" == "agent-runtime" ]]; then
      if [[ -n "$cutover_signed_blue_gateway_pin" ]]; then
        export MIP_CUTOVER_SIGNED_BLUE_GATEWAY_PIN_JSON="$cutover_signed_blue_gateway_pin"
      fi
      if [[ -n "$cutover_signed_blue_supervisor_pin" ]]; then
        export MIP_CUTOVER_SIGNED_BLUE_SUPERVISOR_PIN_JSON="$cutover_signed_blue_supervisor_pin"
      fi
      if [[ -n "$model_verify_key" ]]; then
        export MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY="$model_verify_key"
      fi
      if [[ -n "$model_previous_key" ]]; then
        export MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY="$model_previous_key"
      fi
      if [[ "$allow_runtime_model_signing" == "1" && \
            -n "$model_signing_key" ]]; then
        export MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY="$model_signing_key"
        export MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING=1
      fi
    fi
    "$@"
  )
}

run_with_agent_runtime_credentials() {
  local client_id="${DATABRICKS_AGENT_RUNTIME_CLIENT_ID:-}"
  local client_secret="${DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET:-}"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    run "$@"
    return
  fi
  if [[ -z "$client_id" || -z "$client_secret" ]]; then
    echo "${RED}[deploy] exact agent-runtime credentials are missing for dual-authority audit.${RST}" >&2
    return 2
  fi
  # Preserve deployer auth for the first authority plane. The dedicated values
  # are exported only inside this subshell and never placed in process arguments.
  (
    export DATABRICKS_AGENT_RUNTIME_CLIENT_ID="$client_id"
    export DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET="$client_secret"
    run "$@"
  )
}

run_with_verifier_credentials() {
  local client_id="${DATABRICKS_VERIFIER_CLIENT_ID:-}"
  local client_secret="${DATABRICKS_VERIFIER_CLIENT_SECRET:-}"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    run "$@"
    return
  fi
  if [[ -z "$client_id" || -z "$client_secret" ]]; then
    echo "${RED}[deploy] exact verifier credentials are missing for dual-authority audit.${RST}" >&2
    return 2
  fi
  # Preserve deployer auth only for the bounded admin attestation. The verifier
  # replaces ambient auth before any target-identity probe.
  (
    export DATABRICKS_VERIFIER_CLIENT_ID="$client_id"
    export DATABRICKS_VERIFIER_CLIENT_SECRET="$client_secret"
    run "$@"
  )
}

run_with_agent_proxy_credentials() {
  local client_id="${DATABRICKS_AGENT_PROXY_CLIENT_ID:-}"
  local client_secret="${DATABRICKS_AGENT_PROXY_CLIENT_SECRET:-}"
  local credential_id="${DATABRICKS_AGENT_PROXY_CREDENTIAL_ID:-}"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    run "$@"
    return
  fi
  if [[ -z "$client_id" || -z "$client_secret" || -z "$credential_id" ]]; then
    echo "${RED}[deploy] complete agent-proxy credential binding is missing.${RST}" >&2
    return 2
  fi
  (
    export DATABRICKS_AGENT_PROXY_CLIENT_ID="$client_id"
    export DATABRICKS_AGENT_PROXY_CLIENT_SECRET="$client_secret"
    export DATABRICKS_AGENT_PROXY_CREDENTIAL_ID="$credential_id"
    run "$@"
  )
}

run_with_agent_proxy_binding() {
  local client_id="${DATABRICKS_AGENT_PROXY_CLIENT_ID:-}"
  local credential_id="${DATABRICKS_AGENT_PROXY_CREDENTIAL_ID:-}"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    run "$@"
    return
  fi
  if [[ -z "$client_id" || -z "$credential_id" ]]; then
    echo "${RED}[deploy] agent-proxy identity binding is missing.${RST}" >&2
    return 2
  fi
  (
    export DATABRICKS_AGENT_PROXY_CLIENT_ID="$client_id"
    export DATABRICKS_AGENT_PROXY_CREDENTIAL_ID="$credential_id"
    export -n DATABRICKS_AGENT_PROXY_CLIENT_SECRET 2>/dev/null || true
    run "$@"
  )
}

run_with_account_identity() {
  local account_client_id="${DATABRICKS_ACCOUNT_CLIENT_ID:-}"
  local account_client_secret="${DATABRICKS_ACCOUNT_CLIENT_SECRET:-}"
  echo "${DIM}\$ $* (bounded account-SCIM identity)${RST}"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    return 0
  fi
  if [[ -n "${APP_DEPLOYMENT_LEASE_HEARTBEAT_PID:-}" ]] && \
     ! kill -0 "$APP_DEPLOYMENT_LEASE_HEARTBEAT_PID" 2>/dev/null; then
    echo "${RED}[deploy] signed App deployment lease heartbeat is not running.${RST}" >&2
    return 1
  fi
  if [[ -z "$account_client_id" || -z "$account_client_secret" ]]; then
    echo "${RED}[deploy] bounded account-SCIM credentials are missing.${RST}" >&2
    return 2
  fi
  # shellcheck disable=SC2030  # Account credential export is intentionally subshell-local.
  (
    export DATABRICKS_ACCOUNT_CLIENT_ID="$account_client_id"
    export DATABRICKS_ACCOUNT_CLIENT_SECRET="$account_client_secret"
    "$@"
  )
}

run_with_proof_signing_authority() {
  # shellcheck disable=SC2031  # Parent shell retains the unexported authority.
  local signing_key="${MIP_AI_GATEWAY_PROOF_SIGNING_KEY:-}"
  echo "${DIM}\$ $* (bounded deployer proof-signing authority)${RST}"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    return 0
  fi
  if [[ -z "$signing_key" ]]; then
    echo "${RED}[deploy] bounded proof-signing authority is missing.${RST}" >&2
    return 2
  fi
  # shellcheck disable=SC2030,SC2031  # Proof authority is intentionally subshell-local.
  (
    export MIP_AI_GATEWAY_PROOF_SIGNING_KEY="$signing_key"
    "$@"
  )
}

reconcile_gateway_model_archives() {
  MIP_CUTOVER_SIGNED_BLUE_GATEWAY_PIN_JSON="${MIP_APP_ROLLBACK_GATEWAY_PIN_JSON:-}" \
  MIP_CUTOVER_SIGNED_BLUE_SUPERVISOR_PIN_JSON="${MIP_APP_ROLLBACK_SUPERVISOR_PIN_JSON:-}" \
    run_with_account_identity run_with_proof_signing_authority \
      "$PYTHON" -m tools.databricks.gateway_model_archival_cli \
      archive-unprotected \
      --app-name "$_GRANTS_APP_NAME" \
      --lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
      --source-git-sha "$SOURCE_GIT_SHA" \
      --runtime-application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
      --app-application-id "$APP_SP_CLIENT_ID" \
      --proxy-application-id "$DATABRICKS_AGENT_PROXY_CLIENT_ID" \
      --verifier-application-id "$DATABRICKS_VERIFIER_CLIENT_ID" \
      --archive-owner "$DEPLOY_INVENTORY_PRINCIPAL" \
      --governance-group "${MIP_ADMIN_GROUP_NAME:-mip-admin}" \
      --catalog "${MIP_DEFAULT_CATALOG:-mip}" \
      --model-family "${MIP_AI_GATEWAY_AGENT_MODEL_FAMILY:-${MIP_DEFAULT_CATALOG:-mip}.audit.mortgage_growth_supervisor_proxy}" \
      --experiment-base "${MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE:-mip-agent-runtime-gateway-proxy}" \
      --inference-schema "${MIP_AI_GATEWAY_SCHEMA:-audit}" \
      --inference-table-prefix "${MIP_AI_GATEWAY_TABLE_PREFIX:-mip_agent_gateway_growth_agent}" \
      --rollback-scope "$APP_ROLLBACK_SECRET_SCOPE" \
      --lakebase-instance "$MIP_LAKEBASE_INSTANCE" \
      --warehouse-id "$_GRANTS_WAREHOUSE_ID" \
      --expected-inventory-principal "$DEPLOY_INVENTORY_PRINCIPAL"
}

prove_agent_runtime_dual_uc_boundary() {
  MIP_CUTOVER_SIGNED_BLUE_GATEWAY_PIN_JSON="${MIP_APP_ROLLBACK_GATEWAY_PIN_JSON:-}" \
  MIP_CUTOVER_SIGNED_BLUE_SUPERVISOR_PIN_JSON="${MIP_APP_ROLLBACK_SUPERVISOR_PIN_JSON:-}" \
    run_with_account_identity \
      run_with_proof_signing_authority \
        run_with_agent_runtime_credentials \
        "$PYTHON" -m tools.databricks.verify_agent_runtime_uc_boundary_dual_authority \
      --application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
      --expected-inventory-principal "$DEPLOY_INVENTORY_PRINCIPAL" \
      --supervisor-id "$MIP_AGENT_SUPERVISOR_ID" \
      --supervisor-endpoint-id "$MIP_AGENT_SUPERVISOR_ENDPOINT_ID" \
      --catalog "${MIP_DEFAULT_CATALOG:-mip}" \
      --gateway-model "$MIP_AI_GATEWAY_AGENT_MODEL" \
      --gateway-model-family "${MIP_AI_GATEWAY_AGENT_MODEL_FAMILY:-${MIP_DEFAULT_CATALOG:-mip}.audit.mortgage_growth_supervisor_proxy}" \
      --gateway-experiment-base "${MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE:-mip-agent-runtime-gateway-proxy}" \
      --genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}" \
      --inference-schema "${MIP_AI_GATEWAY_SCHEMA:-audit}" \
      --inference-table-prefix "${MIP_AI_GATEWAY_TABLE_PREFIX:-mip_agent_gateway_growth_agent}" \
      --proxy-caller-application-id "$DATABRICKS_AGENT_PROXY_CLIENT_ID" \
      --proxy-caller-credential-id "$DATABRICKS_AGENT_PROXY_CREDENTIAL_ID" \
      --proxy-caller-secret-reference "$MIP_AGENT_PROXY_SECRET_REFERENCE" \
      --app-name "$_GRANTS_APP_NAME" \
      --deployment-lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
      --deployment-source-git-sha "$SOURCE_GIT_SHA" \
      --app-application-id "$APP_SP_CLIENT_ID" \
      --verifier-application-id "$DATABRICKS_VERIFIER_CLIENT_ID" \
      --archive-owner "$DEPLOY_INVENTORY_PRINCIPAL" \
      --governance-group "${MIP_ADMIN_GROUP_NAME:-mip-admin}" \
      --rollback-scope "$APP_ROLLBACK_SECRET_SCOPE" \
      --lakebase-instance "$MIP_LAKEBASE_INSTANCE" \
      --warehouse-id "$_GRANTS_WAREHOUSE_ID"
}

run_with_lakebase_bootstrap_authority() {
  local control_client_id="${DATABRICKS_AGENT_RUNTIME_CLIENT_ID:-}"
  local control_client_secret="${DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET:-}"
  if [[ "$DRY_RUN" -eq 0 && \
        ( -z "$control_client_id" || -z "$control_client_secret" ) ]]; then
    echo "${RED}[deploy] fresh OAuth-M2M Lakebase bootstrap control credentials are missing.${RST}" >&2
    return 2
  fi
  # Expose the reviewed agent-runtime control identity only to the convergence
  # child. The child creates a fresh OAuth client for every bracketed probe;
  # ordinary deploy commands never inherit this credential under a generic
  # Databricks auth variable.
  (
    export MIP_LAKEBASE_BOOTSTRAP_CONTROL_CLIENT_ID="$control_client_id"
    export MIP_LAKEBASE_BOOTSTRAP_CONTROL_CLIENT_SECRET="$control_client_secret"
    run_with_account_identity run_with_proof_signing_authority "$@"
  )
}

start_proof_signing_heartbeat() {
  # shellcheck disable=SC2031  # Parent shell retains the unexported authority.
  local signing_key="${MIP_AI_GATEWAY_PROOF_SIGNING_KEY:-}"
  echo "${DIM}\$ $* (bounded deployer proof-signing heartbeat)${RST}"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    return 0
  fi
  if [[ -z "$signing_key" ]]; then
    echo "${RED}[deploy] bounded proof-signing authority is missing.${RST}" >&2
    return 2
  fi
  # Launch the external process directly from this shell. Backgrounding the
  # subshell-based foreground wrapper would make that subshell Python's parent
  # and invalidate the heartbeat's deployer-PID fence immediately. The
  # assignment remains scoped to this one child and never enters its argv.
  MIP_AI_GATEWAY_PROOF_SIGNING_KEY="$signing_key" "$@" &
  APP_DEPLOYMENT_LEASE_HEARTBEAT_PID=$!
}
