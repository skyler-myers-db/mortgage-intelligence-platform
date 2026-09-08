# shellcheck shell=bash
# Deploy step file. Step 0: preflight and configuration gates (.env.local, CLI, deployment
# controls, admin allowlist, confirmation and ID-mask secrets, signing keys).
# Sourced by scripts/deploy.sh in reviewed order; it runs in the entrypoint's
# shell with its strict mode, traps, and private variables, never on its own.
# shellcheck disable=SC2034  # top-level state assigned here is read by later step files.
[[ "${BASH_SOURCE[0]}" != "$0" ]] || {
  echo "[deploy] ${BASH_SOURCE[0]} is a step file; run ./scripts/deploy.sh instead." >&2
  exit 2
}

step "preflight — check .env.local, databricks CLI, venv"

if [[ ! -f .env.local ]]; then
  echo "${RED}[deploy] .env.local missing.${RST}" >&2
  echo "  copy .env.example to .env.local, then fill in DATABRICKS_HOST + DATABRICKS_WAREHOUSE_ID." >&2
  exit 2
fi

bind_deployment_workspace_auth

if ! command -v databricks >/dev/null 2>&1; then
  echo "${RED}[deploy] \`databricks\` CLI is not on PATH.${RST}" >&2
  echo "  install: https://docs.databricks.com/en/dev-tools/cli/install.html" >&2
  exit 2
fi

DB_VERSION="$(databricks --version 2>&1 || echo 'unknown')"
echo "  databricks: ${DB_VERSION}"
echo "  python:     ${PYTHON}"
echo "  target:     ${TARGET}"
echo "  dry-run:    ${DRY_RUN}"

if [[ "$DRY_RUN" -eq 0 ]]; then
  # Unity Catalog model registration validates the uploaded artifact through
  # the metastore's cloud storage before the version becomes ready. Fail before
  # the first workspace lookup/mutation when the runtime lacks that AWS
  # artifact client. Dry-runs intentionally validate config without requiring
  # the deployment-only Python dependency set.
  if ! "$PYTHON" -c 'import boto3, mlflow' >/dev/null 2>&1; then
    echo "${RED}[deploy] MLflow Unity Catalog artifact dependencies are unavailable (mlflow + boto3 required).${RST}" >&2
    exit 2
  fi
  DEPLOY_INVENTORY_PRINCIPAL="$("$PYTHON" - <<'PY'
from databricks.sdk import WorkspaceClient

from tools.databricks.audit_global_m2m_access import (
    workspace_admin_inventory_principal,
)

print(workspace_admin_inventory_principal(WorkspaceClient()))
PY
)"
  if [[ -z "$DEPLOY_INVENTORY_PRINCIPAL" ]]; then
    echo "${RED}[deploy] workspace-admin inventory preflight returned no principal.${RST}" >&2
    exit 2
  fi
  echo "  inventory:  ${DEPLOY_INVENTORY_PRINCIPAL} (workspace admin)"
else
  DEPLOY_INVENTORY_PRINCIPAL="dry-run-deployer@example.invalid"
fi

if [[ "$DRY_RUN" -eq 0 && "$NO_CONFIRM" -eq 0 ]]; then
  read -r -p "About to DEPLOY to the ${TARGET} target. Continue? [y/N] " ans
  if [[ "$ans" != "y" && "$ans" != "Y" ]]; then
    echo "aborted."
    exit 1
  fi
fi

# Resolve deployment-scoped controls before any workspace mutation. A reviewed
# shell export wins; otherwise the documented .env.local value wins; defaults
# preserve the established Entrada installation. Alias drift still fails.
MIP_DEFAULT_CATALOG="$(deployment_control_value MIP_DEFAULT_CATALOG mip)"
MIP_UC_FOREIGN_CATALOG_BINDING_POLICY="$(
  deployment_control_value MIP_UC_FOREIGN_CATALOG_BINDING_POLICY
)"
MIP_APP_NAME="$(deployment_control_value MIP_APP_NAME mip-app)"
MIP_LENDER_NAME="$(deployment_control_value MIP_LENDER_NAME 'Summit Mortgage')"
_MIP_LENDER_NMLS_ID="$(deployment_control_value MIP_LENDER_NMLS_ID)"
_MIP_TENANT_ID="$(deployment_control_value MIP_TENANT_ID)"
if [[ ! -f backend/schemas/lender_identity.py ]]; then
  # Isolated shell-contract tests copy only deploy.sh. The only safe fallback
  # without the source-controlled registry is the exact built-in demo pair.
  if [[ "$MIP_LENDER_NAME" != "Summit Mortgage" || \
        ( -n "$_MIP_LENDER_NMLS_ID" && "$_MIP_LENDER_NMLS_ID" != "123456" ) || \
        ( -n "$_MIP_TENANT_ID" && "$_MIP_TENANT_ID" != "summit" ) ]]; then
    echo "${RED}[deploy] source-controlled lender identity registry is unavailable.${RST}" >&2
    exit 2
  fi
  _MIP_LENDER_IDENTITY=$'Summit Mortgage\t123456\tsummit'
elif ! _MIP_LENDER_IDENTITY="$(
    "$PYTHON" -c '
import sys
from backend.schemas.lender_identity import effective_public_tenant_id, validate_public_lender_identity
lender, nmls = validate_public_lender_identity(sys.argv[1], sys.argv[2])
tenant = effective_public_tenant_id(sys.argv[3], lender_name=lender)
print("\t".join((lender, nmls, tenant)))
' "$MIP_LENDER_NAME" "$_MIP_LENDER_NMLS_ID" "$_MIP_TENANT_ID"
)"; then
  echo "${RED}[deploy] lender name, NMLS id, or tenant disclosure namespace is invalid.${RST}" >&2
  exit 2
fi
IFS=$'\t' read -r MIP_LENDER_NAME MIP_LENDER_NMLS_ID MIP_TENANT_ID <<< "$_MIP_LENDER_IDENTITY"
if [[ -z "$MIP_LENDER_NAME" || -z "$MIP_LENDER_NMLS_ID" || -z "$MIP_TENANT_ID" ]]; then
  echo "${RED}[deploy] lender disclosure identity did not resolve completely.${RST}" >&2
  exit 2
fi
_LAKEBASE_INSTANCE_NAME="$(deployment_control_value LAKEBASE_INSTANCE_NAME)"
_MIP_LAKEBASE_INSTANCE="$(deployment_control_value MIP_LAKEBASE_INSTANCE)"
if [[ -n "$_LAKEBASE_INSTANCE_NAME" && -n "$_MIP_LAKEBASE_INSTANCE" && \
      "$_LAKEBASE_INSTANCE_NAME" != "$_MIP_LAKEBASE_INSTANCE" ]]; then
  echo "${RED}[deploy] LAKEBASE_INSTANCE_NAME and MIP_LAKEBASE_INSTANCE must match.${RST}" >&2
  exit 2
fi
MIP_LAKEBASE_INSTANCE="${_MIP_LAKEBASE_INSTANCE:-${_LAKEBASE_INSTANCE_NAME:-mip-app-state}}"
LAKEBASE_INSTANCE_NAME="$MIP_LAKEBASE_INSTANCE"
_LAKEBASE_DATABASE="$(deployment_control_value LAKEBASE_DATABASE)"
_MIP_LAKEBASE_DATABASE_NAME="$(deployment_control_value MIP_LAKEBASE_DATABASE_NAME)"
if [[ -n "$_LAKEBASE_DATABASE" && -n "$_MIP_LAKEBASE_DATABASE_NAME" && \
      "$_LAKEBASE_DATABASE" != "$_MIP_LAKEBASE_DATABASE_NAME" ]]; then
  echo "${RED}[deploy] LAKEBASE_DATABASE and MIP_LAKEBASE_DATABASE_NAME must match.${RST}" >&2
  exit 2
fi
LAKEBASE_DATABASE="${_LAKEBASE_DATABASE:-${_MIP_LAKEBASE_DATABASE_NAME:-mip_app_state}}"
MIP_LAKEBASE_DATABASE_NAME="$LAKEBASE_DATABASE"
MIP_LAKEBASE_SYNC_CATALOG="$(deployment_control_value MIP_LAKEBASE_SYNC_CATALOG mip_app_state)"
MIP_LAKEBASE_SYNC_SCHEMA="$(deployment_control_value MIP_LAKEBASE_SYNC_SCHEMA mip_sync)"
MIP_LAKEBASE_SYNC_TABLES="$(deployment_control_value MIP_LAKEBASE_SYNC_TABLES 'source_readiness,segment_population,funnel_snapshot_daily')"
DEPLOYMENT_SYNC_CATALOG="$MIP_LAKEBASE_SYNC_CATALOG"
DEPLOYMENT_SYNC_SCHEMA="$MIP_LAKEBASE_SYNC_SCHEMA"
DEPLOYMENT_SYNC_TABLES="$MIP_LAKEBASE_SYNC_TABLES"
MIP_GENIE_SPACE_NAME="$(deployment_control_value MIP_GENIE_SPACE_NAME 'Mortgage Lead Intelligence')"
MIP_RUNTIME_SECRET_SCOPE="$(deployment_control_value MIP_RUNTIME_SECRET_SCOPE mip-runtime)"
MIP_APP_ROLLBACK_SECRET_SCOPE="$(deployment_control_value MIP_APP_ROLLBACK_SECRET_SCOPE mip-app-rollback)"
MIP_AGENT_PROXY_SECRET_SCOPE="$(
  deployment_control_value MIP_AGENT_PROXY_SECRET_SCOPE "${MIP_APP_NAME}-agent-proxy"
)"
MIP_OTEL_ENDPOINT="$(deployment_control_value MIP_OTEL_ENDPOINT)"
MIP_OTEL_HEADERS_SECRET_SCOPE="$(deployment_control_value MIP_OTEL_HEADERS_SECRET_SCOPE)"
MIP_OTEL_HEADERS_SECRET_KEY="$(deployment_control_value MIP_OTEL_HEADERS_SECRET_KEY)"
_MIP_OTEL_HEADERS_PLAINTEXT="$(deployment_control_value MIP_OTEL_HEADERS)"
if [[ -n "$_MIP_OTEL_HEADERS_PLAINTEXT" ]]; then
  unset _MIP_OTEL_HEADERS_PLAINTEXT
  echo "${RED}[deploy] MIP_OTEL_HEADERS must never be provided as plaintext deploy config; store it in Databricks Secrets.${RST}" >&2
  exit 2
fi
unset _MIP_OTEL_HEADERS_PLAINTEXT
if [[ -n "$MIP_OTEL_ENDPOINT" || -n "$MIP_OTEL_HEADERS_SECRET_SCOPE" || \
      -n "$MIP_OTEL_HEADERS_SECRET_KEY" ]]; then
  if [[ -z "$MIP_OTEL_ENDPOINT" || -z "$MIP_OTEL_HEADERS_SECRET_SCOPE" || \
        -z "$MIP_OTEL_HEADERS_SECRET_KEY" ]]; then
    echo "${RED}[deploy] OTLP requires MIP_OTEL_ENDPOINT plus the Databricks Secret scope and key.${RST}" >&2
    exit 2
  fi
  if ! "$PYTHON" - "$MIP_OTEL_ENDPOINT" <<'PYEOF'
import sys
from tools.databricks.app_deploy_payload import validated_otel_endpoint

validated_otel_endpoint(sys.argv[1])
PYEOF
  then
    echo "${RED}[deploy] MIP_OTEL_ENDPOINT must be credential-free HTTPS without query or fragment.${RST}" >&2
    exit 2
  fi
  if [[ ! "$MIP_OTEL_HEADERS_SECRET_SCOPE" =~ ^[A-Za-z0-9._-]{1,128}$ || \
        ! "$MIP_OTEL_HEADERS_SECRET_KEY" =~ ^[A-Za-z0-9._-]{1,128}$ ]]; then
    echo "${RED}[deploy] OTLP Databricks Secret scope/key names are invalid.${RST}" >&2
    exit 2
  fi
fi
MIP_DEPLOYMENT_SOURCE_GIT_SHA="$SOURCE_GIT_SHA"
export MIP_DEFAULT_CATALOG MIP_APP_NAME MIP_LENDER_NAME MIP_LENDER_NMLS_ID MIP_TENANT_ID
export MIP_DEPLOYMENT_SOURCE_GIT_SHA
export MIP_UC_FOREIGN_CATALOG_BINDING_POLICY
export MIP_LAKEBASE_INSTANCE LAKEBASE_INSTANCE_NAME
export LAKEBASE_DATABASE MIP_LAKEBASE_DATABASE_NAME MIP_LAKEBASE_SYNC_CATALOG
export MIP_LAKEBASE_SYNC_SCHEMA MIP_LAKEBASE_SYNC_TABLES
export MIP_GENIE_SPACE_NAME MIP_RUNTIME_SECRET_SCOPE MIP_APP_ROLLBACK_SECRET_SCOPE
export MIP_AGENT_PROXY_SECRET_SCOPE
export MIP_OTEL_ENDPOINT MIP_OTEL_HEADERS_SECRET_SCOPE MIP_OTEL_HEADERS_SECRET_KEY
OTEL_RESOURCE_BINDING_ARGS=()
OTEL_DEPLOY_PAYLOAD_ARGS=()
if [[ -n "$MIP_OTEL_ENDPOINT" ]]; then
  OTEL_RESOURCE_BINDING_ARGS=(
    --otel-header-secret-scope "$MIP_OTEL_HEADERS_SECRET_SCOPE"
    --otel-header-secret-key "$MIP_OTEL_HEADERS_SECRET_KEY"
  )
  OTEL_DEPLOY_PAYLOAD_ARGS=(
    --otel-endpoint "$MIP_OTEL_ENDPOINT"
    --otel-header-resource otel_headers
  )
fi
APP_ROLLBACK_SECRET_SCOPE="$MIP_APP_ROLLBACK_SECRET_SCOPE"
if [[ ! "$MIP_APP_NAME" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]]; then
  echo "${RED}[deploy] MIP_APP_NAME must be a lowercase DNS-style name.${RST}" >&2
  exit 2
fi
if [[ "$MIP_AGENT_PROXY_SECRET_SCOPE" != "${MIP_APP_NAME}-agent-proxy" ]]; then
  echo "${RED}[deploy] MIP_AGENT_PROXY_SECRET_SCOPE must be the deterministic App-bound scope '${MIP_APP_NAME}-agent-proxy'.${RST}" >&2
  exit 2
fi
_EXPECTED_APP_ROLLBACK_SECRET_SCOPE="$("$PYTHON" - "$MIP_APP_NAME" <<'PYEOF'
import sys

name = sys.argv[1]
if name.endswith("-app"):
    print(f"{name}-rollback")
elif "-app-" in name:
    prefix, suffix = name.rsplit("-app-", 1)
    print(f"{prefix}-app-rollback-{suffix}")
else:
    print(f"{name}-rollback")
PYEOF
)"
if [[ "$MIP_APP_ROLLBACK_SECRET_SCOPE" != "$_EXPECTED_APP_ROLLBACK_SECRET_SCOPE" ]]; then
  echo "${RED}[deploy] MIP_APP_ROLLBACK_SECRET_SCOPE must be the deterministic App-bound scope '${_EXPECTED_APP_ROLLBACK_SECRET_SCOPE}'.${RST}" >&2
  exit 2
fi
if [[ ! "$MIP_LAKEBASE_INSTANCE" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]]; then
  echo "${RED}[deploy] MIP_LAKEBASE_INSTANCE must be a lowercase DNS-style name.${RST}" >&2
  exit 2
fi
if [[ ! "$MIP_DEFAULT_CATALOG" =~ ^[a-z_][a-z0-9_]{0,254}$ || \
      ! "$MIP_LAKEBASE_SYNC_CATALOG" =~ ^[a-z_][a-z0-9_]{0,254}$ || \
      ! "$MIP_LAKEBASE_SYNC_SCHEMA" =~ ^[a-z_][a-z0-9_]{0,254}$ || \
      ! "$LAKEBASE_DATABASE" =~ ^[a-z_][a-z0-9_]{0,254}$ ]]; then
  echo "${RED}[deploy] UC/Lakebase catalog, schema, and database must be lowercase unquoted identifiers.${RST}" >&2
  exit 2
fi
if ! "$PYTHON" - "$MIP_LAKEBASE_SYNC_TABLES" <<'PYEOF'
import re
import sys

names = sys.argv[1].split(",")
valid = re.compile(r"^[a-z_][a-z0-9_]{0,254}$").fullmatch
raise SystemExit(0 if names and len(names) == len(set(names)) and all(valid(name) for name in names) else 1)
PYEOF
then
  echo "${RED}[deploy] MIP_LAKEBASE_SYNC_TABLES must be a unique comma-separated list of lowercase unquoted identifiers.${RST}" >&2
  exit 2
fi
if [[ ! "$MIP_RUNTIME_SECRET_SCOPE" =~ ^[A-Za-z0-9._-]{1,128}$ || \
      ! "$MIP_APP_ROLLBACK_SECRET_SCOPE" =~ ^[A-Za-z0-9._-]{1,128}$ || \
      ! "$MIP_AGENT_PROXY_SECRET_SCOPE" =~ ^[A-Za-z0-9._-]{1,128}$ ]]; then
  echo "${RED}[deploy] Databricks secret-scope names are invalid.${RST}" >&2
  exit 2
fi
export BUNDLE_VAR_app_name="$MIP_APP_NAME"
export BUNDLE_VAR_lender_name="$MIP_LENDER_NAME"
export BUNDLE_VAR_lender_nmls_id="$MIP_LENDER_NMLS_ID"
export BUNDLE_VAR_tenant_id="$MIP_TENANT_ID"
export BUNDLE_VAR_lakebase_instance_name="$MIP_LAKEBASE_INSTANCE"
export BUNDLE_VAR_lakebase_catalog_name="$MIP_LAKEBASE_SYNC_CATALOG"
export BUNDLE_VAR_lakebase_database_name="$LAKEBASE_DATABASE"
_UC_APPROVED_OWNERS="${MIP_UC_APPROVED_OWNER_PRINCIPALS:-}"
if [[ -z "$_UC_APPROVED_OWNERS" ]]; then
  _UC_APPROVED_OWNERS="$(dotenv_value MIP_UC_APPROVED_OWNER_PRINCIPALS)"
fi
export MIP_UC_APPROVED_OWNER_PRINCIPALS="$_UC_APPROVED_OWNERS"
for _ACCOUNT_AUTH_NAME in DATABRICKS_ACCOUNT_HOST DATABRICKS_ACCOUNT_ID; do
  resolve_m2m_credential "$_ACCOUNT_AUTH_NAME"
done
resolve_m2m_credential DATABRICKS_ACCOUNT_CLIENT_ID shell
resolve_m2m_credential DATABRICKS_ACCOUNT_CLIENT_SECRET shell
resolve_m2m_credential DATABRICKS_OPERATOR2_CLIENT_ID shell
if [[ -z "$DATABRICKS_ACCOUNT_HOST" ]]; then
  DATABRICKS_ACCOUNT_HOST="https://accounts.cloud.databricks.com"
  export DATABRICKS_ACCOUNT_HOST
fi

# Admin-allowlist visibility check (2026-06-11, observed live). Databricks
# Apps deployment env_vars are a FULL REPLACEMENT, `admin_emails` defaults to
# "" in code (no personal identities in source), and the deploy payload
# deliberately does NOT bootstrap the deploying operator into admin (pinned
# by tests/unit/test_app_deploy_payload.py). So a deploy without
# MIP_ADMIN_EMAILS in the environment / .env.local ships an app where EVERY
# admin surface — asset detail, the audit feed, admin ops — returns 403 for
# everyone until the `mip-admin` workspace group exists. That is a valid
# group-based posture, but it must never happen silently. Warn, don't block.
_ADMIN_EMAILS_RESOLVED="${MIP_ADMIN_EMAILS:-$("$PYTHON" - <<'PYEOF'
from pathlib import Path
try:
    from dotenv import dotenv_values
    print((dotenv_values(Path(".env.local")).get("MIP_ADMIN_EMAILS") or "").strip())
except Exception:
    print("")
PYEOF
)}"
if [[ -z "$_ADMIN_EMAILS_RESOLVED" ]]; then
  echo "${YLW}[deploy] WARNING: MIP_ADMIN_EMAILS is not set (env or .env.local).${RST}" >&2
  echo "${YLW}  Admin surfaces (asset detail, audit feed, admin ops) will 403 for every${RST}" >&2
  echo "${YLW}  user unless they are in the '\${MIP_ADMIN_GROUP_NAME:-mip-admin}' workspace group.${RST}" >&2
  echo "${YLW}  To grant explicit admin: add MIP_ADMIN_EMAILS=<operator@email> to .env.local${RST}" >&2
  echo "${YLW}  (or export it for this run) and redeploy.${RST}" >&2
else
  echo "  admin allowlist: configured (MIP_ADMIN_EMAILS set)"
fi

APP_RUNTIME_ENV="${APP_ENV:-}"
if [[ -z "$APP_RUNTIME_ENV" ]]; then
  if [[ "$TARGET" == "dev" ]]; then
    APP_RUNTIME_ENV="sandbox"
  else
    APP_RUNTIME_ENV="$TARGET"
  fi
fi
APP_GIT_SHA="$SOURCE_GIT_SHA"

# Campaign/Genie confirmation tokens must remain verifiable across app
# restarts and replicas. Every deployed runtime, including the shared sandbox,
# requires an operator-owned current secret. Process-local and generated-file
# keys are allowed only by the backend's local/test runtime paths.
_GENIE_ACTION_SECRET_RESOLVED="${MIP_GENIE_ACTION_SECRET_CURRENT:-}"
if [[ -z "$_GENIE_ACTION_SECRET_RESOLVED" ]]; then
  _GENIE_ACTION_SECRET_RESOLVED="$(dotenv_value MIP_GENIE_ACTION_SECRET_CURRENT)"
fi
_GENIE_ACTION_SECRET_NORMALIZED="$(printf '%s' "$_GENIE_ACTION_SECRET_RESOLVED" | tr '[:upper:]' '[:lower:]')"
case "$_GENIE_ACTION_SECRET_NORMALIZED" in
  ""|redacted|changeme|change-me|change_me|placeholder|example|your-secret|your_secret)
    _GENIE_ACTION_SECRET_RESOLVED=""
    ;;
esac
if [[ "$_GENIE_ACTION_SECRET_NORMALIZED" == \<*\> ]]; then
  _GENIE_ACTION_SECRET_RESOLVED=""
fi
if [[ -z "$_GENIE_ACTION_SECRET_RESOLVED" ]]; then
  if [[ "$APP_RUNTIME_ENV" != "local" && "$APP_RUNTIME_ENV" != "test" ]]; then
    echo "${RED}[deploy] ERROR: MIP_GENIE_ACTION_SECRET_CURRENT is required for target '$TARGET' (APP_ENV=${APP_RUNTIME_ENV}).${RST}" >&2
    echo "${RED}  Deployed confirmation and campaign-provenance tokens require a stable, deployment-scoped HMAC key.${RST}" >&2
    exit 1
  fi
  echo "${YLW}[deploy] WARNING: local/test runtime will use its non-durable compatibility key.${RST}" >&2
else
  echo "  genie/campaign HMAC: configured"
fi
if [[ -n "$_GENIE_ACTION_SECRET_RESOLVED" ]]; then
  export MIP_GENIE_ACTION_SECRET_CURRENT="$_GENIE_ACTION_SECRET_RESOLVED"
fi

# Cotality ID-mask HMAC visibility check. The source-known compatibility
# namespace is local/test-only; sandbox and customer runtimes fail before any
# app mutation unless the operator supplies a durable deployment-scoped key.
_ID_MASK_RESOLVED="${MIP_COTALITY_ID_MASK_SECRET:-$("$PYTHON" - <<'PYEOF'
from pathlib import Path
try:
    from dotenv import dotenv_values
    values = dotenv_values(Path(".env.local"))
    print((values.get("MIP_COTALITY_ID_MASK_SECRET") or "").strip())
except Exception:
    print("")
PYEOF
)}"
_ID_MASK_NORMALIZED="$(printf '%s' "$_ID_MASK_RESOLVED" | tr '[:upper:]' '[:lower:]')"
case "$_ID_MASK_NORMALIZED" in
  ""|redacted|changeme|change-me|change_me|placeholder|example|your-secret|your_secret|mip-cotality-id-mask-v1)
    _ID_MASK_RESOLVED=""
    ;;
esac
if [[ "$_ID_MASK_NORMALIZED" == \<*\> ]]; then
  _ID_MASK_RESOLVED=""
fi
if [[ -z "$_ID_MASK_RESOLVED" ]]; then
  if [[ "$APP_RUNTIME_ENV" != "local" && "$APP_RUNTIME_ENV" != "test" ]]; then
    echo "${RED}[deploy] ERROR: MIP_COTALITY_ID_MASK_SECRET is required for target '$TARGET' (APP_ENV=${APP_RUNTIME_ENV}).${RST}" >&2
    echo "${RED}  Deployed runtimes must use a deployment-scoped HMAC secret;${RST}" >&2
    echo "${RED}  the source-known compatibility namespace is allowed only for local/test.${RST}" >&2
    exit 1
  fi
  echo "${YLW}[deploy] WARNING: MIP_COTALITY_ID_MASK_SECRET is not set (env or .env.local).${RST}" >&2
  echo "${YLW}  Cotality ID masking will use the local/test compatibility namespace.${RST}" >&2
else
  echo "  cotality id-mask secret: configured"
fi
if [[ -n "$_ID_MASK_RESOLVED" ]]; then
  export MIP_COTALITY_ID_MASK_SECRET="$_ID_MASK_RESOLVED"
fi

# Exact AI Gateway proof rows use verifier-only Ed25519 signatures. The App
# receives only the derived public key, so the Lakebase proof-writer credential
# cannot manufacture a claimable row by itself.
# shellcheck disable=SC2031  # Parent-shell secret is unchanged by M2M subshells.
_AI_GATEWAY_PROOF_PREVIOUS_KEY_RESOLVED="${MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY:-}"
if [[ -z "$_AI_GATEWAY_PROOF_PREVIOUS_KEY_RESOLVED" ]]; then
  _AI_GATEWAY_PROOF_PREVIOUS_KEY_RESOLVED="$(dotenv_value MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY)"
fi
if [[ -n "$_AI_GATEWAY_PROOF_PREVIOUS_KEY_RESOLVED" ]]; then
  # shellcheck disable=SC2031  # Intentional parent-shell restoration.
  export MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY="$_AI_GATEWAY_PROOF_PREVIOUS_KEY_RESOLVED"
fi
# shellcheck disable=SC2031  # Intentional parent-shell restoration.
_AI_GATEWAY_PROOF_HISTORICAL_KEYS_RESOLVED="${MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS:-}"
if [[ -z "$_AI_GATEWAY_PROOF_HISTORICAL_KEYS_RESOLVED" ]]; then
  _AI_GATEWAY_PROOF_HISTORICAL_KEYS_RESOLVED="$(dotenv_value MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS)"
fi
if [[ -n "$_AI_GATEWAY_PROOF_HISTORICAL_KEYS_RESOLVED" ]]; then
  # shellcheck disable=SC2031  # Intentional parent-shell restoration.
  export MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS="$_AI_GATEWAY_PROOF_HISTORICAL_KEYS_RESOLVED"
fi
# shellcheck disable=SC2031  # Intentional parent-shell restoration.
_AI_GATEWAY_PROOF_SIGNING_KEY_RESOLVED="${MIP_AI_GATEWAY_PROOF_SIGNING_KEY:-}"
if [[ -z "$_AI_GATEWAY_PROOF_SIGNING_KEY_RESOLVED" ]]; then
  _AI_GATEWAY_PROOF_SIGNING_KEY_RESOLVED="$(dotenv_value MIP_AI_GATEWAY_PROOF_SIGNING_KEY)"
fi
if [[ -z "$_AI_GATEWAY_PROOF_SIGNING_KEY_RESOLVED" ]]; then
  if [[ "$APP_RUNTIME_ENV" != "local" && "$APP_RUNTIME_ENV" != "test" ]]; then
    echo "${RED}[deploy] ERROR: MIP_AI_GATEWAY_PROOF_SIGNING_KEY is required for target '$TARGET' (APP_ENV=${APP_RUNTIME_ENV}).${RST}" >&2
    echo "${RED}  AI Gateway exact-row proof requires a verifier-only Ed25519 key.${RST}" >&2
    exit 1
  fi
else
  # shellcheck disable=SC2031
  MIP_AI_GATEWAY_PROOF_SIGNING_KEY="$_AI_GATEWAY_PROOF_SIGNING_KEY_RESOLVED"
  export -n MIP_AI_GATEWAY_PROOF_SIGNING_KEY
  if ! MIP_AI_GATEWAY_PROOF_VERIFY_KEY="$(
    MIP_AI_GATEWAY_PROOF_SIGNING_KEY="$MIP_AI_GATEWAY_PROOF_SIGNING_KEY" \
      "$PYTHON" - <<'PYEOF'
import os
from backend.services.ai_gateway_proof_attestation import derive_gateway_proof_verify_key

print(derive_gateway_proof_verify_key(os.environ["MIP_AI_GATEWAY_PROOF_SIGNING_KEY"]))
PYEOF
  )"; then
    echo "${RED}[deploy] ERROR: MIP_AI_GATEWAY_PROOF_SIGNING_KEY is invalid.${RST}" >&2
    exit 1
  fi
  export MIP_AI_GATEWAY_PROOF_VERIFY_KEY
  echo "  AI Gateway proof attestation: verifier private key / runtime public key configured"
fi

# Proxy-model provenance uses a distinct release-signing key. The runtime
# receives this private key only for the bounded model registration command;
# it never receives the verifier-only inference-row proof key.
# The previous public key is safe to load from local configuration and must
# accompany the current key through provisioning so rotations remain verifiable.
# shellcheck disable=SC2031  # Parent-shell value is intentionally restored.
_GATEWAY_MODEL_PREVIOUS_KEY_RESOLVED="${MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY:-}"
if [[ -z "$_GATEWAY_MODEL_PREVIOUS_KEY_RESOLVED" ]]; then
  _GATEWAY_MODEL_PREVIOUS_KEY_RESOLVED="$(
    dotenv_value MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY
  )"
fi
if [[ -n "$_GATEWAY_MODEL_PREVIOUS_KEY_RESOLVED" ]]; then
  # shellcheck disable=SC2155  # Splitting would expose printf's status to set -e on a secret export.
  export MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY="$(
    printf '%s' "$_GATEWAY_MODEL_PREVIOUS_KEY_RESOLVED"
  )"
fi
# shellcheck disable=SC2031  # Parent-shell secret is unchanged by M2M subshells.
_GATEWAY_MODEL_SIGNING_KEY_RESOLVED="${MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY:-}"
if [[ -z "$_GATEWAY_MODEL_SIGNING_KEY_RESOLVED" ]]; then
  _GATEWAY_MODEL_SIGNING_KEY_RESOLVED="$(
    dotenv_value MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY
  )"
fi
if [[ -z "$_GATEWAY_MODEL_SIGNING_KEY_RESOLVED" ]]; then
  if [[ "$APP_RUNTIME_ENV" != "local" && "$APP_RUNTIME_ENV" != "test" ]]; then
    echo "${RED}[deploy] ERROR: MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY is required for target '$TARGET' (APP_ENV=${APP_RUNTIME_ENV}).${RST}" >&2
    exit 1
  fi
else
  # shellcheck disable=SC2031
  MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY="$_GATEWAY_MODEL_SIGNING_KEY_RESOLVED"
  export -n MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY
  if ! MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY="$(
    MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY="$MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY" \
      "$PYTHON" - <<'PYEOF'
import os
from backend.services.ai_gateway_proof_attestation import derive_gateway_proof_verify_key

print(derive_gateway_proof_verify_key(
    os.environ["MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY"]
))
PYEOF
  )"; then
    echo "${RED}[deploy] ERROR: MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY is invalid.${RST}" >&2
    exit 1
  fi
  export MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY
  if [[ -n "${MIP_AI_GATEWAY_PROOF_VERIFY_KEY:-}" && \
        "$MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY" == \
        "$MIP_AI_GATEWAY_PROOF_VERIFY_KEY" ]]; then
    echo "${RED}[deploy] ERROR: model-attestation and verifier-proof keys must be distinct.${RST}" >&2
    exit 1
  fi
  echo "  Gateway model attestation: separated model-provenance key configured"
fi
