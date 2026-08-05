"""Exact Gateway query-access convergence for M2M provisioning."""

from __future__ import annotations

from collections.abc import Callable, Collection
from typing import Any


def converge_gateway_query_access(
    client: Any,
    *,
    gateway_endpoint: str | None,
    revoke_gateway_endpoints: Collection[str],
    preserved_gateway_endpoints: Collection[str],
    app_name: str,
    deployment_lease_id: str | None,
    deployment_source_git_sha: str | None,
    application_id: str,
    service_principal_id: str,
    effective_group_names: set[str],
    assert_single_writer: Callable[[], None] | None,
    reserved_gateway_endpoints: Callable[[Any], Collection[str]],
    grant: Callable[..., None],
    revoke: Callable[..., None],
) -> bool:
    """Grant the selected Gateway and revoke every non-preserved predecessor."""

    obsolete = set(revoke_gateway_endpoints)
    if gateway_endpoint:
        obsolete.update(reserved_gateway_endpoints(client))
    lease_id = str(deployment_lease_id or "").strip()
    source_git_sha = str(deployment_source_git_sha or "").strip()
    if (gateway_endpoint or obsolete) and (not lease_id or not source_git_sha):
        raise SystemExit(
            "Gateway access provisioning requires explicit deployment lease/source"
        )
    if gateway_endpoint:
        grant(
            client,
            gateway_endpoint,
            application_id,
            app_name=app_name,
            deployment_lease_id=lease_id,
            deployment_source_git_sha=source_git_sha,
            sp_id=service_principal_id,
            effective_group_names=effective_group_names,
            assert_single_writer=assert_single_writer,
        )
    for obsolete_endpoint in sorted(obsolete.difference(preserved_gateway_endpoints)):
        if obsolete_endpoint and obsolete_endpoint != gateway_endpoint:
            revoke(
                client,
                obsolete_endpoint,
                application_id,
                app_name=app_name,
                deployment_lease_id=lease_id,
                deployment_source_git_sha=source_git_sha,
                sp_id=service_principal_id,
                effective_group_names=effective_group_names,
                assert_single_writer=assert_single_writer,
            )
    return gateway_endpoint is not None
