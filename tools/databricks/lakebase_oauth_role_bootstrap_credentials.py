"""Eventually consistent credential quarantine for one-use bootstrap principals."""

from __future__ import annotations

import time
from contextlib import suppress
from typing import Any

from databricks.sdk.errors import NotFound
from tools.databricks.lakebase_oauth_role_bootstrap_sessions import (
    terminate_bootstrap_sessions,
)
from tools.databricks.lakebase_oauth_role_scim_marker import (
    assert_bootstrap_principal_display_name,
    assert_scim_external_id_unset,
)


def assert_workspace_mutation_lease(
    bootstrap_lock_cursor: Any | None,
    bootstrap_lock_key: Any | None,
    *,
    allow_unlocked_recovery_for_tests: bool,
) -> None:
    if bootstrap_lock_cursor is not None and bootstrap_lock_key is not None:
        from tools.databricks.lakebase_oauth_role_bootstrap_lock import (
            assert_bootstrap_lock_held,
        )

        assert_bootstrap_lock_held(
            bootstrap_lock_cursor,
            lock_key=bootstrap_lock_key,
        )
    elif not allow_unlocked_recovery_for_tests:
        raise RuntimeError("workspace recovery mutation lacks canonical advisory lock")


def disable_and_revoke_bootstrap_credentials(
    client: Any,
    *,
    service_principal_id: str,
    attempts: int = 15,
    bootstrap_lock_cursor: Any | None = None,
    bootstrap_lock_key: Any | None = None,
    allow_unlocked_recovery: bool = True,
    expected_immutable_contract: tuple[str, str, str, str] | None = None,
    expected_absent_identity: tuple[str, str] | None = None,
    allow_secret_proxy_not_found: bool = False,
) -> None:
    def assert_mutation_lease() -> None:
        if bootstrap_lock_cursor is not None and bootstrap_lock_key is not None:
            from tools.databricks.lakebase_oauth_role_bootstrap_lock import (
                assert_bootstrap_lock_held,
            )

            assert_bootstrap_lock_held(
                bootstrap_lock_cursor,
                lock_key=bootstrap_lock_key,
            )
        elif not allow_unlocked_recovery:
            raise RuntimeError("bootstrap credential mutation lacks canonical advisory lock")

    if expected_immutable_contract is not None and expected_absent_identity is not None:
        raise ValueError("only one bootstrap credential identity contract may be supplied")
    immutable_contract: tuple[str, ...] | None = expected_immutable_contract
    if immutable_contract is not None and (
        len(immutable_contract) != 4
        or immutable_contract[0] != service_principal_id
        or not all(immutable_contract[:3])
        or immutable_contract[3]
    ):
        raise RuntimeError("temporary Lakebase bootstrap expected immutable identity is invalid")
    require_principal_absent = expected_absent_identity is not None
    if expected_absent_identity is not None:
        if (
            len(expected_absent_identity) != 2
            or expected_absent_identity[0] != service_principal_id
            or not all(expected_absent_identity)
        ):
            raise RuntimeError("temporary Lakebase bootstrap expected absent identity is invalid")
        immutable_contract = (
            expected_absent_identity[0],
            expected_absent_identity[1],
            "",
            "",
        )
    stable_observations = 0
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            try:
                before = client.service_principals.get(service_principal_id)
            except NotFound:
                if immutable_contract is None:
                    raise RuntimeError(
                        "temporary Lakebase bootstrap principal disappeared before "
                        "credential quarantine"
                    ) from None
                principal_absent = True
            else:
                if require_principal_absent:
                    raise RuntimeError(
                        "temporary Lakebase bootstrap principal unexpectedly reappeared"
                    )
                immutable_before = tuple(
                    str(getattr(before, field, "") or "")
                    for field in ("id", "application_id", "display_name", "external_id")
                )
                if immutable_contract is None:
                    if not all(immutable_before[:3]) or immutable_before[3]:
                        raise RuntimeError(
                            "temporary Lakebase bootstrap principal identity is incomplete"
                        )
                    immutable_contract = immutable_before
                elif immutable_before != immutable_contract:
                    raise RuntimeError(
                        "temporary Lakebase bootstrap principal immutable identity drifted"
                    )
                principal_absent = False

            try:
                secrets = list(client.service_principal_secrets_proxy.list(service_principal_id))
            except NotFound:
                if not (require_principal_absent and allow_secret_proxy_not_found):
                    raise
                secrets = []
            for secret in secrets:
                secret_id = str(getattr(secret, "id", "") or "").strip()
                if not secret_id:
                    raise RuntimeError(
                        "temporary Lakebase bootstrap credential has no immutable id"
                    )
                assert_mutation_lease()
                with suppress(Exception):  # a following LIST proves the outcome
                    client.service_principal_secrets_proxy.delete(
                        service_principal_id,
                        secret_id,
                    )
            immutable_final = immutable_contract
            if not principal_absent:
                try:
                    principal = client.service_principals.get(service_principal_id)
                except NotFound:
                    principal_absent = True
                else:
                    immutable_final = tuple(
                        str(getattr(principal, field, "") or "")
                        for field in (
                            "id",
                            "application_id",
                            "display_name",
                            "external_id",
                        )
                    )
            try:
                remaining = list(client.service_principal_secrets_proxy.list(service_principal_id))
            except NotFound:
                if not (require_principal_absent and allow_secret_proxy_not_found):
                    raise
                remaining = []
            if immutable_final != immutable_contract or remaining:
                raise RuntimeError(
                    "temporary Lakebase bootstrap credential quarantine did not converge"
                )
            stable_observations += 1
            last_error = None
            if stable_observations >= 3:
                return
        except Exception as exc:  # noqa: BLE001 - retry transient SCIM propagation
            stable_observations = 0
            last_error = exc
        if attempt + 1 < attempts:
            time.sleep(1)
    detail = f"; last_error={type(last_error).__name__}" if last_error else ""
    raise RuntimeError(
        f"temporary Lakebase bootstrap credential quarantine did not converge{detail}"
    )


def emergency_quarantine_verified_bootstrap_credentials(
    client: Any,
    *,
    account_client: Any | None = None,
    service_principal_id: str,
    application_id: str,
    display_name: str,
    external_id: str,
) -> None:
    """Quarantine only the immutable principal already verified by this run."""

    try:
        assert_bootstrap_principal_display_name(
            display_name,
            expected_name=display_name,
            ownership_marker=external_id,
        )
    except RuntimeError as exc:
        raise RuntimeError("emergency Lakebase bootstrap principal identity drifted") from exc
    try:
        exact = client.service_principals.get(service_principal_id)
    except NotFound:
        exact = None
    expected_absent_identity: tuple[str, str] | None = None
    if exact is not None:
        try:
            assert_scim_external_id_unset(
                exact,
                label="emergency Lakebase bootstrap principal",
            )
        except RuntimeError as exc:
            raise RuntimeError("emergency Lakebase bootstrap principal identity drifted") from exc
        if (
            str(getattr(exact, "id", "") or "").strip() != service_principal_id
            or str(getattr(exact, "application_id", "") or "").strip() != application_id
            or str(getattr(exact, "display_name", "") or "") != display_name
            or any(getattr(exact, field, None) for field in ("groups", "roles", "entitlements"))
        ):
            raise RuntimeError("emergency Lakebase bootstrap principal identity drifted")
    else:
        if account_client is None:
            raise RuntimeError("emergency Lakebase bootstrap principal absence lacks account proof")
        from tools.databricks.lakebase_oauth_role_recovery_identity import (
            prove_deleted_bootstrap_principal_absent,
        )

        prove_deleted_bootstrap_principal_absent(
            client,
            account_client,
            principal_id=service_principal_id,
            application_id=application_id,
        )
        expected_absent_identity = (service_principal_id, application_id)
    if any(
        str(getattr(app, "service_principal_client_id", "") or "") == application_id
        for app in client.apps.list()
    ):
        raise RuntimeError("emergency Lakebase bootstrap principal is bound to an App")
    disable_and_revoke_bootstrap_credentials(
        client,
        service_principal_id=service_principal_id,
        allow_unlocked_recovery=True,
        expected_immutable_contract=(
            (
                service_principal_id,
                application_id,
                display_name,
                "",
            )
            if expected_absent_identity is None
            else None
        ),
        expected_absent_identity=expected_absent_identity,
        allow_secret_proxy_not_found=expected_absent_identity is not None,
    )


def quarantine_bootstrap_identity(
    client: Any,
    deployer_cursor: Any,
    *,
    service_principal_id: str,
    application_id: str,
    display_name: str,
    expected_executor: str,
    instance_name: str,
    database_name: str,
    target_application_id: str,
    external_id: str,
    allow_absent_managed_event_triggers: bool,
    bootstrap_lock_cursor: Any,
    bootstrap_lock_key: Any,
) -> None:
    """Revoke credentials and sessions while retaining the exact SCIM handle."""

    errors: list[str] = []
    try:
        disable_and_revoke_bootstrap_credentials(
            client,
            service_principal_id=service_principal_id,
            bootstrap_lock_cursor=bootstrap_lock_cursor,
            bootstrap_lock_key=bootstrap_lock_key,
            allow_unlocked_recovery=False,
        )
    except Exception as exc:  # noqa: BLE001 - session quarantine remains mandatory
        errors.append(f"credential quarantine: {type(exc).__name__}: {exc}")
    try:
        from tools.databricks.lakebase_oauth_role_bootstrap import read_profile
        from tools.databricks.lakebase_oauth_role_bootstrap_admission import (
            fence_bootstrap_role_admission,
        )

        profile = read_profile(deployer_cursor, application_id)
        if profile is not None:
            fence_bootstrap_role_admission(
                client,
                deployer_cursor,
                instance_name=instance_name,
                database_name=database_name,
                application_id=application_id,
                display_name=display_name,
                target_application_id=target_application_id,
                external_id=external_id,
                service_principal_id=service_principal_id,
                allow_absent_managed_event_triggers=(allow_absent_managed_event_triggers),
                bootstrap_lock_cursor=bootstrap_lock_cursor,
                bootstrap_lock_key=bootstrap_lock_key,
                allow_unlocked_recovery_for_tests=False,
            )
        terminate_bootstrap_sessions(
            deployer_cursor,
            application_id=application_id,
            expected_executor=expected_executor,
        )
    except Exception as exc:  # noqa: BLE001 - preserve every quarantine failure
        errors.append(f"session quarantine: {type(exc).__name__}: {exc}")
    if errors:
        raise RuntimeError(f"temporary Lakebase bootstrap quarantine was incomplete: {errors!r}")
