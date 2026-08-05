"""App query-access convergence for the governed outer Gateway."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def converge_app_gateway_permissions(
    workspace: Any,
    *,
    gateway_endpoint: str,
    supervisor_endpoint: str,
    app_name: str,
    deployment_lease_id: str,
    deployment_source_git_sha: str,
    default_gateway_endpoint: str,
    legacy_gateway_endpoint: str,
    preserve_endpoints: tuple[str, ...],
    assert_single_writer: Callable[[], None],
    grant: Callable[..., None],
    revoke: Callable[..., bool],
    emit: Callable[[str], None],
) -> None:
    """Grant only the outer proxy and revoke historical direct bypasses."""

    app = workspace.apps.get(app_name)
    service_principal = str(
        getattr(app, "service_principal_client_id", None)
        or (app.get("service_principal_client_id") if isinstance(app, dict) else "")
        or ""
    ).strip()
    if not service_principal:
        raise RuntimeError(f"app service principal not found for {app_name!r}")
    grant(
        workspace,
        app_name=app_name,
        deployment_lease_id=deployment_lease_id,
        deployment_source_git_sha=deployment_source_git_sha,
        endpoint_name=gateway_endpoint,
        service_principal=service_principal,
        assert_single_writer=assert_single_writer,
    )
    emit(
        f"[agentic] granted CAN_QUERY on {gateway_endpoint} "
        f"to app service principal {service_principal}"
    )
    obsolete_endpoints = {
        supervisor_endpoint,
        default_gateway_endpoint,
        legacy_gateway_endpoint,
    }
    list_endpoints = getattr(getattr(workspace, "serving_endpoints", None), "list", None)
    if callable(list_endpoints):
        for item in list_endpoints():
            name = str(
                (
                    item.get("name")
                    if isinstance(item, dict)
                    else getattr(item, "name", "")
                )
                or ""
            ).strip()
            if name == default_gateway_endpoint or name.startswith(
                f"{default_gateway_endpoint}-"
            ):
                obsolete_endpoints.add(name)
    for obsolete_endpoint in obsolete_endpoints:
        if obsolete_endpoint == gateway_endpoint:
            continue
        if obsolete_endpoint in preserve_endpoints:
            emit(
                f"[agentic] preserved App ACL on blue endpoint {obsolete_endpoint} "
                "until green proof"
            )
            continue
        removed = revoke(
            workspace,
            app_name=app_name,
            endpoint_name=obsolete_endpoint,
            service_principal=service_principal,
            missing_ok=obsolete_endpoint != supervisor_endpoint,
            assert_single_writer=assert_single_writer,
        )
        emit(
            f"[agentic] {'revoked' if removed else 'verified absent'} direct App ACL "
            f"on obsolete endpoint {obsolete_endpoint}"
        )
