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

# >>> deploy interpreter pins: check >>>
# A drifted mlflow or databricks-sdk registers and serves a stack CI never
# proved (full_deploy_readiness.md 3.12), so the
# interpreter's installed closure must equal the requirements.txt pins
# (uv.lock). tools/python_deps_check.py exits 0 pinned, 10 drift (it prints the
# remedy); anything else is a hard error. A dry run reports and continues; a
# real run refuses with exit 2 before any workspace call. Isolated
# shell-contract copies carry no tool: a dry run skips, a real run refuses.
PYTHON_DEPS_STATE="skipped"
if [[ -f tools/python_deps_check.py ]]; then
  _python_deps_rc=0
  "$PYTHON" -m tools.python_deps_check || _python_deps_rc=$?
  case "$_python_deps_rc" in
    0) PYTHON_DEPS_STATE="pinned" ;;
    10) PYTHON_DEPS_STATE="drifted" ;;
    *) PYTHON_DEPS_STATE="error" ;;
  esac
  unset _python_deps_rc
fi
echo "  python deps: ${PYTHON_DEPS_STATE}"
if [[ "$DRY_RUN" -eq 0 && "$PYTHON_DEPS_STATE" != "pinned" ]]; then
  case "$PYTHON_DEPS_STATE" in
    drifted) echo "${RED}[deploy] ${PYTHON} does not match the requirements.txt pins; apply the fix above.${RST}" >&2 ;;
    error) echo "${RED}[deploy] the deploy-interpreter pin check failed; ${PYTHON} cannot be proven pinned.${RST}" >&2 ;;
    *) echo "${RED}[deploy] tools/python_deps_check.py is not in this tree; ${PYTHON} cannot be proven pinned.${RST}" >&2 ;;
  esac
  echo "  select a pinned interpreter with MIP_DEPLOY_PYTHON (docs/deployment.md, \"Deploy interpreter\")." >&2
  exit 2
fi
# <<< deploy interpreter pins: check <<<

# >>> requirements digest: check >>>
# On 2026-10-28 the Databricks Apps build skipped the reinstall for a lock bump
# that left requirements.txt unchanged, and the App kept its old wheels. It
# reinstalls only when the bytes of requirements.txt change, never following
# the -c uv.lock / -r requirements.in includes (confirmed the same day by the
# first deploy carrying the digest line), so a lock bump needs a restamp (docs/deployment.md, "App Python dependencies: the
# deps-digest line").
# tools/requirements_digest.py exits 0 current and 1 stale (it prints the
# remedy); anything else is a hard error. A dry run reports and continues; a
# real run refuses with exit 2 before any workspace call. Isolated
# shell-contract copies carry no tool: a dry run skips, a real run refuses.
REQUIREMENTS_DIGEST_STATE="skipped"
if [[ -f tools/requirements_digest.py ]]; then
  _requirements_digest_rc=0
  "$PYTHON" -m tools.requirements_digest --check || _requirements_digest_rc=$?
  case "$_requirements_digest_rc" in
    0) REQUIREMENTS_DIGEST_STATE="current" ;;
    1) REQUIREMENTS_DIGEST_STATE="stale" ;;
    *) REQUIREMENTS_DIGEST_STATE="error" ;;
  esac
  unset _requirements_digest_rc
fi
echo "  requirements digest: ${REQUIREMENTS_DIGEST_STATE}"
if [[ "$DRY_RUN" -eq 0 && "$REQUIREMENTS_DIGEST_STATE" != "current" ]]; then
  case "$REQUIREMENTS_DIGEST_STATE" in
    stale) echo "${RED}[deploy] requirements.txt's deps-digest line is stale, so the Apps build can skip the dependency reinstall; apply the fix above and commit.${RST}" >&2 ;;
    error) echo "${RED}[deploy] the requirements digest check failed; requirements.txt cannot be proven current.${RST}" >&2 ;;
    *) echo "${RED}[deploy] tools/requirements_digest.py is not in this tree; requirements.txt cannot be proven current.${RST}" >&2 ;;
  esac
  exit 2
fi
# <<< requirements digest: check <<<

# >>> app runtime closure: check >>>
# Step 1 stages the App source from `git archive` of this commit (tools/
# databricks/app_runtime_closure.py), and a refusal there would stop and
# quiesce the App. Run its git-side refusals (junk, hidden, symlink or
# notebook-shaped tracked files, a missing required entry, a stale deps-digest
# line, the tracked-file allowance) here, with no dist and no workspace call,
# so an unstageable commit is refused before the lease. Exit codes: 0 current,
# 2 refused (it prints why); anything else is a hard error. A dry run reports
# and continues; a real run refuses with exit 2. Isolated shell-contract
# copies carry no tool: a dry run skips, a real run refuses.
APP_RUNTIME_CLOSURE_STATE="skipped"
if [[ -f tools/databricks/app_runtime_closure.py ]]; then
  _app_runtime_closure_rc=0
  "$PYTHON" -m tools.databricks.app_runtime_closure check --ref "$SOURCE_GIT_SHA" || _app_runtime_closure_rc=$?
  case "$_app_runtime_closure_rc" in
    0) APP_RUNTIME_CLOSURE_STATE="current" ;;
    2) APP_RUNTIME_CLOSURE_STATE="refused" ;;
    *) APP_RUNTIME_CLOSURE_STATE="error" ;;
  esac
  unset _app_runtime_closure_rc
fi
echo "  app runtime closure: ${APP_RUNTIME_CLOSURE_STATE}"
if [[ "$DRY_RUN" -eq 0 && "$APP_RUNTIME_CLOSURE_STATE" != "current" ]]; then
  case "$APP_RUNTIME_CLOSURE_STATE" in
    refused) echo "${RED}[deploy] the App runtime closure at ${SOURCE_GIT_SHA} cannot be staged; fix the refusal above and commit.${RST}" >&2 ;;
    error) echo "${RED}[deploy] the App runtime closure check failed; ${SOURCE_GIT_SHA} cannot be proven stageable.${RST}" >&2 ;;
    *) echo "${RED}[deploy] tools/databricks/app_runtime_closure.py is not in this tree; the App source cannot be proven stageable.${RST}" >&2 ;;
  esac
  exit 2
fi
# <<< app runtime closure: check <<<

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

# >>> frontend dependency freshness: check >>>
# Step 1 builds the App source from frontend/package-lock.json; an install
# that drifted from the lock would ship a build the lock never described
# (W5a carryover, audit stack-10). Check and report here, before the prompt;
# the repair (`npm ci`) runs only after it. A shared/symlinked install is
# never mutated. Exit codes of tools/frontend_deps_check.py: 0 fresh,
# 10 stale or missing, 11 shared; anything else is a hard preflight error.
FRONTEND_DEPS_STATE="skipped"
if [[ -f tools/frontend_deps_check.py ]]; then
  _frontend_deps_rc=0
  "$PYTHON" -m tools.frontend_deps_check || _frontend_deps_rc=$?
  case "$_frontend_deps_rc" in
    0) FRONTEND_DEPS_STATE="fresh" ;;
    10) FRONTEND_DEPS_STATE="stale" ;;
    11) FRONTEND_DEPS_STATE="shared" ;;
    *) FRONTEND_DEPS_STATE="error" ;;
  esac
  echo "  frontend deps: ${FRONTEND_DEPS_STATE}"
  if [[ "$DRY_RUN" -eq 0 && "$FRONTEND_DEPS_STATE" == "shared" ]]; then
    echo "${RED}[deploy] frontend/node_modules is a shared/symlinked install; run npm --prefix frontend ci in a private checkout.${RST}" >&2
    exit 2
  fi
  if [[ "$DRY_RUN" -eq 0 && "$FRONTEND_DEPS_STATE" == "error" ]]; then
    echo "${RED}[deploy] the frontend dependency check failed (exit ${_frontend_deps_rc}).${RST}" >&2
    exit 2
  fi
else
  echo "  frontend deps: skipped (tools/frontend_deps_check.py is not in this tree)"
fi
# <<< frontend dependency freshness: check <<<

# >>> scheduled-refresh (data-freshness design 2026-10-26)
# mip_scheduled_refresh's pause status is decided here, never in a bundle
# target: the variable defaults to PAUSED. Only the dev target's LIVE install
# unpauses, by default; MIP_SCHEDULED_REFRESH=off opts it out. prod is always
# PAUSED: a prod cadence is a customer decision that needs a reviewed change
# here, so an explicit `on` for prod is refused rather than ignored. Pure, so
# it also runs in DRY_RUN. A Jobs-UI unpause is reverted by the next deploy,
# and any apply outside this script re-pauses the live schedule; this switch is
# the durable one (docs/runbook.md "Weekly scheduled refresh").
if [[ "$TARGET" == "dev" ]]; then
  _SCHEDULED_REFRESH="$(deployment_control_value MIP_SCHEDULED_REFRESH on)"
else
  _SCHEDULED_REFRESH="$(deployment_control_value MIP_SCHEDULED_REFRESH off)"
fi
case "$_SCHEDULED_REFRESH" in
  on|off) ;;
  *)
    echo "${RED}[deploy] MIP_SCHEDULED_REFRESH must be on or off.${RST}" >&2
    exit 2
    ;;
esac
if [[ "$TARGET" != "dev" && "$_SCHEDULED_REFRESH" == "on" ]]; then
  echo "${RED}[deploy] MIP_SCHEDULED_REFRESH=on is honoured only for the dev target's live install; ${TARGET} keeps mip_scheduled_refresh PAUSED.${RST}" >&2
  exit 2
fi
if [[ "$TARGET" == "dev" && "$_SCHEDULED_REFRESH" == "on" ]]; then
  BUNDLE_VAR_scheduled_refresh_pause_status=UNPAUSED
else
  BUNDLE_VAR_scheduled_refresh_pause_status=PAUSED
fi
export BUNDLE_VAR_scheduled_refresh_pause_status
echo "  scheduled refresh: ${BUNDLE_VAR_scheduled_refresh_pause_status} (mip_scheduled_refresh, Fridays 06:00 America/Chicago)"
unset _SCHEDULED_REFRESH
# <<< scheduled-refresh

if [[ "$DRY_RUN" -eq 0 && "$NO_CONFIRM" -eq 0 ]]; then
  read -r -p "About to DEPLOY to the ${TARGET} target. Continue? [y/N] " ans
  if [[ "$ans" != "y" && "$ans" != "Y" ]]; then
    echo "aborted."
    exit 1
  fi
fi

# >>> frontend dependency freshness: repair >>>
# After the confirmation prompt (or NO_CONFIRM): install exactly the lock into
# this checkout's own frontend/node_modules, then prove it took.
if [[ "$DRY_RUN" -eq 0 && "$FRONTEND_DEPS_STATE" == "stale" ]]; then
  run npm --prefix frontend ci --no-audit --no-fund
  _frontend_deps_rc=0
  "$PYTHON" -m tools.frontend_deps_check || _frontend_deps_rc=$?
  if [[ "$_frontend_deps_rc" -ne 0 ]]; then
    echo "${RED}[deploy] frontend/node_modules still does not match frontend/package-lock.json after npm ci.${RST}" >&2
    echo "  fix: npm --prefix frontend ci --no-audit --no-fund, then re-run ./scripts/deploy.sh" >&2
    exit 2
  fi
fi
# <<< frontend dependency freshness: repair <<<

# Resolve deployment-scoped controls before any workspace mutation. A reviewed
# shell export wins; otherwise the documented .env.local value wins; defaults
# preserve the established Entrada installation. Alias drift still fails.
MIP_DEFAULT_CATALOG="$(deployment_control_value MIP_DEFAULT_CATALOG mip)"
MIP_UC_FOREIGN_CATALOG_BINDING_POLICY="$(
  deployment_control_value MIP_UC_FOREIGN_CATALOG_BINDING_POLICY
)"
MIP_APP_NAME="$(deployment_control_value MIP_APP_NAME mip-app)"
# The resolver below gets the lender name only when it is configured: an
# unset name is Summit there, and a pinned record must not meet that default.
_MIP_LENDER_NAME_CONFIGURED="$(deployment_control_value MIP_LENDER_NAME)"
MIP_LENDER_NAME="${_MIP_LENDER_NAME_CONFIGURED:-Summit Mortgage}"
_MIP_LENDER_NMLS_ID="$(deployment_control_value MIP_LENDER_NMLS_ID)"
_MIP_TENANT_ID="$(deployment_control_value MIP_TENANT_ID)"
# A customer lender is a digest-pinned record, never a bare name: the
# identifying record and its public reviewed sha256 (backend/schemas/
# lender_identity_record.py). Neither is ever printed.
_MIP_LENDER_IDENTITY_JSON="$(deployment_control_value MIP_LENDER_IDENTITY_JSON)"
_MIP_LENDER_IDENTITY_SHA256="$(deployment_control_value MIP_LENDER_IDENTITY_SHA256)"
if [[ ! -f backend/schemas/lender_identity.py || ! -f backend/schemas/lender_identity_record.py ]]; then
  # Isolated shell-contract tests copy only deploy.sh. The only safe fallback
  # without the source-controlled registry is the exact built-in demo pair.
  if [[ "$MIP_LENDER_NAME" != "Summit Mortgage" || \
        ( -n "$_MIP_LENDER_NMLS_ID" && "$_MIP_LENDER_NMLS_ID" != "123456" ) || \
        ( -n "$_MIP_TENANT_ID" && "$_MIP_TENANT_ID" != "summit" ) || \
        -n "${_MIP_LENDER_IDENTITY_JSON//[[:space:]]/}" || \
        -n "${_MIP_LENDER_IDENTITY_SHA256//[[:space:]]/}" ]]; then
    echo "${RED}[deploy] source-controlled lender identity registry is unavailable.${RST}" >&2
    exit 2
  fi
  _MIP_LENDER_IDENTITY=$'Summit Mortgage\t123456\tsummit'
elif ! _MIP_LENDER_IDENTITY="$(
    "$PYTHON" -c '
import sys
from backend.schemas.lender_identity_record import (
    LEGACY_ENV_NAMES, PINNED_ENV_NAMES, present_env, reviewed_lender_identity_from_env,
)
identity = reviewed_lender_identity_from_env(
    present_env(dict(zip((*LEGACY_ENV_NAMES, *PINNED_ENV_NAMES), sys.argv[1:], strict=True)))
)
fields = (identity.lender_name, identity.nmls_id, identity.tenant_id)
print("\t".join((*fields, identity.digest) if identity.digest else fields))
' "$_MIP_LENDER_NAME_CONFIGURED" "$_MIP_LENDER_NMLS_ID" "$_MIP_TENANT_ID" \
      "$_MIP_LENDER_IDENTITY_JSON" "$_MIP_LENDER_IDENTITY_SHA256"
)"; then
  echo "${RED}[deploy] lender name, NMLS id, tenant disclosure namespace, or pinned lender record is invalid.${RST}" >&2
  exit 2
fi
_MIP_LENDER_IDENTITY_DIGEST=''
IFS=$'\t' read -r MIP_LENDER_NAME MIP_LENDER_NMLS_ID MIP_TENANT_ID _MIP_LENDER_IDENTITY_DIGEST \
  <<< "$_MIP_LENDER_IDENTITY"
if [[ -z "$MIP_LENDER_NAME" || -z "$MIP_LENDER_NMLS_ID" || -z "$MIP_TENANT_ID" ]]; then
  echo "${RED}[deploy] lender disclosure identity did not resolve completely.${RST}" >&2
  exit 2
fi
if [[ -n "$_MIP_LENDER_IDENTITY_DIGEST" ]]; then
  # Verified above: the App payload, bundle_env, the SQL render and the
  # Lakebase migration each re-verify the record against this pin.
  MIP_LENDER_IDENTITY_JSON="$_MIP_LENDER_IDENTITY_JSON"
  MIP_LENDER_IDENTITY_SHA256="$_MIP_LENDER_IDENTITY_DIGEST"
  export MIP_LENDER_IDENTITY_JSON MIP_LENDER_IDENTITY_SHA256
  echo "[deploy] lender identity: digest-pinned record ${MIP_LENDER_IDENTITY_SHA256:0:12}"
fi
# Optional lender co-branding (audit responsive-10, report 12.4 #9). The mark
# file is checked against the reviewed registry (backend/schemas/
# lender_branding.py) before any workspace mutation; only its sha256 travels
# on, to Step 1's stage and the frontend build. The path is never logged.
# The theme and accent are tenant defaults for users who have not chosen;
# the product defaults (dark, bright) emit nothing.
MIP_LENDER_MARK_FILE="$(deployment_control_value MIP_LENDER_MARK_FILE)"
MIP_DEFAULT_THEME="$(deployment_control_value MIP_DEFAULT_THEME dark)"
MIP_DEFAULT_ACCENT="$(deployment_control_value MIP_DEFAULT_ACCENT bright)"
if [[ ! "$MIP_DEFAULT_THEME" =~ ^(dark|light|system)$ ]]; then
  echo "${RED}[deploy] MIP_DEFAULT_THEME must be dark, light or system.${RST}" >&2
  exit 2
fi
if [[ ! "$MIP_DEFAULT_ACCENT" =~ ^(bright|teal|navy|red)$ ]]; then
  echo "${RED}[deploy] MIP_DEFAULT_ACCENT must be bright, teal, navy or red.${RST}" >&2
  exit 2
fi
if [[ -f tools/branding/lender_mark.py ]]; then
  if ! MIP_LENDER_MARK_SHA256="$(
      "$PYTHON" -m tools.branding.lender_mark validate --lender "$MIP_LENDER_NAME" \
        --nmls "$MIP_LENDER_NMLS_ID" --file "$MIP_LENDER_MARK_FILE" --print-sha256
    )" || [[ -n "$MIP_LENDER_MARK_SHA256" && ! "$MIP_LENDER_MARK_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
    echo "${RED}[deploy] lender mark did not pass the reviewed lender-mark registry.${RST}" >&2
    exit 2
  fi
else
  # Isolated shell-contract copies carry no branding tool (as the lender
  # identity fallback above): the only safe answer there is no mark.
  if [[ -n "$MIP_LENDER_MARK_FILE" ]]; then
    echo "${RED}[deploy] lender mark tool is unavailable; unset MIP_LENDER_MARK_FILE.${RST}" >&2
    exit 2
  fi
  MIP_LENDER_MARK_SHA256=''
fi
if [[ -n "$MIP_LENDER_MARK_SHA256" ]]; then
  echo "[deploy] lender mark: ${MIP_LENDER_MARK_SHA256:0:12}"
else
  echo "[deploy] lender mark: none"
fi
export MIP_LENDER_MARK_FILE MIP_DEFAULT_THEME MIP_DEFAULT_ACCENT MIP_LENDER_MARK_SHA256
# Demo-only presenter mode (D-shell-deviations-e1; W5b NB-3) and the consented
# Genie refusal-text capture switch (D-audit-reads-d) are parsed here, before
# any workspace mutation: a truthy presenter value for prod, or a typo in
# either, stops at step 0 instead of at App promotion. The presenter
# vocabulary is the payload's _PRESENTER_MODE_ON/_OFF (the two are pinned
# equal); tr, not ${var,,}, so bash 3.2 parses it. The payload keeps its own
# checks as defence in depth.
_MIP_PRESENTER_MODE="$(deployment_control_value MIP_PRESENTER_MODE \
  | tr '[:upper:]' '[:lower:]' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
case "$_MIP_PRESENTER_MODE" in
  ''|0|false|no|off) ;;
  1|true|yes|on)
    if [[ "$TARGET" == "prod" ]]; then
      echo "${RED}[deploy] MIP_PRESENTER_MODE is demo-only and refused for target prod.${RST}" >&2
      exit 2
    fi
    ;;
  *)
    echo "${RED}[deploy] MIP_PRESENTER_MODE must be one of 0/1/true/false/yes/no/on/off.${RST}" >&2
    exit 2
    ;;
esac
_MIP_GENIE_REFUSAL_TEXT_CAPTURE="$(deployment_control_value MIP_GENIE_REFUSAL_TEXT_CAPTURE \
  | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
case "$_MIP_GENIE_REFUSAL_TEXT_CAPTURE" in
  ''|enabled|disabled) ;;
  *)
    echo "${RED}[deploy] MIP_GENIE_REFUSAL_TEXT_CAPTURE must be enabled or disabled.${RST}" >&2
    exit 2
    ;;
esac
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
# >>> pii-salt scope control >>>
# The write-once PII salt (tools/databricks/pii_salt_scope.py). The silver SQL
# and the Lakeflow pipeline read secret('mip', 'pii-salt-v1') as literals, so
# mip is the only accepted scope until they take this control: another scope
# would hold a salt nothing reads, while the readers hash with whatever sits
# under mip. An existing salt is never rewritten either way. Require-existing
# stays 0 (a first install creates the salt) until the install profile
# records that masked keys already exist.
MIP_PII_SALT_SCOPE="$(deployment_control_value MIP_PII_SALT_SCOPE mip)"
PII_SALT_REQUIRE_EXISTING=0
if [[ "$MIP_PII_SALT_SCOPE" != "mip" ]]; then
  echo "${RED}[deploy] MIP_PII_SALT_SCOPE must be mip: the silver SQL and the Lakeflow pipeline read secret('mip', 'pii-salt-v1') as literals.${RST}" >&2
  exit 2
fi
# <<< pii-salt scope control <<<
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
# >>> cotality source names >>>
# The Cotality source share the silver lift reads (backend/config/
# source_names.py); unset means the canonical names. Resolved once here (shell,
# then .env.local) and exported, so the SQL renderer, the lineage-view helper,
# the Genie spec render and bundle_env (which sets the cotality_source_* bundle
# variables the feature pipeline's configuration reads) all see the same
# names. A render that would leave a canonical token refuses on its own. A
# share with no table prefix spells it "-": a blank value means unset.
MIP_COTALITY_SOURCE_CATALOG="$(deployment_control_value MIP_COTALITY_SOURCE_CATALOG)"
MIP_COTALITY_SOURCE_SCHEMA="$(deployment_control_value MIP_COTALITY_SOURCE_SCHEMA)"
MIP_COTALITY_SOURCE_TABLE_PREFIX="$(deployment_control_value MIP_COTALITY_SOURCE_TABLE_PREFIX)"
if [[ -n "$MIP_COTALITY_SOURCE_CATALOG" && ! "$MIP_COTALITY_SOURCE_CATALOG" =~ ^[a-z_][a-z0-9_]{0,254}$ ]] || \
   [[ -n "$MIP_COTALITY_SOURCE_SCHEMA" && ! "$MIP_COTALITY_SOURCE_SCHEMA" =~ ^[a-z_][a-z0-9_]{0,254}$ ]] || \
   [[ -n "$MIP_COTALITY_SOURCE_TABLE_PREFIX" && ! "$MIP_COTALITY_SOURCE_TABLE_PREFIX" =~ ^([a-z0-9_]{1,64}|-)$ ]]; then
  echo "${RED}[deploy] MIP_COTALITY_SOURCE_CATALOG, _SCHEMA and _TABLE_PREFIX must be lowercase unquoted identifiers (_TABLE_PREFIX '-' for none).${RST}" >&2
  exit 2
fi
export MIP_COTALITY_SOURCE_CATALOG MIP_COTALITY_SOURCE_SCHEMA MIP_COTALITY_SOURCE_TABLE_PREFIX
# <<< cotality source names <<<
export BUNDLE_VAR_app_name="$MIP_APP_NAME"
export BUNDLE_VAR_lender_name="$MIP_LENDER_NAME"
export BUNDLE_VAR_lender_nmls_id="$MIP_LENDER_NMLS_ID"
export BUNDLE_VAR_tenant_id="$MIP_TENANT_ID"
if [[ -n "$_MIP_LENDER_IDENTITY_DIGEST" ]]; then
  export BUNDLE_VAR_lender_identity_json="$MIP_LENDER_IDENTITY_JSON"
  export BUNDLE_VAR_lender_identity_sha256="$MIP_LENDER_IDENTITY_SHA256"
else
  # Only the record verified above may reach the bundle, never an ambient one.
  unset BUNDLE_VAR_lender_identity_json BUNDLE_VAR_lender_identity_sha256
fi
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

# >>> auditor-sod-check
# Auditor segregation-of-duties check (D-audit-reads-c3). Warn, never refuse; counts only.
# Each list resolves from the shell env first, else .env.local (the admin
# check's precedence above); no identity is ever printed.
_AUDITOR_SOD="$(
  MIP_AUDITOR_EMAILS="${MIP_AUDITOR_EMAILS:-}" \
  MIP_AUDITOR_IDENTITIES="${MIP_AUDITOR_IDENTITIES:-}" \
  MIP_ADMIN_EMAILS="${MIP_ADMIN_EMAILS:-}" \
  MIP_ADMIN_IDENTITIES="${MIP_ADMIN_IDENTITIES:-}" \
  MIP_APPROVER_EMAILS="${MIP_APPROVER_EMAILS:-}" \
  MIP_APPROVER_IDENTITIES="${MIP_APPROVER_IDENTITIES:-}" \
  "$PYTHON" - <<'PYEOF'
import os
from pathlib import Path

try:
    from dotenv import dotenv_values

    DOTENV = dotenv_values(Path(".env.local"))
except Exception:
    DOTENV = {}


def identities(*names):
    found = set()
    for name in names:
        raw = (os.environ.get(name) or DOTENV.get(name) or "").strip()
        found |= {token.strip().lower() for token in raw.split(",") if token.strip()}
    return found


AUDITORS = identities("MIP_AUDITOR_EMAILS", "MIP_AUDITOR_IDENTITIES")
PRIVILEGED = identities(
    "MIP_ADMIN_EMAILS", "MIP_ADMIN_IDENTITIES", "MIP_APPROVER_EMAILS", "MIP_APPROVER_IDENTITIES"
)
print(f"{len(AUDITORS)}\t{len(AUDITORS & PRIVILEGED)}")
PYEOF
)" || _AUDITOR_SOD=""
[[ -n "$_AUDITOR_SOD" ]] || _AUDITOR_SOD=$'0\t0'
IFS=$'\t' read -r _AUDITOR_COUNT _AUDITOR_OVERLAP <<<"$_AUDITOR_SOD"
echo "  auditor allowlist: ${_AUDITOR_COUNT:-0} configured"
if [[ "${_AUDITOR_OVERLAP:-0}" =~ ^[1-9][0-9]*$ ]]; then
  if [[ "$_AUDITOR_OVERLAP" == "1" ]]; then
    _AUDITOR_OVERLAP_TEXT="1 auditor identity also holds"
  else
    _AUDITOR_OVERLAP_TEXT="${_AUDITOR_OVERLAP} auditor identities also hold"
  fi
  echo "${YLW}[deploy] WARNING: ${_AUDITOR_OVERLAP_TEXT} the administrator or approver role; auditors should hold neither (segregation of duties).${RST}" >&2
fi
# <<< auditor-sod-check

APP_RUNTIME_ENV="${APP_ENV:-}"
if [[ -z "$APP_RUNTIME_ENV" ]]; then
  if [[ "$TARGET" == "dev" ]]; then
    APP_RUNTIME_ENV="sandbox"
  else
    APP_RUNTIME_ENV="$TARGET"
  fi
fi
APP_GIT_SHA="$SOURCE_GIT_SHA"

# Runtime HMAC keys (the Cotality ID mask and the Genie action current and
# previous keys) are presence-only and need no deploy input. The provisioner
# keeps every key already in the runtime secret scope, never reading or
# rewriting its value, and writes an absent key once (a first install) from
# its optional shell or .env.local input or a value generated on the runner.
# No value of theirs reaches CI. The read-only plan below (runtime secret
# pre-stop check) asserts each key's presence or names the first-install
# write before the App stop.
echo "  runtime HMAC keys: presence-only (an existing key is kept unread; an absent one is written once)"

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

# >>> runtime secret pre-stop check >>>
# Step 0a stops the App before the bundle-apply step provisions mip-runtime.
# Plan that provisioning read-only here, while the App still serves: the plan
# lists the scope's keys (it reads no value), keeps every existing key, and
# names each first-install write, so an unusable optional input or another
# workspace refuses now, not after the stop (full_deploy_readiness.md 3.7).
# The tool names keys only, never values.
if [[ "$DRY_RUN" -eq 0 ]]; then
  if ! "$PYTHON" -m tools.databricks.provision_runtime_secrets --check \
      --scope "${MIP_RUNTIME_SECRET_SCOPE:-mip-runtime}"; then
    echo "${RED}[deploy] the runtime secret plan was refused before the App stop (see above).${RST}" >&2
    exit 2
  fi
else
  echo "[deploy] would check (read-only): tools.databricks.provision_runtime_secrets --check --scope ${MIP_RUNTIME_SECRET_SCOPE:-mip-runtime}"
fi
# <<< runtime secret pre-stop check <<<
