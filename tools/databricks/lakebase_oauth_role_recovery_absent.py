"""Read-only recovery boundary when a Lakebase instance is absent."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from databricks.sdk.errors import NotFound
from tools.databricks.lakebase_oauth_role_tombstone import orphan_tombstones

_ABSENCE_STABILITY_SECONDS = 30.0
_ABSENCE_POLL_SECONDS = 1.0


def recover_absent_instance_principals(
    client: Any,
    *,
    account_client: Any,
    instance_name: str,
    database_name: str,
    target_application_id: str,
    marker_signing_key: str | None = None,
    resource_absence_probe: Callable[[], bool] | None = None,
    monotonic: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> bool:
    """Return true only for stable absence with no recoverable identity state.

    An absent database cannot provide the canonical advisory lock or SQL role
    inventory. This path is therefore deliberately read-only: it never revokes
    credentials, retires principals, upgrades markers, or deletes tombstones.
    Any residual signed identity is retained as recovery authority and blocks
    until an existing instance database can prove and delete its SQL role.
    """

    from tools.databricks.lakebase_oauth_role_recovery import (
        _assert_bootstrap_principal_contract,
        _bootstrap_identity_contract,
        _exact_bootstrap_principals,
        _marker_signing_key,
    )

    display_name, external_id = _bootstrap_identity_contract(
        instance_name=instance_name,
        database_name=database_name,
        application_id=target_application_id,
    )
    # Preserve the same signed-marker configuration preflight as locked
    # recovery. Tombstone decoding itself uses the configured verify-key ring.
    marker_signing_key = marker_signing_key or _marker_signing_key()
    if not marker_signing_key:
        raise RuntimeError("temporary Lakebase marker signing key is unavailable")
    clock = monotonic or time.monotonic
    pause = sleep or time.sleep

    def resource_is_absent() -> bool:
        if resource_absence_probe is not None:
            return resource_absence_probe()
        try:
            observed = client.database.get_database_instance(instance_name)
        except NotFound:
            return True
        observed_name = str(getattr(observed, "name", "") or "").strip()
        if observed_name != instance_name:
            raise RuntimeError("Lakebase direct instance GET changed identities")
        return False

    stable_since = clock()
    while True:
        if not resource_is_absent():
            return False
        now = clock()
        remaining = _ABSENCE_STABILITY_SECONDS - (now - stable_since)
        if remaining <= 0:
            break
        pause(min(_ABSENCE_POLL_SECONDS, remaining))

    principals = _exact_bootstrap_principals(
        client,
        display_name=display_name,
        external_id=external_id,
        account_client=account_client,
    )
    resolved = [
        _assert_bootstrap_principal_contract(
            client,
            principal,
            display_name=display_name,
            external_id=external_id,
            account_client=account_client,
        )
        for principal in principals
    ]
    if any(application_id == target_application_id for _, application_id in resolved):
        raise RuntimeError("target runtime identity is never a bootstrap principal")

    tombstones = orphan_tombstones(
        client,
        base_external_id=external_id,
        account_client=account_client,
    )
    if any(application_id == target_application_id for _, application_id, *_ in tombstones):
        raise RuntimeError("target runtime identity is never a bootstrap role")
    if not resource_is_absent():
        return False
    if resolved or tombstones:
        raise RuntimeError(
            "absent-instance Lakebase bootstrap state was retained because unlocked "
            "recovery cannot prove SQL role absence; restore a reviewed instance "
            "connection and run canonical locked recovery"
        )
    return True


def commented_bootstrap_roles(cursor: Any, external_id: str) -> list[str]:
    cursor.execute(
        """
        SELECT role.rolname
        FROM pg_roles role
        WHERE shobj_description(role.oid, 'pg_authid') = %s
        ORDER BY role.rolname
        """,
        (external_id,),
    )
    return [str(row[0]) for row in cursor.fetchall()]
