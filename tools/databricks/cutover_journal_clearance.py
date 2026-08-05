"""Exact cutover-journal clearance command wiring."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tools.databricks.app_gateway_access_mode import clear_stale_aware_cutover_journal
from tools.databricks.cutover_supervisor_inventory import (
    supervisor_by_id_direct,
    supervisor_inventory_direct,
)


def clear_journal(
    workspace: Any,
    *,
    app_name: str,
    runtime_application_id: str,
    app_application_id: str,
    app_scim_id: str,
    verifier_application_id: str,
    verifier_scim_id: str,
    proxy_application_id: str,
    assert_single_writer: Callable[[], None],
) -> None:
    """Clear only after exact endpoint, Supervisor, and group retirement proof."""

    clear_stale_aware_cutover_journal(
        workspace,
        app_name=app_name,
        runtime_application_id=runtime_application_id,
        assert_single_writer=assert_single_writer,
        supervisor_by_id=lambda supervisor_id: supervisor_by_id_direct(
            workspace,
            supervisor_id,
        ),
        supervisor_inventory=lambda: supervisor_inventory_direct(workspace),
        app_application_id=app_application_id,
        app_scim_id=app_scim_id,
        verifier_application_id=verifier_application_id,
        verifier_scim_id=verifier_scim_id,
        proxy_application_id=proxy_application_id,
    )
