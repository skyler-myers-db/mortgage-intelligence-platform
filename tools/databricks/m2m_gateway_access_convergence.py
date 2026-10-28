"""Exact Gateway query-access convergence for M2M provisioning."""

from __future__ import annotations

from collections.abc import Callable, Collection
from typing import Any

from tools.databricks.install_scope import active_install_suffix, assert_verifier_revokes_owned


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
    install_warehouse_id: str | None = None,
) -> bool:
    """Grant the selected Gateway and revoke every non-preserved predecessor.

    In a suffixed install (the CI staging install) any revoke is refused, before
    the grant and with exit 2, unless the verifier is proven this install's own
    (``install_scope.assert_verifier_revokes_owned``); each revoke repeats it.
    """

    obsolete = set(revoke_gateway_endpoints)
    if gateway_endpoint:
        obsolete.update(reserved_gateway_endpoints(client))
    lease_id = str(deployment_lease_id or "").strip()
    source_git_sha = str(deployment_source_git_sha or "").strip()
    if (gateway_endpoint or obsolete) and (not lease_id or not source_git_sha):
        raise SystemExit(
            "Gateway access provisioning requires explicit deployment lease/source"
        )
    revokes = [
        endpoint
        for endpoint in sorted(obsolete.difference(preserved_gateway_endpoints))
        if endpoint and endpoint != gateway_endpoint
    ]
    suffix = active_install_suffix()
    if revokes and suffix:
        assert_verifier_revokes_owned(
            client, application_id=application_id, warehouse_id=install_warehouse_id, suffix=suffix
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
    for obsolete_endpoint in revokes:
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
            install_warehouse_id=install_warehouse_id,
        )
    return gateway_endpoint is not None
