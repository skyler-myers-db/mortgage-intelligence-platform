"""Fail-safe v2-to-v3 Lakebase tombstone authority handoff."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from tools.databricks.lakebase_oauth_role_bootstrap import read_profile
from tools.databricks.lakebase_oauth_role_bootstrap_contract import (
    bootstrap_oauth_label_service_principal_id,
)
from tools.databricks.lakebase_oauth_role_bootstrap_principal import (
    assert_bootstrap_principal_contract,
)
from tools.databricks.lakebase_oauth_role_tombstone import (
    OrphanTombstone,
    orphan_tombstones,
    upgrade_v2_orphan_tombstone,
)


def _direct_original_principal_id(
    client: Any,
    account_client: Any,
    *,
    principal_id: str,
    application_id: str,
    bootstrap_reservation_name: str,
    ownership_marker: str,
) -> str:
    resolved_id, resolved_application_id = assert_bootstrap_principal_contract(
        client,
        SimpleNamespace(id=principal_id),
        display_name=bootstrap_reservation_name,
        external_id=ownership_marker,
        account_client=account_client,
    )
    if (resolved_id, resolved_application_id) != (principal_id, application_id):
        raise RuntimeError("temporary Lakebase v3 direct principal identity drifted")
    return resolved_id


def migrate_v2_tombstones_before_role_cleanup(
    client: Any,
    deployer_cursor: Any,
    account_client: Any,
    tombstones: list[OrphanTombstone],
    base_external_id: str,
    bootstrap_reservation_name: str,
    signing_key: str,
    principal_ids_by_application: dict[str, str],
    bootstrap_lock_cursor: Any | None,
    bootstrap_lock_key: Any | None,
    allow_unlocked_recovery_for_tests: bool,
) -> list[OrphanTombstone]:
    """Migrate every independently identified v2 marker before role deletion."""

    migrated: dict[str, str] = {}
    for v2 in (marker for marker in tombstones if marker[4] is None):
        application_id = v2[1]
        v3 = [
            marker for marker in tombstones if marker[1] == application_id and marker[4] is not None
        ]
        principal_id = principal_ids_by_application.get(application_id)
        if read_profile(deployer_cursor, application_id) is not None:
            label_id = bootstrap_oauth_label_service_principal_id(
                deployer_cursor,
                application_id,
            )
            if principal_id is not None and principal_id != label_id:
                raise RuntimeError(
                    "temporary Lakebase v2 migration principal conflicts with OAuth label"
                )
            principal_id = label_id
        if principal_id is None and len(v3) == 1:
            encoded_id = v3[0][4]
            if encoded_id is None:  # pragma: no cover - narrowed by the v3 predicate
                raise AssertionError("v3 tombstone principal id is missing")
            principal_id = _direct_original_principal_id(
                client,
                account_client,
                principal_id=encoded_id,
                application_id=application_id,
                bootstrap_reservation_name=bootstrap_reservation_name,
                ownership_marker=base_external_id,
            )
        if principal_id is None:
            if v3:
                raise RuntimeError(
                    "temporary Lakebase v2-to-v3 handoff lacks independent principal proof"
                )
            continue
        if any(marker[4] != principal_id for marker in v3):
            raise RuntimeError("temporary Lakebase v2 migration conflicts with signed v3 principal")
        upgrade_v2_orphan_tombstone(
            client,
            account_client=account_client,
            base_external_id=base_external_id,
            application_id=application_id,
            principal_id=principal_id,
            signing_key=signing_key,
            bootstrap_lock_cursor=bootstrap_lock_cursor,
            bootstrap_lock_key=bootstrap_lock_key,
            allow_unlocked_recovery_for_tests=allow_unlocked_recovery_for_tests,
        )
        migrated[application_id] = principal_id
    if not migrated:
        return tombstones
    refreshed = orphan_tombstones(
        client,
        base_external_id=base_external_id,
        account_client=account_client,
    )
    for application_id, principal_id in migrated.items():
        matches = [marker for marker in refreshed if marker[1] == application_id]
        if len(matches) != 1 or matches[0][4] != principal_id:
            raise RuntimeError("temporary Lakebase v3 migration postflight drifted")
    return refreshed
