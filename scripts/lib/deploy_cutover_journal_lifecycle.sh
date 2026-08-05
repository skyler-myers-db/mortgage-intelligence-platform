# shellcheck shell=bash
# Durable cutover-journal classification, retry admission, and retirement.
# Sourced by scripts/deploy.sh; this file must not change shell options or traps.

plan_historical_cutover_journal_preservation() {
  "$PYTHON" - \
    "${MIP_REPLACED_AGENT_GATEWAY_PIN_JSON:-}" \
    "${MIP_REPLACED_AGENT_SUPERVISOR_PIN_JSON:-}" \
    "${MIP_APP_ROLLBACK_GATEWAY_PIN_JSON:-}" \
    "${MIP_APP_ROLLBACK_SUPERVISOR_PIN_JSON:-}" <<'PYEOF'
import json
import sys

from tools.databricks.app_gateway_access_mode import (
    classify_cutover_journal_against_signed_blue,
)

old_gateway, old_supervisor, blue_gateway, blue_supervisor = (
    json.loads(value) if value else None for value in sys.argv[1:]
)
if blue_gateway is None or blue_supervisor is None:
    raise RuntimeError("signed-blue runtime pins are required for journal classification")
relation = classify_cutover_journal_against_signed_blue(
    journal_gateway_pin=old_gateway,
    journal_supervisor_pin=old_supervisor,
    signed_blue_gateway_pin=blue_gateway,
    signed_blue_supervisor_pin=blue_supervisor,
)


def immutable_tuple(value, fields):
    if value is None:
        return None
    return tuple(str(value.get(field) or "").strip() for field in fields)


gateway_fields = ("name", "endpoint_id", "creator")
supervisor_fields = ("supervisor_id", "endpoint", "endpoint_id", "creator")
preserve_journal_gateway = (
    old_gateway is not None
    and immutable_tuple(old_gateway, gateway_fields)
    != immutable_tuple(blue_gateway, gateway_fields)
)
preserve_journal_supervisor = (
    old_supervisor is not None
    and immutable_tuple(old_supervisor, supervisor_fields)
    != immutable_tuple(blue_supervisor, supervisor_fields)
)
print(
    "\t".join(
        (
            relation,
            "1" if preserve_journal_gateway else "0",
            "1" if preserve_journal_supervisor else "0",
        )
    )
)
PYEOF
}

merge_historical_cutover_journal_preservation() {
  local relation preserve_gateway preserve_supervisor
  IFS=$'\t' read -r relation preserve_gateway preserve_supervisor \
    < <(plan_historical_cutover_journal_preservation) || return 1
  case "$relation" in
    current)
      if [[ "$preserve_gateway" == "1" ]]; then
        HISTORICAL_ENDPOINT_PRESERVE_ARGS+=(
          --preserve-retirement-gateway-json \
          "$MIP_REPLACED_AGENT_GATEWAY_PIN_JSON"
        )
      fi
      if [[ "$preserve_supervisor" == "1" ]]; then
        HISTORICAL_ENDPOINT_PRESERVE_ARGS+=(
          --preserve-retirement-supervisor-json \
          "$MIP_REPLACED_AGENT_SUPERVISOR_PIN_JSON"
        )
      fi
      ;;
    stale)
      STALE_CUTOVER_JOURNAL_PENDING=1
      ;;
    absent)
      ;;
    *)
      echo "${RED}[deploy] cutover journal classification is invalid.${RST}" >&2
      return 1
      ;;
  esac
}

journal_preactivation_app_acl_endpoint() {
  local endpoint="${1:-}" existing
  [[ -n "$endpoint" ]] || return 0
  if [[ "${APP_UPGRADE_STATE:-first_install}" != "blue_active" || \
        "${APP_SIGNED_BLUE_AVAILABLE:-0}" -ne 1 ]]; then
    return 0
  fi
  if [[ "$endpoint" == "${MIP_APP_ROLLBACK_GATEWAY_ENDPOINT:-}" || \
        "$endpoint" == "${MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT:-}" ]]; then
    return 0
  fi
  for existing in "${PREACTIVATION_APP_REVOKE_ENDPOINTS[@]}"; do
    [[ "$existing" == "$endpoint" ]] && return 0
  done
  PREACTIVATION_APP_REVOKE_ENDPOINTS+=("$endpoint")
  PREACTIVATION_APP_ACL_MUTATED=1
}

reconcile_retry_supervisor_app_acl() {
  [[ "$DRY_RUN" -eq 0 && "${APP_UPGRADE_STATE:-first_install}" == "blue_active" && \
     "${APP_SIGNED_BLUE_AVAILABLE:-0}" -eq 1 ]] || return 0
  local plan_file endpoint endpoint_id creator
  plan_file="$(mktemp -t mip-supervisor-app-acl-retry.XXXXXX.tsv)"
  if ! "$PYTHON" - \
    "$_GRANTS_APP_NAME" \
    "$MIP_APP_DEPLOYMENT_LEASE_ID" \
    "$SOURCE_GIT_SHA" \
    "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
    "$MIP_APP_ROLLBACK_SUPERVISOR_ID" \
    "$MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT" \
    "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}" \
    "${MIP_DEFAULT_CATALOG:-mip}" \
    "${MIP_AGENT_SUPERVISOR_NAME:-Mortgage Growth Agent}" \
    "$plan_file" <<'PYEOF'
import sys
from pathlib import Path

from databricks.sdk import WorkspaceClient
from tools.databricks import app_deployment_lease
from tools.databricks.agent_runtime_access import assert_runtime_creator
from tools.databricks.agentic_supervisor_endpoint import supervisor_candidates
from tools.databricks.app_gateway_access_mode import (
    app_service_principal_identity,
    inspect_app_gateway_access_mode,
)
from tools.databricks.provision_agentic_resources import _supervisor_agents

(
    app_name,
    lease_id,
    source_sha,
    runtime_id,
    blue_id,
    blue_endpoint,
    genie_space_id,
    catalog,
    supervisor_name,
    out_path,
) = sys.argv[1:]
workspace = WorkspaceClient()
lease = app_deployment_lease.held_assertion(
    workspace,
    app_name=app_name,
    lease_id=lease_id,
    source_git_sha=source_sha,
)
lease()
app_client_id, app_scim_id = app_service_principal_identity(
    workspace,
    app_name=app_name,
)
candidates = supervisor_candidates(
    _supervisor_agents(),
    display_name=supervisor_name,
    genie_space_id=genie_space_id,
    catalog=catalog,
)
rows = []
for candidate in (
    candidates.canonical,
    candidates.replacement,
    candidates.managed_query_replacement,
    candidates.legacy_replacement,
):
    if candidate is None:
        continue
    candidate_id = str(candidate.get("supervisor_agent_id") or "").strip()
    endpoint = str(candidate.get("endpoint_name") or "").strip()
    is_blue = (candidate_id, endpoint) == (blue_id, blue_endpoint)
    if not is_blue and (candidate_id == blue_id or endpoint == blue_endpoint):
        raise RuntimeError("signed-blue Supervisor immutable tuple drifted")
    if not candidate_id or not endpoint:
        raise RuntimeError("reserved Supervisor candidate has an incomplete immutable tuple")
    assert_runtime_creator(
        candidate.get("creator"),
        application_id=runtime_id,
        resource="retry Supervisor candidate",
    )
    details = workspace.serving_endpoints.get(endpoint)
    endpoint_id = str(getattr(details, "id", "") or "").strip()
    creator = str(getattr(details, "creator", "") or "").strip()
    if not endpoint_id:
        raise RuntimeError("retry Supervisor endpoint has no immutable ID")
    assert_runtime_creator(
        creator,
        application_id=runtime_id,
        resource="retry Supervisor endpoint",
    )
    mode = inspect_app_gateway_access_mode(
        workspace,
        app_name=app_name,
        endpoint_name=endpoint,
        app_client_id=app_client_id,
        app_scim_id=app_scim_id,
        legacy_pinned=is_blue,
    )
    if mode in {"legacy", "mixed"}:
        if is_blue:
            continue
        raise RuntimeError("non-blue reserved Supervisor retains non-atomic legacy App access")
    if mode == "managed":
        rows.append((endpoint, endpoint_id, creator))
Path(out_path).write_text(
    "".join("\t".join(row) + "\n" for row in rows),
    encoding="utf-8",
)
PYEOF
  then
    rm -f "$plan_file"
    return 1
  fi
  while IFS=$'\t' read -r endpoint endpoint_id creator; do
    [[ -n "$endpoint" && -n "$endpoint_id" && -n "$creator" ]] || {
      rm -f "$plan_file"
      echo "${RED}[deploy] retry Supervisor ACL plan is incomplete.${RST}" >&2
      return 1
    }
    journal_preactivation_app_acl_endpoint "$endpoint"
  done < "$plan_file"
  if [[ ! -s "$plan_file" ]]; then
    rm -f "$plan_file"
    return 0
  fi
  if ! "$PYTHON" - \
    "$_GRANTS_APP_NAME" \
    "$MIP_APP_DEPLOYMENT_LEASE_ID" \
    "$SOURCE_GIT_SHA" \
    "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
    "$MIP_APP_ROLLBACK_SUPERVISOR_ID" \
    "$MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT" \
    "$plan_file" <<'PYEOF'
import sys
from pathlib import Path

from databricks.sdk import WorkspaceClient
from tools.databricks import app_deployment_lease
from tools.databricks.agent_runtime_access import assert_runtime_creator
from tools.databricks.app_gateway_access_mode import (
    app_service_principal_identity,
    inspect_app_gateway_access_mode,
    revoke_managed_app_access,
)

app_name, lease_id, source_sha, runtime_id, blue_id, blue_endpoint, plan_path = sys.argv[1:]
workspace = WorkspaceClient()
lease = app_deployment_lease.held_assertion(
    workspace,
    app_name=app_name,
    lease_id=lease_id,
    source_git_sha=source_sha,
)
lease()
app_client_id, app_scim_id = app_service_principal_identity(
    workspace,
    app_name=app_name,
)
for raw in Path(plan_path).read_text(encoding="utf-8").splitlines():
    endpoint, endpoint_id, creator = raw.split("\t")
    details = workspace.serving_endpoints.get(endpoint)
    actual = (
        str(getattr(details, "id", "") or "").strip(),
        str(getattr(details, "creator", "") or "").strip(),
    )
    if actual != (endpoint_id, creator):
        raise RuntimeError("retry Supervisor endpoint identity changed before mutation")
    assert_runtime_creator(
        creator,
        application_id=runtime_id,
        resource="retry Supervisor endpoint",
    )
    mode = inspect_app_gateway_access_mode(
        workspace,
        app_name=app_name,
        endpoint_name=endpoint,
        app_client_id=app_client_id,
        app_scim_id=app_scim_id,
        legacy_pinned=endpoint == blue_endpoint,
    )
    if mode in {"legacy", "mixed"}:
        if endpoint == blue_endpoint:
            raise RuntimeError("signed-blue Supervisor gained non-atomic App access")
        raise RuntimeError("non-blue reserved Supervisor gained non-atomic legacy App access")
    if mode == "managed":
        revoke_managed_app_access(
            workspace,
            app_name=app_name,
            endpoint_name=endpoint,
            app_client_id=app_client_id,
            app_scim_id=app_scim_id,
            missing_ok=False,
            assert_before_mutation=lease,
        )
PYEOF
  then
    rm -f "$plan_file"
    return 1
  fi
  rm -f "$plan_file"
}

refresh_captured_cutover_journal() {
  [[ "${APP_UPGRADE_STATE:-first_install}" == \
     "green_captured_cleanup_pending" ]] || return 1
  local journal_env
  journal_env="$(mktemp -t mip-captured-cutover.XXXXXX.env)"
  if ! run_as_m2m_identity \
    agent-runtime \
    DATABRICKS_AGENT_RUNTIME_CLIENT_ID \
    DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET \
    "$PYTHON" -m tools.databricks.cutover_agent_runtime_supervisor export-journal \
    --runtime-application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
    --out-env "$journal_env"; then
    rm -f "$journal_env"
    return 1
  fi
  unset \
    MIP_REPLACED_AGENT_SUPERVISOR_ID \
    MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT \
    MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT_ID \
    MIP_REPLACED_AGENT_SUPERVISOR_CREATOR \
    MIP_REPLACED_AGENT_SUPERVISOR_CREATE_TIME \
    MIP_REPLACED_AGENT_SUPERVISOR_PIN_JSON \
    MIP_REPLACED_AGENT_GATEWAY_ENDPOINT \
    MIP_REPLACED_AGENT_GATEWAY_ENDPOINT_ID \
    MIP_REPLACED_AGENT_GATEWAY_CREATOR \
    MIP_REPLACED_AGENT_GATEWAY_DELETE_ALLOWED \
    MIP_REPLACED_AGENT_GATEWAY_PIN_JSON
  set -a
  # shellcheck disable=SC1090
  . "$journal_env"
  set +a
  rm -f "$journal_env"
}

pinned_serving_endpoint_status() {
  local endpoint="${1:?endpoint is required}"
  local endpoint_id="${2:?endpoint ID is required}"
  local creator="${3:?endpoint creator is required}"
  "$PYTHON" - "$endpoint" "$endpoint_id" "$creator" <<'PYEOF'
import sys

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound, ResourceDoesNotExist

endpoint, expected_id, expected_creator = sys.argv[1:]
try:
    details = WorkspaceClient().serving_endpoints.get(endpoint)
except (NotFound, ResourceDoesNotExist):
    raise SystemExit(3)
actual = (
    str(getattr(details, "id", "") or "").strip(),
    str(getattr(details, "creator", "") or "").strip(),
)
if actual != (expected_id, expected_creator):
    raise RuntimeError("signed cutover endpoint immutable identity drifted")
PYEOF
}

pinned_query_access_mode() {
  local application_id="${1:?application ID is required}"
  local endpoint="${2:?endpoint is required}"
  "$PYTHON" - "$_GRANTS_APP_NAME" "$application_id" "$endpoint" <<'PYEOF'
import sys

from databricks.sdk import WorkspaceClient
from tools.databricks.serving_endpoint_acl import inspect_exact_query_access_mode

app_name, application_id, endpoint = sys.argv[1:]
print(
    inspect_exact_query_access_mode(
        WorkspaceClient(),
        app_name=app_name,
        endpoint_name=endpoint,
        service_principal=application_id,
        legacy_pinned=True,
    )
)
PYEOF
}

load_captured_live_old_resources() {
  CAPTURED_OLD_GATEWAY_LIVE=0
  CAPTURED_OLD_SUPERVISOR_LIVE=0
  refresh_captured_cutover_journal || return 1
  local status=0
  if [[ -n "${MIP_REPLACED_AGENT_GATEWAY_ENDPOINT:-}" ]]; then
    if pinned_serving_endpoint_status \
      "$MIP_REPLACED_AGENT_GATEWAY_ENDPOINT" \
      "$MIP_REPLACED_AGENT_GATEWAY_ENDPOINT_ID" \
      "$MIP_REPLACED_AGENT_GATEWAY_CREATOR"; then
      CAPTURED_OLD_GATEWAY_LIVE=1
    else
      status=$?
      [[ "$status" -eq 3 ]] || return "$status"
    fi
  fi
  if [[ -n "${MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT:-}" ]]; then
    if pinned_serving_endpoint_status \
      "$MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT" \
      "$MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT_ID" \
      "$MIP_REPLACED_AGENT_SUPERVISOR_CREATOR"; then
      CAPTURED_OLD_SUPERVISOR_LIVE=1
    else
      status=$?
      [[ "$status" -eq 3 ]] || return "$status"
    fi
  fi
}

classify_journaled_old_supervisor_app_access() {
  OLD_SUPERVISOR_APP_ACCESS_MODE="none"
  [[ -n "${MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT:-}" ]] || return 0
  local status=0
  if pinned_serving_endpoint_status \
    "$MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT" \
    "$MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT_ID" \
    "$MIP_REPLACED_AGENT_SUPERVISOR_CREATOR"; then
    :
  else
    status=$?
    [[ "$status" -eq 3 ]] && return 0
    return "$status"
  fi
  OLD_SUPERVISOR_APP_ACCESS_MODE="$(pinned_query_access_mode \
    "$APP_SP_CLIENT_ID" \
    "$MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT")" || return 1
  if [[ "$OLD_SUPERVISOR_APP_ACCESS_MODE" == "managed" ]]; then
    echo "${RED}[deploy] journaled old Supervisor retained managed App access after retry reconciliation.${RST}" >&2
    return 1
  fi
}

resume_captured_runtime_retirement() {
  [[ "${APP_UPGRADE_STATE:-first_install}" == \
     "green_captured_cleanup_pending" ]] || return 1
  refresh_captured_cutover_journal || return 1
  if [[ -z "${MIP_REPLACED_AGENT_SUPERVISOR_ID:-}" && \
        -z "${MIP_REPLACED_AGENT_GATEWAY_ENDPOINT:-}" ]]; then
    CAPTURED_RUNTIME_RETIREMENT_COMPLETE=1
    return 0
  fi
  if ! declare -p AGENT_RUNTIME_GREEN_ARGS >/dev/null 2>&1; then
    echo "${RED}[deploy] captured cleanup lacks its exact green runtime contract.${RST}" >&2
    return 1
  fi
  local -a retire_args=(
    -m tools.databricks.cutover_agent_runtime_supervisor retire
    "${AGENT_RUNTIME_GREEN_ARGS[@]}"
    --verifier-application-id "$DATABRICKS_VERIFIER_CLIENT_ID"
    --verifier-scim-id "$MIP_VERIFIER_SCIM_ID"
    --proxy-application-id "$DATABRICKS_AGENT_PROXY_CLIENT_ID"
  )
  if [[ -n "${MIP_REPLACED_AGENT_SUPERVISOR_ID:-}" ]]; then
    retire_args+=(
      --old-id "$MIP_REPLACED_AGENT_SUPERVISOR_ID"
      --old-endpoint "$MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT"
      --old-endpoint-id "$MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT_ID"
      --old-creator "$MIP_REPLACED_AGENT_SUPERVISOR_CREATOR"
      --old-create-time "$MIP_REPLACED_AGENT_SUPERVISOR_CREATE_TIME"
    )
  fi
  if [[ -n "${MIP_REPLACED_AGENT_GATEWAY_ENDPOINT:-}" ]]; then
    retire_args+=(
      --old-gateway-endpoint "$MIP_REPLACED_AGENT_GATEWAY_ENDPOINT"
      --old-gateway-endpoint-id "$MIP_REPLACED_AGENT_GATEWAY_ENDPOINT_ID"
      --old-gateway-creator "$MIP_REPLACED_AGENT_GATEWAY_CREATOR"
    )
    if [[ "${MIP_REPLACED_AGENT_GATEWAY_DELETE_ALLOWED:-0}" == "1" ]]; then
      retire_args+=(--old-gateway-delete-allowed)
    fi
  fi
  "$PYTHON" "${retire_args[@]}" || return 1
  run_as_m2m_identity \
    agent-runtime \
    DATABRICKS_AGENT_RUNTIME_CLIENT_ID \
    DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET \
    "$PYTHON" -m tools.databricks.cutover_agent_runtime_supervisor finalize \
    --replacement-id "$MIP_AGENT_SUPERVISOR_ID" \
    --replacement-endpoint "$MIP_AGENT_SUPERVISOR_ENDPOINT" \
    --runtime-application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
    --app-name "$_GRANTS_APP_NAME" \
    --deployment-lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
    --deployment-source-git-sha "$SOURCE_GIT_SHA" \
    --catalog "${MIP_DEFAULT_CATALOG:-mip}" \
    --genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}" || return 1
  CAPTURED_RUNTIME_RETIREMENT_COMPLETE=1
}

complete_captured_runtime_retirement_journal() {
  [[ "${APP_UPGRADE_STATE:-first_install}" == \
     "green_captured_cleanup_pending" ]] || return 0
  if [[ "$CAPTURED_APP_BOUNDARY_PROVEN" -ne 1 || \
        "$CAPTURED_PROXY_BOUNDARY_PROVEN" -ne 1 || \
        "$CAPTURED_VERIFIER_BOUNDARY_PROVEN" -ne 1 ]]; then
    echo "${RED}[deploy] captured cleanup lacks all three pre-retirement boundary proofs.${RST}" >&2
    return 1
  fi
  if ! resume_captured_runtime_retirement; then
    # A provider may have committed only part of the authenticated retirement.
    # Re-read the journal and prove the resulting survivor set without granting
    # any old access. Green remains signed and live; the journal stays for retry.
    CAPTURED_APP_BOUNDARY_PROVEN=0
    CAPTURED_PROXY_BOUNDARY_PROVEN=0
    CAPTURED_VERIFIER_BOUNDARY_PROVEN=0
    converge_green_only_app_access || return 1
    compensate_agent_proxy_access || return 1
    compensate_verifier_gateway_access || return 1
    [[ "$CAPTURED_APP_BOUNDARY_PROVEN" -eq 1 && \
       "$CAPTURED_PROXY_BOUNDARY_PROVEN" -eq 1 && \
       "$CAPTURED_VERIFIER_BOUNDARY_PROVEN" -eq 1 ]]
    return $?
  fi
  CAPTURED_APP_BOUNDARY_PROVEN=0
  CAPTURED_PROXY_BOUNDARY_PROVEN=0
  CAPTURED_VERIFIER_BOUNDARY_PROVEN=0
  converge_green_only_app_access || return 1
  compensate_agent_proxy_access || return 1
  compensate_verifier_gateway_access || return 1
  local credential_id
  local -a retirement_args=()
  if [[ -n "${MIP_APP_ROLLBACK_PROXY_CREDENTIAL_IDS:-}" ]]; then
    local -a credential_ids=()
    IFS=',' read -r -a credential_ids \
      <<< "$MIP_APP_ROLLBACK_PROXY_CREDENTIAL_IDS"
    for credential_id in "${credential_ids[@]}"; do
      if [[ ! "$credential_id" =~ ^[A-Za-z0-9._-]{1,128}$ ]]; then
        echo "${RED}[deploy] captured proxy retirement credential ID is invalid.${RST}" >&2
        return 1
      fi
      retirement_args+=(--signed-blue-credential-id "$credential_id")
    done
  fi
  run_with_proof_signing_authority \
    run_with_agent_proxy_binding \
      "$PYTHON" -m tools.databricks.provision_agent_proxy_secret \
    --app-name "$_GRANTS_APP_NAME" \
    --scope "$MIP_AGENT_PROXY_SECRET_SCOPE" \
    --rollback-scope "$APP_ROLLBACK_SECRET_SCOPE" \
    --runtime-application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
    --cleanup-signed-blue \
    "${retirement_args[@]}" || return 1
  CAPTURED_PROXY_BOUNDARY_PROVEN=0
  compensate_agent_proxy_access || return 1
  run_as_m2m_identity \
    agent-runtime \
    DATABRICKS_AGENT_RUNTIME_CLIENT_ID \
    DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET \
    "$PYTHON" -m tools.databricks.cutover_agent_runtime_supervisor clear-journal \
    --runtime-application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
    --app-application-id "$APP_SP_CLIENT_ID" \
    --app-scim-id "$APP_SP_SCIM_ID" \
    --verifier-application-id "$DATABRICKS_VERIFIER_CLIENT_ID" \
    --verifier-scim-id "$MIP_VERIFIER_SCIM_ID" \
    --proxy-application-id "$DATABRICKS_AGENT_PROXY_CLIENT_ID" \
    --app-name "$_GRANTS_APP_NAME" \
    --deployment-lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
    --deployment-source-git-sha "$SOURCE_GIT_SHA" || return 1
  CAPTURED_RUNTIME_RETIREMENT_COMPLETE=0
  AGENT_PROXY_ACCESS_MUTATED=0
  VERIFIER_GATEWAY_CUTOVER_MUTATED=0
  APP_UPGRADE_STATE="green_verified"
}
