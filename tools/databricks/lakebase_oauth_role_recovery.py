"""Deterministic recovery for one-use privileged Lakebase bootstrap identities."""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Callable
from typing import Any

from psycopg import sql as psql

from tools.databricks.lakebase_oauth_role_bootstrap import (
    _BOOTSTRAP_API_PROFILE,
    assert_oauth_security_label,
    read_profile,
)
from tools.databricks.lakebase_oauth_role_tombstone import (
    delete_orphan_tombstone as _delete_orphan_tombstone,
)
from tools.databricks.lakebase_oauth_role_tombstone import (
    ensure_orphan_tombstone as _ensure_orphan_tombstone,
)
from tools.databricks.lakebase_oauth_role_tombstone import (
    orphan_tombstones as _orphan_tombstones,
)

_BOOTSTRAP_DISPLAY_PREFIX = "mip-lakebase-role-bootstrap-"
_BOOTSTRAP_EXTERNAL_ID_PREFIX = "mip:lb:b:v1:"
_MARKER_SIGNING_KEY_ENV = "MIP_AI_GATEWAY_PROOF_SIGNING_KEY"


def _bootstrap_identity_contract(
    *,
    instance_name: str,
    database_name: str,
    application_id: str,
) -> tuple[str, str]:
    digest = hashlib.sha256(
        f"{instance_name}\0{database_name}\0{application_id}".encode()
    ).hexdigest()
    return _BOOTSTRAP_DISPLAY_PREFIX + digest[:24], _BOOTSTRAP_EXTERNAL_ID_PREFIX + digest[:48]


def _marker_signing_key() -> str | None:
    value = os.environ.get(_MARKER_SIGNING_KEY_ENV, "").strip()
    return value or None


def _exact_bootstrap_principals(
    client: Any,
    *,
    display_name: str,
    external_id: str,
) -> list[Any]:
    candidates: dict[str, Any] = {}
    for filter_expr in (
        f"displayName eq '{display_name}'",
        f"externalId eq '{external_id}'",
    ):
        for principal in client.service_principals.list(filter=filter_expr):
            principal_id = str(getattr(principal, "id", "") or "").strip()
            if not principal_id:
                raise RuntimeError("bootstrap principal inventory returned an identity without id")
            candidates[principal_id] = principal
    conflicts = [
        principal
        for principal in candidates.values()
        if str(getattr(principal, "display_name", "") or "") != display_name
        or str(getattr(principal, "external_id", "") or "") != external_id
    ]
    if conflicts:
        raise RuntimeError("reserved Lakebase bootstrap identity marker is ambiguous")
    return list(candidates.values())


def _control_plane_role(
    client: Any,
    *,
    instance_name: str,
    application_id: str,
) -> Any | None:
    roles = [
        role
        for role in client.database.list_database_instance_roles(instance_name)
        if str(getattr(role, "name", "") or "") == application_id
    ]
    if len(roles) > 1:
        raise RuntimeError("temporary Lakebase bootstrap role inventory is ambiguous")
    return roles[0] if roles else None


def _assert_bootstrap_principal_contract(
    client: Any,
    principal: Any,
    *,
    display_name: str,
    external_id: str,
) -> tuple[str, str]:
    principal_id = str(getattr(principal, "id", "") or "").strip()
    if not principal_id:
        raise RuntimeError("temporary Lakebase bootstrap principal has no immutable id")
    exact = client.service_principals.get(principal_id)
    application_id = str(getattr(exact, "application_id", "") or "").strip()
    if (
        not application_id
        or str(getattr(exact, "display_name", "") or "") != display_name
        or str(getattr(exact, "external_id", "") or "") != external_id
        or any(getattr(exact, field, None) for field in ("groups", "roles", "entitlements"))
    ):
        raise RuntimeError("temporary Lakebase bootstrap principal contract drifted")
    if any(
        str(getattr(app, "service_principal_client_id", "") or "") == application_id
        for app in client.apps.list()
    ):
        raise RuntimeError("temporary Lakebase bootstrap principal is bound to an App")
    return principal_id, application_id


def _bootstrap_role_relationships(cursor: Any, application_id: str) -> list[tuple[Any, ...]]:
    cursor.execute(
        """
        SELECT parent.rolname,
               member.rolname,
               membership.admin_option,
               membership.inherit_option,
               membership.set_option,
               grantor.rolname
        FROM pg_auth_members membership
        JOIN pg_roles parent ON parent.oid = membership.roleid
        JOIN pg_roles member ON member.oid = membership.member
        JOIN pg_roles grantor ON grantor.oid = membership.grantor
        WHERE membership.roleid = (SELECT oid FROM pg_roles WHERE rolname = %s)
           OR membership.member = (SELECT oid FROM pg_roles WHERE rolname = %s)
        ORDER BY parent.rolname, member.rolname
        """,
        (application_id, application_id),
    )
    return list(cursor.fetchall())


def _role_ownership_marker(cursor: Any, application_id: str) -> str | None:
    cursor.execute(
        "SELECT shobj_description(oid, 'pg_authid') FROM pg_roles WHERE rolname = %s",
        (application_id,),
    )
    row = cursor.fetchone()
    return str(row[0]) if row and row[0] is not None else None


def _assert_bootstrap_role_contract(
    client: Any,
    cursor: Any,
    *,
    instance_name: str,
    database_name: str,
    application_id: str,
    target_application_id: str,
    external_id: str,
    service_principal_id: str | None,
) -> bool:
    control_role = _control_plane_role(
        client,
        instance_name=instance_name,
        application_id=application_id,
    )
    profile = read_profile(cursor, application_id)
    if control_role is None and profile is None:
        return False
    if control_role is None:
        raise RuntimeError("temporary Lakebase bootstrap role contract drifted")
    identity_type = getattr(control_role, "identity_type", None)
    if str(getattr(identity_type, "value", identity_type) or "") != "SERVICE_PRINCIPAL":
        raise RuntimeError("temporary Lakebase bootstrap role identity type drifted")
    if service_principal_id is not None:
        assert_oauth_security_label(
            cursor,
            application_id=application_id,
            service_principal_id=service_principal_id,
        )

    # Persist the source-owned recovery marker before checking any mutable
    # membership or ACL surface. If later cleanup fails and the workspace SP
    # must still be deleted to invalidate tokens, the privileged DB role stays
    # discoverable on the next run.
    description = _role_ownership_marker(cursor, application_id)
    if description is None:
        cursor.execute(
            psql.SQL("COMMENT ON ROLE {} IS {}").format(
                psql.Identifier(application_id),
                psql.Literal(external_id),
            )
        )
        description = _role_ownership_marker(cursor, application_id)
    if description != external_id:
        raise RuntimeError("temporary Lakebase bootstrap role ownership marker drifted")
    if profile != _BOOTSTRAP_API_PROFILE:
        raise RuntimeError("temporary Lakebase bootstrap role attribute profile drifted")

    relationships = _bootstrap_role_relationships(cursor, application_id)
    allowed_relationships = [
        (
            target_application_id,
            application_id,
            True,
            False,
            False,
            "cloud_admin",
        )
    ]
    if relationships not in ([], allowed_relationships):
        raise RuntimeError("temporary Lakebase bootstrap role relationship drifted")

    cursor.execute(
        """
        SELECT ARRAY(
            SELECT acl.privilege_type
            FROM pg_database database_object
            CROSS JOIN aclexplode(database_object.datacl) acl
            JOIN pg_roles grantee ON grantee.oid = acl.grantee
            WHERE database_object.datname = %s
              AND grantee.rolname = %s
            ORDER BY acl.privilege_type
        )
        """,
        (database_name, application_id),
    )
    direct_database_privileges = tuple((cursor.fetchone() or ([],))[0] or [])
    if direct_database_privileges not in ((), ("CREATE",)):
        raise RuntimeError("temporary Lakebase bootstrap database privilege drifted")

    cursor.execute(
        """
        SELECT dependency.dbid,
               dependency.classid::regclass::text,
               dependency.objsubid,
               dependency.deptype,
               COALESCE(database_object.datname, '')
        FROM pg_shdepend dependency
        LEFT JOIN pg_database database_object
          ON dependency.classid = 'pg_database'::regclass
         AND database_object.oid = dependency.objid
        WHERE dependency.refclassid = 'pg_authid'::regclass
          AND dependency.refobjid = (
              SELECT oid FROM pg_roles WHERE rolname = %s
          )
        ORDER BY 1, 2, 3, 4, 5
        """,
        (application_id,),
    )
    shared_dependencies = list(cursor.fetchall())
    allowed_dependencies = [(0, "pg_database", 0, "a", database_name)]
    if shared_dependencies not in ([], allowed_dependencies):
        raise RuntimeError("temporary Lakebase bootstrap dependency drifted")

    return True


def _disable_and_revoke_bootstrap_credentials(
    client: Any,
    *,
    service_principal_id: str,
) -> None:
    from databricks.sdk.service.iam import Patch, PatchOp, PatchSchema

    before = client.service_principals.get(service_principal_id)
    immutable_before = tuple(
        str(getattr(before, field, "") or "")
        for field in ("id", "application_id", "display_name", "external_id")
    )
    client.service_principals.patch(
        id=service_principal_id,
        operations=[Patch(op=PatchOp.REPLACE, path="active", value=False)],
        schemas=[PatchSchema.URN_IETF_PARAMS_SCIM_API_MESSAGES_2_0_PATCH_OP],
    )
    principal = client.service_principals.get(service_principal_id)
    immutable_after = tuple(
        str(getattr(principal, field, "") or "")
        for field in ("id", "application_id", "display_name", "external_id")
    )
    if immutable_after != immutable_before or getattr(principal, "active", None) is not False:
        raise RuntimeError("temporary Lakebase bootstrap principal did not become inactive")
    secrets = list(client.service_principal_secrets_proxy.list(service_principal_id))
    for secret in secrets:
        secret_id = str(getattr(secret, "id", "") or "").strip()
        if not secret_id:
            raise RuntimeError("temporary Lakebase bootstrap credential has no immutable id")
        client.service_principal_secrets_proxy.delete(service_principal_id, secret_id)
    if list(client.service_principal_secrets_proxy.list(service_principal_id)):
        raise RuntimeError("temporary Lakebase bootstrap credentials survived revocation")


def _delete_bootstrap_role(
    client: Any,
    deployer_cursor: Any,
    *,
    instance_name: str,
    application_id: str,
    attempts: int = 15,
) -> None:
    """Delete a possibly ambiguous role creation and prove stable absence."""

    absent_observations = 0
    last_error: Exception | None = None
    for attempt in range(attempts):
        present = read_profile(deployer_cursor, application_id) is not None
        try:
            present = present or _control_plane_role(
                client,
                instance_name=instance_name,
                application_id=application_id,
            ) is not None
        except Exception as exc:  # noqa: BLE001 - absence must remain conclusive
            last_error = exc
            present = True
        if present:
            absent_observations = 0
            try:
                client.database.delete_database_instance_role(instance_name, application_id)
                last_error = None
            except Exception as exc:  # noqa: BLE001 - retry ambiguous deletion
                last_error = exc
        else:
            absent_observations += 1
            if absent_observations >= 3:
                return
        if attempt + 1 < attempts:
            time.sleep(1)
    detail = f"; last_error={type(last_error).__name__}" if last_error is not None else ""
    raise RuntimeError(f"temporary Lakebase bootstrap role cleanup did not converge{detail}")


def _delete_control_plane_bootstrap_role(
    client: Any,
    *,
    instance_name: str,
    application_id: str,
    attempts: int = 15,
) -> None:
    """Delete a creator when its target database is absent and SQL is unavailable."""

    absent_observations = 0
    last_error: Exception | None = None
    for attempt in range(attempts):
        role = _control_plane_role(
            client,
            instance_name=instance_name,
            application_id=application_id,
        )
        if role is None:
            absent_observations += 1
            if absent_observations >= 3:
                return
        else:
            identity_type = getattr(role, "identity_type", None)
            if str(getattr(identity_type, "value", identity_type) or "") != "SERVICE_PRINCIPAL":
                raise RuntimeError("temporary Lakebase bootstrap role identity type drifted")
            absent_observations = 0
            try:
                client.database.delete_database_instance_role(instance_name, application_id)
                last_error = None
            except Exception as exc:  # noqa: BLE001 - retry ambiguous deletion
                last_error = exc
        if attempt + 1 < attempts:
            time.sleep(1)
    detail = f"; last_error={type(last_error).__name__}" if last_error is not None else ""
    raise RuntimeError(
        f"temporary Lakebase bootstrap control-plane role cleanup did not converge{detail}"
    )


def _commented_bootstrap_roles(cursor: Any, external_id: str) -> list[str]:
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


def _delete_bootstrap_principal(
    client: Any,
    *,
    principal_id: str,
    display_name: str,
    external_id: str,
    attempts: int = 15,
) -> None:
    last_error: Exception | None = None
    for attempt in range(attempts):
        principals = _exact_bootstrap_principals(
            client,
            display_name=display_name,
            external_id=external_id,
        )
        if not any(str(getattr(item, "id", "") or "") == principal_id for item in principals):
            return
        try:
            client.service_principals.delete(principal_id)
            last_error = None
        except Exception as exc:  # noqa: BLE001 - retry ambiguous deletion
            last_error = exc
        if attempt + 1 < attempts:
            time.sleep(1)
    detail = f"; last_error={type(last_error).__name__}" if last_error is not None else ""
    raise RuntimeError(f"temporary Lakebase bootstrap principal deletion did not converge{detail}")


def _role_exists_on_either_plane(
    client: Any,
    deployer_cursor: Any,
    *,
    instance_name: str,
    application_id: str,
) -> bool:
    """Fail closed across eventual-consistency disagreement between role inventories."""

    sql_present = read_profile(deployer_cursor, application_id) is not None
    control_present = _control_plane_role(
        client,
        instance_name=instance_name,
        application_id=application_id,
    ) is not None
    return sql_present or control_present


def recover_bootstrap_principals_for_absent_instance(
    client: Any,
    *,
    instance_name: str,
    database_name: str,
    target_application_id: str,
    marker_signing_key: str | None = None,
    resource_absence_probe: Callable[[], bool] | None = None,
    recover_control_plane_roles: bool = False,
    attempts: int = 15,
) -> bool:
    """Recover workspace markers only after proving the target resource stays absent.

    By default the exact Lakebase instance is the resource. Callers may supply
    an equally fail-closed probe for a child resource (currently the target
    database). Returns ``False`` as soon as the resource exists so the caller
    can switch to full SQL/control-plane recovery. Returns ``True`` only after
    three stable observations of both resource and marker absence.
    """

    display_name, external_id = _bootstrap_identity_contract(
        instance_name=instance_name,
        database_name=database_name,
        application_id=target_application_id,
    )
    marker_signing_key = marker_signing_key or _marker_signing_key()
    marker_absence = 0
    for attempt in range(attempts):
        principals = _exact_bootstrap_principals(
            client,
            display_name=display_name,
            external_id=external_id,
        )
        verified: list[tuple[str, str, bool]] = []
        credential_errors: list[str] = []
        for principal in principals:
            principal_id, application_id = _assert_bootstrap_principal_contract(
                client,
                principal,
                display_name=display_name,
                external_id=external_id,
            )
            credential_cleanup_succeeded = False
            try:
                _disable_and_revoke_bootstrap_credentials(
                    client,
                    service_principal_id=principal_id,
                )
                credential_cleanup_succeeded = True
            except Exception as exc:  # noqa: BLE001 - quarantine every exact marker
                credential_errors.append(
                    f"{principal_id} credential cleanup: {type(exc).__name__}: {exc}"
                )
            verified.append(
                (principal_id, application_id, credential_cleanup_succeeded)
            )
        orphan_tombstones = _orphan_tombstones(
            client,
            base_external_id=external_id,
        )

        if resource_absence_probe is None:
            instances = [
                instance
                for instance in client.database.list_database_instances()
                if str(getattr(instance, "name", "") or "") == instance_name
            ]
            if len(instances) > 1:
                raise RuntimeError("Lakebase instance inventory is ambiguous")
            resource_absent = not instances
        else:
            resource_absent = resource_absence_probe()
        if not resource_absent:
            return False
        resource_absence = attempt + 1

        if verified or orphan_tombstones:
            marker_absence = 0
            if resource_absence >= 3:
                cleanup_errors: list[str] = list(credential_errors)
                role_cleanup_failures: set[str] = set()
                if recover_control_plane_roles:
                    role_targets = {
                        application_id
                        for _principal_id, application_id, _credential_ok in verified
                    }
                    for _marker_id, application_id, *_marker_fields in orphan_tombstones:
                        if application_id == target_application_id:
                            cleanup_errors.append(
                                "orphan marker contract: target runtime identity is never a "
                                "bootstrap role"
                            )
                            role_cleanup_failures.add(application_id)
                        else:
                            role_targets.add(application_id)
                    for application_id in sorted(role_targets):
                        try:
                            _delete_control_plane_bootstrap_role(
                                client,
                                instance_name=instance_name,
                                application_id=application_id,
                            )
                        except Exception as exc:  # noqa: BLE001 - aggregate exact roles
                            role_cleanup_failures.add(application_id)
                            cleanup_errors.append(f"{type(exc).__name__}: {exc}")
                for principal_id, application_id, credential_cleanup_succeeded in verified:
                    principal_deletion_authorized = (
                        application_id not in role_cleanup_failures
                    )
                    if not principal_deletion_authorized and not credential_cleanup_succeeded:
                        try:
                            _ensure_orphan_tombstone(
                                client,
                                base_external_id=external_id,
                                application_id=application_id,
                                signing_key=str(marker_signing_key or ""),
                            )
                            principal_deletion_authorized = True
                        except Exception as exc:  # noqa: BLE001 - retain source marker
                            cleanup_errors.append(
                                f"orphan marker persistence: {type(exc).__name__}: {exc}"
                            )
                    if not principal_deletion_authorized:
                        cleanup_errors.append(
                            "principal cleanup: retained inactive credential-free marker "
                            "because control-plane role deletion was unproven"
                        )
                        continue
                    try:
                        _delete_bootstrap_principal(
                            client,
                            principal_id=principal_id,
                            display_name=display_name,
                            external_id=external_id,
                        )
                    except Exception as exc:  # noqa: BLE001 - aggregate exact identities
                        cleanup_errors.append(f"{type(exc).__name__}: {exc}")
                for tombstone_id, application_id, *_remaining_fields in orphan_tombstones:
                    if application_id in role_cleanup_failures:
                        continue
                    try:
                        _delete_orphan_tombstone(
                            client,
                            tombstone_id=tombstone_id,
                            base_external_id=external_id,
                        )
                    except Exception as exc:  # noqa: BLE001 - aggregate exact markers
                        cleanup_errors.append(f"{type(exc).__name__}: {exc}")
                if cleanup_errors:
                    raise RuntimeError(
                        "temporary Lakebase bootstrap principal cleanup was incomplete: "
                        f"{cleanup_errors!r}"
                    )
        else:
            marker_absence += 1
            if resource_absence >= 3 and marker_absence >= 3:
                return True
        if attempt + 1 < attempts:
            time.sleep(1)
    raise RuntimeError("absent-instance Lakebase bootstrap recovery did not converge")


def recover_stale_bootstrap_identities(
    client: Any,
    deployer_cursor: Any,
    *,
    instance_name: str,
    database_name: str,
    target_application_id: str,
    marker_signing_key: str | None = None,
    attempts: int = 15,
) -> None:
    """Recover deterministic one-use creators left by interruption or ambiguity."""

    display_name, external_id = _bootstrap_identity_contract(
        instance_name=instance_name,
        database_name=database_name,
        application_id=target_application_id,
    )
    marker_signing_key = marker_signing_key or _marker_signing_key()
    # Three observations are required on every path. A previous process may
    # have committed a workspace principal or database role just before losing
    # its response, so a single empty eventually-consistent list is not proof.
    required_absence = 3
    absence_observations = 0
    for attempt in range(attempts):
        principals = _exact_bootstrap_principals(
            client,
            display_name=display_name,
            external_id=external_id,
        )

        # Cross the credential boundary before any Lakebase inventory query can
        # fail. Each exact, app-unbound deterministic principal is disabled and
        # stripped of secrets independently.
        principal_states: list[tuple[str, str, bool, list[str]]] = []
        for principal in principals:
            principal_id, application_id = _assert_bootstrap_principal_contract(
                client,
                principal,
                display_name=display_name,
                external_id=external_id,
            )
            principal_errors: list[str] = []
            credential_cleanup_succeeded = False
            try:
                _disable_and_revoke_bootstrap_credentials(
                    client,
                    service_principal_id=principal_id,
                )
                credential_cleanup_succeeded = True
            except Exception as exc:  # noqa: BLE001 - continue independent cleanup
                principal_errors.append(
                    f"credential cleanup: {type(exc).__name__}: {exc}"
                )
            principal_states.append(
                (
                    principal_id,
                    application_id,
                    credential_cleanup_succeeded,
                    principal_errors,
                )
            )

        inventory_errors: list[str] = []
        try:
            orphan_tombstones = _orphan_tombstones(
                client,
                base_external_id=external_id,
            )
        except Exception as exc:  # noqa: BLE001 - principals still require cleanup
            orphan_tombstones = []
            inventory_errors.append(
                f"orphan marker inventory: {type(exc).__name__}: {exc}"
            )
        try:
            commented_roles = _commented_bootstrap_roles(deployer_cursor, external_id)
        except Exception as exc:  # noqa: BLE001 - principals still require cleanup
            commented_roles = []
            inventory_errors.append(
                f"database marker inventory: {type(exc).__name__}: {exc}"
            )

        if (
            not principals
            and not orphan_tombstones
            and not commented_roles
            and not inventory_errors
        ):
            absence_observations += 1
            if absence_observations >= required_absence:
                return
        else:
            absence_observations = 0
            handled_roles: set[str] = set()
            cleanup_groups: list[str] = list(inventory_errors)
            for (
                tombstone_id,
                application_id,
                _tombstone_display_name,
                _tombstone_external_id,
            ) in orphan_tombstones:
                if application_id == target_application_id:
                    cleanup_groups.append(
                        "orphan marker contract: target runtime identity is never a "
                        "bootstrap role"
                    )
                    continue
                handled_roles.add(application_id)
                tombstone_errors: list[str] = []
                role_present = False
                try:
                    role_present = _assert_bootstrap_role_contract(
                        client,
                        deployer_cursor,
                        instance_name=instance_name,
                        database_name=database_name,
                        application_id=application_id,
                        target_application_id=target_application_id,
                        external_id=external_id,
                        service_principal_id=None,
                    )
                except Exception:  # tombstone owns quarantine even under drift
                    try:
                        role_present = _role_exists_on_either_plane(
                            client,
                            deployer_cursor,
                            instance_name=instance_name,
                            application_id=application_id,
                        )
                    except Exception:  # inventory failure is never absence
                        role_present = True
                role_cleanup_succeeded = not role_present
                if role_present:
                    try:
                        _delete_bootstrap_role(
                            client,
                            deployer_cursor,
                            instance_name=instance_name,
                            application_id=application_id,
                        )
                        role_cleanup_succeeded = True
                    except Exception as exc:  # noqa: BLE001 - retain durable marker
                        tombstone_errors.append(
                            f"orphan database role cleanup: {type(exc).__name__}: {exc}"
                        )
                if role_cleanup_succeeded:
                    try:
                        _delete_orphan_tombstone(
                            client,
                            tombstone_id=tombstone_id,
                            base_external_id=external_id,
                        )
                    except Exception as exc:  # noqa: BLE001 - aggregate marker cleanup
                        tombstone_errors.append(
                            f"orphan marker cleanup: {type(exc).__name__}: {exc}"
                        )
                else:
                    tombstone_errors.append(
                        "orphan marker cleanup: retained because database role deletion "
                        "was unproven"
                    )
                cleanup_groups.extend(tombstone_errors)

            for (
                principal_id,
                application_id,
                credential_cleanup_succeeded,
                cleanup_errors,
            ) in principal_states:
                handled_roles.add(application_id)
                role_present = False
                role_contract_error: Exception | None = None
                try:
                    role_present = _assert_bootstrap_role_contract(
                        client,
                        deployer_cursor,
                        instance_name=instance_name,
                        database_name=database_name,
                        application_id=application_id,
                        target_application_id=target_application_id,
                        external_id=external_id,
                        service_principal_id=principal_id,
                    )
                except Exception as exc:  # noqa: BLE001 - continue independent cleanup
                    role_contract_error = exc
                    try:
                        role_present = _role_exists_on_either_plane(
                            client,
                            deployer_cursor,
                            instance_name=instance_name,
                            application_id=application_id,
                        )
                    except Exception as inventory_exc:  # noqa: BLE001
                        # Inventory failure is not absence. Force the deletion
                        # helper to obtain its own three stable cross-plane
                        # absence observations before any principal marker can
                        # be retired.
                        role_present = True
                        cleanup_errors.append(
                            "database role inventory: "
                            f"{type(inventory_exc).__name__}: {inventory_exc}"
                        )
                role_cleanup_succeeded = not role_present
                if role_present:
                    try:
                        _delete_bootstrap_role(
                            client,
                            deployer_cursor,
                            instance_name=instance_name,
                            application_id=application_id,
                        )
                        role_cleanup_succeeded = True
                    except Exception as exc:  # noqa: BLE001 - continue independent cleanup
                        if role_contract_error is not None:
                            cleanup_errors.append(
                                "database role contract: "
                                f"{type(role_contract_error).__name__}: {role_contract_error}"
                            )
                        cleanup_errors.append(f"database role cleanup: {type(exc).__name__}: {exc}")
                principal_deletion_authorized = role_cleanup_succeeded
                if not role_cleanup_succeeded and not credential_cleanup_succeeded:
                    try:
                        _ensure_orphan_tombstone(
                            client,
                            base_external_id=external_id,
                            application_id=application_id,
                            signing_key=str(marker_signing_key or ""),
                        )
                        principal_deletion_authorized = True
                    except Exception as exc:  # noqa: BLE001 - preserve original marker
                        cleanup_errors.append(
                            f"orphan marker persistence: {type(exc).__name__}: {exc}"
                        )
                if principal_deletion_authorized:
                    try:
                        _delete_bootstrap_principal(
                            client,
                            principal_id=principal_id,
                            display_name=display_name,
                            external_id=external_id,
                        )
                    except Exception as exc:  # noqa: BLE001 - aggregate every cleanup failure
                        cleanup_errors.append(f"principal cleanup: {type(exc).__name__}: {exc}")
                elif credential_cleanup_succeeded:
                    cleanup_errors.append(
                        "principal cleanup: retained inactive credential-free marker because "
                        "database role deletion was unproven"
                    )
                else:
                    cleanup_errors.append(
                        "principal cleanup: retained because durable orphan marker "
                        "persistence was unproven"
                    )
                cleanup_groups.extend(cleanup_errors)

            for application_id in commented_roles:
                if application_id in handled_roles:
                    continue
                try:
                    role_present = _assert_bootstrap_role_contract(
                        client,
                        deployer_cursor,
                        instance_name=instance_name,
                        database_name=database_name,
                        application_id=application_id,
                        target_application_id=target_application_id,
                        external_id=external_id,
                        service_principal_id=None,
                    )
                except Exception:  # exact marker owns quarantine even under drift
                    try:
                        role_present = _role_exists_on_either_plane(
                            client,
                            deployer_cursor,
                            instance_name=instance_name,
                            application_id=application_id,
                        )
                    except Exception:  # inventory failure is never treated as absence
                        role_present = True
                if role_present:
                    try:
                        _delete_bootstrap_role(
                            client,
                            deployer_cursor,
                            instance_name=instance_name,
                            application_id=application_id,
                        )
                    except Exception as exc:  # noqa: BLE001 - aggregate all cleanup
                        cleanup_groups.append(
                            f"commented database role cleanup: {type(exc).__name__}: {exc}"
                        )
            if cleanup_groups:
                raise RuntimeError(
                    f"temporary Lakebase bootstrap cleanup was incomplete: {cleanup_groups!r}"
                )
        if attempt + 1 < attempts:
            time.sleep(1)
    raise RuntimeError("temporary Lakebase bootstrap principal cleanup did not converge")
