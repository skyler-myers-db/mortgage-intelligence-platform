"""Resume an authenticated stale cutover journal under signed-blue proof."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tools.databricks.app_gateway_access_mode import (
    app_service_principal_identity,
    assert_signed_blue_runtime_live,
    classify_cutover_journal_against_signed_blue,
    json_pin_from_env,
    revoke_managed_app_access,
)
from tools.databricks.cutover_journal_store import read_cutover_journal
from tools.databricks.cutover_supervisor_inventory import supervisor_by_id_direct
from tools.databricks.provision_agentic_resources import _run_no_json
from tools.databricks.retired_serving_query_groups import (
    delete_pinned_gateway,
    exact_service_principal_scim_id,
    retire_endpoint_query_groups,
    retire_pinned_supervisor,
)


def _endpoint_identity(workspace: Any, endpoint: str) -> tuple[str, str]:
    details = workspace.serving_endpoints.get(endpoint)
    endpoint_id = str(getattr(details, "id", None) or "").strip()
    creator = str(getattr(details, "creator", None) or "").strip()
    if not endpoint_id or not creator:
        raise RuntimeError("serving endpoint has no immutable id or creator")
    return endpoint_id, creator


def _gateway_pin(journal: dict[str, str]) -> dict[str, str] | None:
    if not journal.get("old_gateway_endpoint"):
        return None
    return {
        "name": journal["old_gateway_endpoint"],
        "endpoint_id": journal["old_gateway_endpoint_id"],
        "creator": journal["old_gateway_creator"],
    }


def _supervisor_pin(journal: dict[str, str]) -> dict[str, str] | None:
    if not journal.get("old_id"):
        return None
    return {
        "supervisor_id": journal["old_id"],
        "endpoint": journal["old_endpoint"],
        "endpoint_id": journal["old_endpoint_id"],
        "creator": journal["old_creator"],
    }


def resume_stale_journal_retirement(
    workspace: Any,
    *,
    runtime_application_id: str,
    app_name: str,
    app_application_id: str,
    verifier_application_id: str,
    verifier_scim_id: str,
    proxy_application_id: str,
    timeout_s: int,
    assert_single_writer: Callable[[], None],
) -> None:
    """Finish exact stale-journal retirement while signed blue remains live."""

    signed_blue_gateway_pin = json_pin_from_env(
        "MIP_CUTOVER_SIGNED_BLUE_GATEWAY_PIN_JSON"
    )
    signed_blue_supervisor_pin = json_pin_from_env(
        "MIP_CUTOVER_SIGNED_BLUE_SUPERVISOR_PIN_JSON"
    )
    if signed_blue_gateway_pin is None or signed_blue_supervisor_pin is None:
        raise RuntimeError("stale-journal recovery requires both signed-blue runtime pins")
    journal = read_cutover_journal(
        workspace,
        runtime_application_id=runtime_application_id,
    )
    if journal is None:
        raise RuntimeError("stale-journal recovery requires an authenticated cutover journal")
    relation = classify_cutover_journal_against_signed_blue(
        journal_gateway_pin=_gateway_pin(journal),
        journal_supervisor_pin=_supervisor_pin(journal),
        signed_blue_gateway_pin=signed_blue_gateway_pin,
        signed_blue_supervisor_pin=signed_blue_supervisor_pin,
    )
    if relation != "stale":
        raise RuntimeError("cutover journal does not describe a stale signed-blue predecessor")

    app_principal, app_principal_id = app_service_principal_identity(
        workspace,
        app_name=app_name,
    )
    applications = (
        app_application_id.strip(),
        verifier_application_id.strip(),
        proxy_application_id.strip(),
    )
    verifier_scim = verifier_scim_id.strip()
    if not all(applications) or not verifier_scim:
        raise ValueError("complete App, verifier, and proxy identities are required")
    if app_principal != applications[0]:
        raise RuntimeError("live App service-principal application ID drifted")
    if len(set(applications)) != 3:
        raise RuntimeError("App, verifier, and proxy applications must be distinct")
    exact_verifier_scim = exact_service_principal_scim_id(
        workspace,
        application_id=applications[1],
    )
    if exact_verifier_scim != verifier_scim:
        raise RuntimeError("live verifier service-principal SCIM ID drifted")
    proxy_scim_id = exact_service_principal_scim_id(
        workspace,
        application_id=applications[2],
    )
    if len({app_principal_id, verifier_scim, proxy_scim_id}) != 3:
        raise RuntimeError("App, verifier, and proxy SCIM identities must be distinct")

    def assert_recovery_boundary() -> None:
        assert_single_writer()
        current = read_cutover_journal(
            workspace,
            runtime_application_id=runtime_application_id,
        )
        if current != journal:
            raise RuntimeError("cutover journal changed during stale retirement recovery")
        assert_signed_blue_runtime_live(
            workspace,
            runtime_application_id=runtime_application_id,
            signed_blue_gateway_pin=signed_blue_gateway_pin,
            signed_blue_supervisor_pin=signed_blue_supervisor_pin,
            supervisor_by_id=lambda supervisor_id: supervisor_by_id_direct(
                workspace,
                supervisor_id,
            ),
        )

    assert_recovery_boundary()
    gateway_pin = _gateway_pin(journal)
    supervisor_pin = _supervisor_pin(journal)
    delete_pinned_gateway(
        workspace,
        app_name=app_name,
        endpoint=(gateway_pin or {}).get("name"),
        endpoint_id=(gateway_pin or {}).get("endpoint_id"),
        creator=(gateway_pin or {}).get("creator"),
        delete_allowed=journal.get("old_gateway_delete_allowed") == "1",
        green_endpoint=str(signed_blue_gateway_pin["name"]).strip(),
        runtime_application_id=runtime_application_id,
        app_principal=app_principal,
        app_principal_id=app_principal_id,
        verifier_application_id=applications[1],
        verifier_scim_id=verifier_scim,
        timeout_s=timeout_s,
        assert_single_writer=assert_recovery_boundary,
        endpoint_identity=_endpoint_identity,
        revoke_app_access=revoke_managed_app_access,
        retire_query_groups=retire_endpoint_query_groups,
    )
    retire_pinned_supervisor(
        workspace,
        app_name=app_name,
        canonical_name=journal["canonical_name"],
        old_id=(supervisor_pin or {}).get("supervisor_id"),
        old_endpoint=(supervisor_pin or {}).get("endpoint"),
        old_endpoint_id=(supervisor_pin or {}).get("endpoint_id"),
        old_creator=(supervisor_pin or {}).get("creator"),
        old_create_time=journal.get("old_create_time"),
        app_principal=app_principal,
        app_principal_id=app_principal_id,
        proxy_application_id=applications[2],
        proxy_scim_id=proxy_scim_id,
        cleanup_enabled=True,
        timeout_s=timeout_s,
        assert_single_writer=assert_recovery_boundary,
        agent_by_id=lambda supervisor_id: supervisor_by_id_direct(
            workspace,
            supervisor_id,
        ),
        endpoint_identity=_endpoint_identity,
        revoke_app_access=revoke_managed_app_access,
        delete_agent=_run_no_json,
        retire_query_groups=retire_endpoint_query_groups,
    )


def resume_stale_journal_from_args(
    workspace: Any,
    args: Any,
    assert_single_writer: Callable[[], None] | None,
) -> None:
    """Dispatch the recovery-only CLI arguments without bloating the main cutover module."""

    if assert_single_writer is None:
        raise RuntimeError("stale-journal recovery requires the deployment lease")
    resume_stale_journal_retirement(
        workspace,
        runtime_application_id=args.runtime_application_id,
        app_name=args.app_name,
        app_application_id=args.app_application_id,
        verifier_application_id=args.verifier_application_id,
        verifier_scim_id=args.verifier_scim_id,
        proxy_application_id=args.proxy_application_id,
        timeout_s=args.timeout_s,
        assert_single_writer=assert_single_writer,
    )
