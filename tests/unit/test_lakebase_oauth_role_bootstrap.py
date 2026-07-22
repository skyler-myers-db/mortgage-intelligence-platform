"""Tests for the one-use Lakebase OAuth role bootstrap identity."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from databricks.sdk.errors import NotFound

from backend.services.ai_gateway_proof_attestation import (
    derive_gateway_proof_verify_key,
)
from tools.databricks import lakebase_oauth_role_account_inventory as account_inventory
from tools.databricks import lakebase_oauth_role_account_principal as account_principal
from tools.databricks import lakebase_oauth_role_bootstrap as bootstrap
from tools.databricks import lakebase_oauth_role_bootstrap_credentials as bootstrap_credentials
from tools.databricks import lakebase_oauth_role_bootstrap_lock as bootstrap_lock
from tools.databricks import (
    lakebase_oauth_role_bootstrap_orchestration as bootstrap_orchestration,
)
from tools.databricks import lakebase_oauth_role_bootstrap_sessions as bootstrap_sessions
from tools.databricks import lakebase_oauth_role_bootstrap_target as bootstrap_target
from tools.databricks import lakebase_oauth_role_bootstrap_wrapper as bootstrap_wrapper
from tools.databricks import (
    lakebase_oauth_role_bootstrap_wrapper_contract as bootstrap_wrapper_contract,
)
from tools.databricks import lakebase_oauth_role_recovery as recovery
from tools.databricks import lakebase_oauth_role_recovery_absent as recovery_absent
from tools.databricks import lakebase_oauth_role_recovery_identity as recovery_identity
from tools.databricks import lakebase_oauth_role_scim_marker as scim_marker
from tools.databricks import lakebase_oauth_role_tombstone as tombstone

_TARGET = "11111111-1111-4111-8111-111111111111"
_TARGET_SCIM_ID = "74635290620767"
_CREATOR = "22222222-2222-4222-8222-222222222222"
_CREATOR_SCIM_ID = "78879891843203"
_CONTROL = "33333333-3333-4333-8333-333333333333"
_INSTANCE = "mip-app-state"
_DATABASE = "mip_app_state"
_SIGNING_KEY = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
_VERIFY_KEY = derive_gateway_proof_verify_key(_SIGNING_KEY)
_ATTACKER_SIGNING_KEY = base64.urlsafe_b64encode(b"a" * 32).decode().rstrip("=")
_ATTACKER_VERIFY_KEY = derive_gateway_proof_verify_key(_ATTACKER_SIGNING_KEY)
_DISPLAY_NAME, _EXTERNAL_ID = recovery._bootstrap_identity_contract(
    instance_name=_INSTANCE,
    database_name=_DATABASE,
    application_id=_TARGET,
)
_WRAPPER_SCHEMA = bootstrap_wrapper.wrapper_schema_name(
    instance_name=_INSTANCE,
    database_name=_DATABASE,
    target_application_id=_TARGET,
)


def _signed_display_name() -> str:
    return scim_marker.bootstrap_principal_display_name(
        reservation_name=_DISPLAY_NAME,
        ownership_marker=_EXTERNAL_ID,
        signing_key=_SIGNING_KEY,
    )


def test_default_bootstrap_client_has_bounded_control_plane_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SimpleNamespace()
    config_constructor = MagicMock(return_value=config)
    client = MagicMock()
    client_constructor = MagicMock(return_value=client)
    monkeypatch.setattr(bootstrap, "Config", config_constructor)
    monkeypatch.setattr(bootstrap, "WorkspaceClient", client_constructor)

    assert (
        bootstrap._bounded_bootstrap_workspace_client(
            host="https://workspace.example",
            client_id="client-id",
            client_secret="client-secret",
            auth_type="oauth-m2m",
        )
        is client
    )
    assert client_constructor.call_args.kwargs == {"config": config}
    assert config_constructor.call_args.kwargs == {
        "host": "https://workspace.example",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "auth_type": "oauth-m2m",
        "http_timeout_seconds": 30,
        "retry_timeout_seconds": 30,
    }


@pytest.fixture(autouse=True)
def _no_recovery_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIP_LAKEBASE_BOOTSTRAP_CONTROL_CLIENT_ID", _CONTROL)
    monkeypatch.setenv(
        "MIP_LAKEBASE_BOOTSTRAP_CONTROL_CLIENT_SECRET",
        "control-secret-never-log",
    )
    monotonic_tick = 0

    def advance_monotonic() -> float:
        nonlocal monotonic_tick
        monotonic_tick += 1
        return float(monotonic_tick)

    monkeypatch.setattr(bootstrap.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(recovery.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(bootstrap_credentials.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(bootstrap_lock.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(bootstrap_sessions.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(bootstrap_target.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(account_inventory.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(account_principal.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(account_principal.time, "monotonic", advance_monotonic)
    monkeypatch.setattr(recovery_absent.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(recovery_absent.time, "monotonic", advance_monotonic)
    monkeypatch.setattr(tombstone.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(tombstone.time, "monotonic", advance_monotonic)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", _SIGNING_KEY)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", _VERIFY_KEY)
    monkeypatch.delenv("MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY", raising=False)
    monkeypatch.delenv("MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS", raising=False)
    recover = recovery.recover_stale_bootstrap_identities
    recover_absent = recovery.recover_bootstrap_principals_for_absent_instance

    def recover_with_local_event_trigger_seam(*args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("allow_absent_managed_event_triggers", True)
        kwargs.setdefault("allow_unlocked_recovery_for_tests", True)
        kwargs.setdefault("account_client", args[0]._account_client)
        recover(*args, **kwargs)

    def recover_absent_with_account(*args: Any, **kwargs: Any) -> bool:
        kwargs.setdefault("account_client", args[0]._account_client)
        return recover_absent(*args, **kwargs)

    monkeypatch.setattr(
        recovery,
        "recover_stale_bootstrap_identities",
        recover_with_local_event_trigger_seam,
    )
    monkeypatch.setattr(
        recovery,
        "recover_bootstrap_principals_for_absent_instance",
        recover_absent_with_account,
    )


class _State:
    def __init__(self) -> None:
        self.target_profile: tuple[bool, ...] | None = None
        self.target_relationships: list[tuple[Any, ...]] = []
        self.target_dependencies: list[tuple[Any, ...]] = []
        self.target_sessions: list[int] = []
        self.target_settings: tuple[Any, ...] = (-1, None, "********", None)
        self.target_database_settings: list[tuple[Any, ...]] = []
        self.target_label = f"id={_TARGET_SCIM_ID},type=service_principal"
        self.target_identity_type = "SERVICE_PRINCIPAL"
        self.function_exists = True
        self.function_source_sha256 = bootstrap._ROLE_FUNCTION_SOURCE_SHA256
        self.function_acl: tuple[str, ...] | None = None
        self.public_schema_owner = "pg_database_owner"
        self.public_schema_acl: list[tuple[Any, ...]] = [
            ("databricks_superuser", "CREATE", True, "pg_database_owner"),
            ("databricks_superuser", "USAGE", True, "pg_database_owner"),
            ("databricks_writer_42", "CREATE", False, "pg_database_owner"),
            ("databricks_writer_42", "USAGE", False, "pg_database_owner"),
            ("pg_database_owner", "CREATE", False, "pg_database_owner"),
            ("pg_database_owner", "USAGE", False, "pg_database_owner"),
        ]
        self.created_creator_role = False
        self.creator_profile: tuple[bool, ...] | None = None
        self.creator_settings: tuple[Any, ...] = (-1, None, "********", None)
        self.creator_database_settings: list[tuple[Any, ...]] = []
        self.creator_label = f"id={_CREATOR_SCIM_ID},type=service_principal"
        self.creator_control_plane_present = False
        self.deleted_creator_role = False
        self.deleted_creator_sp = False
        self.fail_creator_role_delete = False
        self.fail_target_role_delete_attempts = 0
        self.fail_secret_list = False
        self.fail_secret_delete = False
        self.fail_creator_sp_delete = False
        self.fail_account_principal_delete = False
        self.fail_marker_inventory = False
        self.fail_role_comment = False
        self.create_sp_commit_then_error = False
        self.tombstone_create_commit_then_error = False
        self.tombstone_delete_commit_then_error = False
        self.bootstrap_sp_present = False
        self.bootstrap_application_id = _CREATOR
        self.bootstrap_sp_active = True
        self.bootstrap_secrets: list[str] = []
        self.orphan_tombstones: dict[str, SimpleNamespace] = {}
        self.creator_role_comment: str | None = None
        self.wrapper_schema_exists = False
        self.wrapper_owner = "pg_database_owner"
        self.wrapper_provider_acl = False
        self.wrapper_function_exists = False
        self.wrapper_schema_usage = False
        self.wrapper_function_execute = False
        self.wrapper_public_execute = False
        self.wrapper_prokind = "f"
        self.wrapper_proallargtypes: list[int] | None = None
        self.wrapper_proargmodes: list[str] | None = None
        self.wrapper_proargnames: list[str] | None = None
        self.wrapper_language = "sql"
        self.wrapper_security_definer = False
        self.wrapper_prosqlbody_present = True
        self.wrapper_definition_override: str | None = None
        self.wrapper_provider_dependencies_override: list[tuple[Any, ...]] | None = None
        self.wrapper_transaction_snapshot: tuple[Any, ...] | None = None
        self.deployer_current_user = "deployer"
        self.deployer_session_user = "deployer"
        self.deployer_database_create = True
        self.deployer_can_set_database_owner = True
        self.bootstrap_session_user = _CREATOR
        self.advisory_lock_held = False
        self.advisory_lock_contended = False
        self.advisory_lock_lost = False
        self.advisory_backend_pid = 6001
        self.advisory_lock_checks = 0
        self.lose_lock_on_check: int | None = None
        self.creator_dependencies_override: list[tuple[Any, ...]] | None = None
        self.creator_sessions: list[int] = []
        self.retained_backend_active = False
        self.retained_backend_pid = 7201
        self.retained_backend_start = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
        self.retained_application_name = ""
        self.retained_client_addr = "203.0.113.42"
        self.sessions_survive_termination = False
        self.session_termination_raises = False
        self.assert_session_quarantine_order = False
        self.sessions_reappear_on_delete_failure = False
        self.sessions_reappear_during_wrapper_cleanup = False
        self.creator_database_create = False
        self.creator_public_usage = False
        self.creator_database_privilege = "CREATE"
        self.creator_public_privilege = "USAGE"
        self.creator_database_grantable = False
        self.creator_public_grantable = False
        self.fail_create_schema_after_commit = False
        self.fail_provider_revoke_after_commit = False
        self.fail_create_function_after_commit = False
        self.fail_drop_function_after_commit = False
        self.fail_drop_schema_after_commit = False
        self.fail_schema_grant_after_commit = False
        self.fail_function_grant_after_commit = False
        self.fail_provider_call_after_commit = False
        self.provider_commit_ambiguity_seen = False
        self.reconciliation_target_profile_read_failures = 0
        self.provider_commit_residual_relationships: list[tuple[Any, ...]] | None = None
        self.provider_commit_residual_mutations: dict[str, Any] = {}
        self.fail_provider_call_after_statement = False
        self.fail_provider_call_before_statement = False
        self.fail_provider_rollback = False
        self.provider_rollback_leaves_target = False
        self.fail_wrapper_commit_after_apply = False
        self.fail_wrapper_teardown_commit_after_apply = False
        self.fail_fresh_deployer_connect = False
        self.fail_patch_attempts = 0
        self.hide_principal_after_patch = False
        self.hide_principal_from_list = False
        self.creator_oid = 5101
        self.creator_oid_after_first_read: int | None = None
        self.creator_oid_reads = 0
        self.session_binding_override: tuple[int | None, str] | None = None
        self.target_session_reappears_on_delete = False
        self.target_deleted = False
        self.target_database_present = True
        self.create_profile = bootstrap.SAFE_OAUTH_PROFILE
        self.create_relationships = [(_TARGET, _CREATOR, True, False, False, "cloud_admin")]
        self.deployer_statements: list[str] = []
        self.bootstrap_statements: list[str] = []
        self.actions: list[tuple[str, int]] = []


def _render(query: Any) -> str:
    value = query.as_string() if hasattr(query, "as_string") else str(query)
    return " ".join(value.split())


class _DeployerCursor:
    def __init__(self, state: _State, *, database_name: str = _DATABASE) -> None:
        self.state = state
        self.database_name = database_name
        self._one: tuple[Any, ...] | None = None
        self._all: list[tuple[Any, ...]] = []

    def __enter__(self) -> _DeployerCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, query: Any, params: object = None) -> None:
        rendered = _render(query)
        self.state.deployer_statements.append(rendered)
        self._one = None
        self._all = []
        if "FROM pg_event_trigger event_trigger" in rendered:
            self._all = []
        elif rendered == "SELECT current_user, session_user":
            self._one = (
                self.state.deployer_current_user,
                self.state.deployer_session_user,
            )
        elif (
            "has_database_privilege(current_user, current_database(), 'CREATE')" in rendered
            and "pg_has_role(current_user, 'pg_database_owner', 'SET')" in rendered
        ):
            self._one = (
                self.state.deployer_current_user,
                self.state.deployer_session_user,
                self.state.deployer_database_create,
                self.state.deployer_can_set_database_owner,
            )
        elif rendered == "SELECT current_database()":
            self._one = (self.database_name,)
        elif rendered == "SELECT 1":
            self._one = (1,)
        elif rendered == "SELECT 1 FROM pg_database WHERE datname = %s":
            self._one = (1,) if self.state.target_database_present else None
        elif rendered == "SELECT pg_backend_pid()":
            self._one = (self.state.advisory_backend_pid,)
        elif rendered == "SELECT pg_try_advisory_lock(%s)":
            acquired = not self.state.advisory_lock_contended
            self.state.advisory_lock_held = acquired
            self._one = (acquired,)
        elif rendered == "SELECT pg_advisory_unlock(%s)":
            released = self.state.advisory_lock_held and not self.state.advisory_lock_lost
            self.state.advisory_lock_held = False
            self._one = (released,)
        elif "FROM pg_locks" in rendered:
            self.state.advisory_lock_checks += 1
            if self.state.lose_lock_on_check == self.state.advisory_lock_checks:
                self.state.advisory_lock_lost = True
            held = self.state.advisory_lock_held and not self.state.advisory_lock_lost
            self._one = (1 if held else 0,)
        elif rendered == "BEGIN":
            self.state.wrapper_transaction_snapshot = (
                self.state.wrapper_schema_exists,
                self.state.wrapper_provider_acl,
                self.state.wrapper_function_exists,
                self.state.wrapper_schema_usage,
                self.state.wrapper_function_execute,
                self.state.wrapper_public_execute,
            )
        elif rendered == 'SET LOCAL ROLE "pg_database_owner"':
            self.state.deployer_current_user = "pg_database_owner"
        elif rendered == "COMMIT":
            self.state.wrapper_transaction_snapshot = None
            self.state.deployer_current_user = self.state.deployer_session_user
            if (
                self.state.fail_wrapper_teardown_commit_after_apply
                and not self.state.wrapper_schema_exists
            ):
                self.state.fail_wrapper_teardown_commit_after_apply = False
                raise RuntimeError("injected wrapper teardown commit ambiguity")
            if self.state.fail_wrapper_commit_after_apply:
                self.state.fail_wrapper_commit_after_apply = False
                raise RuntimeError("injected wrapper commit ambiguity")
        elif rendered == "ROLLBACK":
            snapshot = self.state.wrapper_transaction_snapshot
            if snapshot is not None:
                (
                    self.state.wrapper_schema_exists,
                    self.state.wrapper_provider_acl,
                    self.state.wrapper_function_exists,
                    self.state.wrapper_schema_usage,
                    self.state.wrapper_function_execute,
                    self.state.wrapper_public_execute,
                ) = snapshot
            self.state.wrapper_transaction_snapshot = None
            self.state.deployer_current_user = self.state.deployer_session_user
        elif rendered == "SELECT oid FROM pg_roles WHERE rolname = %s":
            role_name = params[0] if isinstance(params, tuple) else None
            if role_name == _CREATOR:
                self.state.creator_oid_reads += 1
                if (
                    self.state.creator_oid_after_first_read is not None
                    and self.state.creator_oid_reads > 1
                ):
                    self.state.creator_oid = self.state.creator_oid_after_first_read
            present = (
                self.state.creator_profile is not None
                if role_name == _CREATOR
                else self.state.target_profile is not None
            )
            role_oid = self.state.creator_oid if role_name == _CREATOR else 5102
            self._all = [(role_oid,)] if present else []
        elif rendered.startswith("SELECT oid, rolname FROM pg_roles"):
            role_name = None
            if isinstance(params, tuple):
                role_name = params[0] if "WHERE rolname = %s" in rendered else params[-1]
            present = (
                self.state.creator_profile is not None
                if role_name == _CREATOR
                else self.state.target_profile is not None
            )
            role_oid = self.state.creator_oid if role_name == _CREATOR else 5102
            self._all = [(role_oid, role_name)] if present else []
        elif (
            "SELECT activity.pid," in rendered
            and "activity.backend_start" in rendered
            and "activity.application_name" in rendered
        ):
            if self.state.retained_backend_active:
                self._all = [
                    (
                        self.state.retained_backend_pid,
                        self.state.creator_oid,
                        _CREATOR,
                        _DATABASE,
                        self.state.retained_application_name,
                        self.state.retained_backend_start,
                        "client backend",
                        self.state.retained_client_addr,
                    )
                ]
        elif "FROM pg_stat_activity" in rendered:
            if self.state.assert_session_quarantine_order:
                assert self.state.bootstrap_secrets == []
            role_name = None
            if isinstance(params, tuple):
                role_name = params[1] if len(params) >= 2 else params[0]
            sessions = (
                self.state.creator_sessions if role_name == _CREATOR else self.state.target_sessions
            )
            role_oid = self.state.creator_oid if role_name == _CREATOR else 5102
            binding = self.state.session_binding_override
            self._all = [
                (
                    pid,
                    role_oid if binding is None else binding[0],
                    role_name if binding is None else binding[1],
                )
                for pid in sessions
            ]
        elif rendered == "SELECT pg_terminate_backend(%s)":
            if self.state.session_termination_raises:
                raise RuntimeError("injected session termination failure")
            pid = int(params[0]) if isinstance(params, tuple) else -1
            if not self.state.sessions_survive_termination:
                if pid in self.state.creator_sessions:
                    self.state.creator_sessions.remove(pid)
                if pid in self.state.target_sessions:
                    self.state.target_sessions.remove(pid)
            self._one = (not self.state.sessions_survive_termination,)
        elif "JOIN pg_depend extension_membership" in rendered:
            self._one = (
                (
                    "public",
                    "databricks_create_role",
                    "text, text",
                    "f",
                    "text",
                    "cloud_admin",
                    "c",
                    "v",
                    "s",
                    False,
                    True,
                    False,
                    None,
                    "$libdir/databricks_auth",
                    "databricks_auth",
                    "1.0",
                    True,
                    "public",
                    "databricks_writer_42",
                    self.state.function_source_sha256,
                    bootstrap._ROLE_FUNCTION_SOURCE_BYTES,
                    self.state.function_acl,
                )
                if self.state.function_exists
                else None
            )
        elif "SELECT namespace.nspname, owner.rolname, database_object.oid" in rendered:
            self._all = [("public", self.state.public_schema_owner, 42)]
        elif "SELECT namespace.oid," in rendered and "namespace.nspowner" in rendered:
            self._all = (
                [(2201, self.state.wrapper_owner, 42)] if self.state.wrapper_schema_exists else []
            )
        elif (
            "CASE WHEN acl.grantee = 0 THEN 'PUBLIC'" in rendered
            and "FROM pg_namespace" in rendered
        ):
            if isinstance(params, tuple) and params == (_WRAPPER_SCHEMA,):
                self._all = [
                    (self.state.wrapper_owner, "CREATE", False, self.state.wrapper_owner),
                    (self.state.wrapper_owner, "USAGE", False, self.state.wrapper_owner),
                ]
                if self.state.wrapper_provider_acl:
                    self._all.extend(
                        [
                            ("databricks_superuser", "CREATE", True, self.state.wrapper_owner),
                            ("databricks_superuser", "USAGE", True, self.state.wrapper_owner),
                            (
                                "databricks_writer_42",
                                "CREATE",
                                False,
                                "databricks_superuser",
                            ),
                            (
                                "databricks_writer_42",
                                "USAGE",
                                False,
                                "databricks_superuser",
                            ),
                            ("databricks_gateway", "USAGE", False, self.state.wrapper_owner),
                            (
                                "databricks_reader_42",
                                "USAGE",
                                False,
                                "databricks_superuser",
                            ),
                        ]
                    )
                if self.state.wrapper_schema_usage:
                    self._all.append((_CREATOR, "USAGE", False, self.state.wrapper_owner))
            else:
                self._all = list(self.state.public_schema_acl)
        elif (
            "FROM unnest(%s::text[]) target(role_name)" in rendered
            or "owner.rolname = 'cloud_admin'" in rendered
        ):
            self._all = []
        elif rendered == "SELECT current_user":
            self._one = (self.state.deployer_current_user,)
        elif "routine.provolatile" in rendered and "FROM pg_proc routine" in rendered:
            if self.state.wrapper_function_exists:
                definition = bootstrap_wrapper_contract.canonical_wrapper_definition(
                    schema_name=_WRAPPER_SCHEMA,
                    target_application_id=_TARGET,
                    bootstrap_application_id=_CREATOR,
                )
                definition = self.state.wrapper_definition_override or definition
                self._all = [
                    (
                        3301,
                        _WRAPPER_SCHEMA,
                        "create_target_role",
                        self.state.wrapper_prokind,
                        "",
                        0,
                        0,
                        0,
                        self.state.wrapper_proallargtypes,
                        self.state.wrapper_proargmodes,
                        self.state.wrapper_proargnames,
                        "text",
                        self.state.wrapper_owner,
                        self.state.wrapper_language,
                        "v",
                        "u",
                        self.state.wrapper_security_definer,
                        False,
                        False,
                        ["search_path=pg_catalog", "createrole_self_grant="],
                        "",
                        self.state.wrapper_prosqlbody_present,
                        definition,
                        __import__("hashlib").sha256(definition.encode()).hexdigest(),
                        len(definition.encode()),
                    )
                ]
        elif (
            "FROM pg_proc routine" in rendered
            and "aclexplode(routine.proacl)" in rendered
            and "namespace.nspname = 'public'" in rendered
        ):
            self._all = []
        elif "FROM pg_proc routine" in rendered and "aclexplode(routine.proacl)" in rendered:
            self._all = [(self.state.wrapper_owner, "EXECUTE", False, self.state.wrapper_owner)]
            if self.state.wrapper_public_execute:
                self._all.append(("PUBLIC", "EXECUTE", False, self.state.wrapper_owner))
            if self.state.wrapper_function_execute:
                self._all.append((_CREATOR, "EXECUTE", False, self.state.wrapper_owner))
        elif "dependency.refclassid = 'pg_namespace'" in rendered:
            self._all = [("pg_proc", 3301, "n")] if self.state.wrapper_function_exists else []
        elif "referenced.proname" in rendered and "FROM pg_depend dependency" in rendered:
            if self.state.wrapper_provider_dependencies_override is not None:
                self._all = list(self.state.wrapper_provider_dependencies_override)
            else:
                self._all = (
                    [
                        (
                            "pg_proc",
                            3301,
                            "pg_proc",
                            "public",
                            "databricks_create_role",
                            "text, text",
                            "n",
                        )
                    ]
                    if self.state.wrapper_function_exists
                    else []
                )
        elif "FROM pg_shdepend dependency" in rendered:
            role_name = params[0] if isinstance(params, tuple) else None
            if role_name == _TARGET:
                self._all = list(self.state.target_dependencies)
            elif self.state.creator_dependencies_override is not None:
                self._all = list(self.state.creator_dependencies_override)
            else:
                self._all = []
                if self.state.wrapper_schema_usage:
                    self._all.append((42, "pg_namespace", 2201, 0, "a"))
                if self.state.wrapper_function_execute:
                    self._all.append((42, "pg_proc", 3301, 0, "a"))
                if self.state.creator_database_create:
                    self._all.append((0, "pg_database", 42, 0, "a"))
                if self.state.creator_public_usage:
                    self._all.append((42, "pg_namespace", 2200, 0, "a"))
                self._all.sort()
        elif "SELECT routine.oid" in rendered and "FROM pg_proc routine" in rendered:
            self._all = [(3301,)] if self.state.wrapper_function_exists else []
        elif rendered.startswith("SELECT to_regnamespace"):
            self._one = (2201,) if self.state.wrapper_schema_exists else (None,)
        elif rendered == "SELECT oid FROM pg_database WHERE datname = current_database()":
            self._one = (42,)
        elif rendered == "SELECT oid FROM pg_database WHERE datname = %s":
            self._one = (42,) if params == (_DATABASE,) else None
        elif rendered == "SELECT oid FROM pg_namespace WHERE nspname = 'public'":
            self._one = (2200,)
        elif "SELECT rolconnlimit, rolvaliduntil, rolpassword, rolconfig" in rendered:
            role_name = params[0] if isinstance(params, tuple) else None
            settings = (
                self.state.creator_settings if role_name == _CREATOR else self.state.target_settings
            )
            self._all = (
                [settings]
                if (
                    self.state.creator_profile is not None
                    if role_name == _CREATOR
                    else self.state.target_profile is not None
                )
                else []
            )
        elif "FROM pg_db_role_setting setting" in rendered:
            role_name = params[0] if isinstance(params, tuple) else None
            self._all = list(
                self.state.creator_database_settings
                if role_name == _CREATOR
                else self.state.target_database_settings
            )
        elif (
            "CROSS JOIN LATERAL aclexplode(database_object.datacl)" in rendered
            and "acl.privilege_type" in rendered
        ):
            self._all = (
                [
                    (
                        42,
                        self.state.creator_database_privilege,
                        self.state.creator_database_grantable,
                        self.state.deployer_current_user,
                    )
                ]
                if self.state.creator_database_create
                else []
            )
        elif (
            "CROSS JOIN LATERAL aclexplode(namespace.nspacl)" in rendered
            and "database_object.oid" in rendered
            and "grantee.rolname = %s" in rendered
        ):
            self._all = (
                [
                    (
                        42,
                        2200,
                        self.state.creator_public_privilege,
                        self.state.creator_public_grantable,
                        self.state.deployer_current_user,
                    )
                ]
                if self.state.creator_public_usage
                else []
            )
        elif "rolreplication" in rendered and "FROM pg_roles" in rendered:
            role_name = params[0] if isinstance(params, tuple) else None
            if (
                role_name == _TARGET
                and self.state.provider_commit_ambiguity_seen
                and self.state.reconciliation_target_profile_read_failures
            ):
                self.state.reconciliation_target_profile_read_failures -= 1
                raise RuntimeError("injected transient target profile read")
            self._one = (
                self.state.creator_profile if role_name == _CREATOR else self.state.target_profile
            )
        elif "pg_shseclabel" in rendered:
            role_name = params[0] if isinstance(params, tuple) else None
            label = self.state.creator_label if role_name == _CREATOR else self.state.target_label
            self._all = [("databricks_auth", label)]
        elif rendered.startswith("SELECT 1 FROM pg_auth_members"):
            self._one = (1,) if self.state.target_relationships else None
        elif "FROM pg_auth_members membership" in rendered:
            role_name = params[0] if isinstance(params, tuple) else _CREATOR
            self._all = [
                row for row in self.state.target_relationships if role_name in (row[0], row[1])
            ]
        elif rendered.startswith("SELECT shobj_description"):
            self._one = (self.state.creator_role_comment,)
        elif "WHERE shobj_description(role.oid" in rendered:
            if self.state.fail_marker_inventory:
                raise RuntimeError("injected marker-inventory failure")
            self._all = [(_CREATOR,)] if self.state.creator_role_comment == _EXTERNAL_ID else []
        elif rendered.startswith("COMMENT ON ROLE"):
            self.state.actions.append(("bootstrap_comment", self.state.advisory_lock_checks))
            if self.state.fail_role_comment:
                raise RuntimeError("injected role-comment visibility failure")
            self.state.creator_role_comment = _EXTERNAL_ID
        elif rendered.startswith("CREATE SCHEMA"):
            self.state.actions.append(("wrapper_publish", self.state.advisory_lock_checks))
            self.state.wrapper_schema_exists = True
            self.state.wrapper_provider_acl = True
            if self.state.fail_create_schema_after_commit:
                self.state.fail_create_schema_after_commit = False
                raise RuntimeError("injected create-schema commit ambiguity")
        elif rendered.startswith("REVOKE ALL PRIVILEGES ON SCHEMA"):
            self.state.actions.append(("wrapper_schema_revoke", self.state.advisory_lock_checks))
            self.state.wrapper_provider_acl = False
            if self.state.fail_provider_revoke_after_commit:
                self.state.fail_provider_revoke_after_commit = False
                raise RuntimeError("injected provider-revoke commit ambiguity")
        elif rendered.startswith("CREATE FUNCTION"):
            self.state.actions.append(("wrapper_function_create", self.state.advisory_lock_checks))
            self.state.wrapper_function_exists = True
            self.state.wrapper_public_execute = True
            if self.state.fail_create_function_after_commit:
                self.state.fail_create_function_after_commit = False
                raise RuntimeError("injected create-function commit ambiguity")
        elif rendered.startswith("GRANT USAGE ON SCHEMA"):
            self.state.actions.append(("wrapper_schema_grant", self.state.advisory_lock_checks))
            self.state.wrapper_schema_usage = True
            if self.state.fail_schema_grant_after_commit:
                raise RuntimeError("injected schema-grant commit ambiguity")
        elif rendered.startswith("GRANT EXECUTE ON FUNCTION"):
            self.state.actions.append(("wrapper_function_grant", self.state.advisory_lock_checks))
            self.state.wrapper_function_execute = True
            if self.state.fail_function_grant_after_commit:
                raise RuntimeError("injected function-grant commit ambiguity")
        elif rendered.startswith("REVOKE ALL PRIVILEGES ON FUNCTION"):
            self.state.actions.append(("wrapper_function_revoke", self.state.advisory_lock_checks))
            self.state.wrapper_public_execute = False
        elif rendered.startswith("REVOKE EXECUTE ON FUNCTION"):
            self.state.wrapper_function_execute = False
        elif rendered.startswith("REVOKE USAGE ON SCHEMA"):
            if "public FROM" in rendered:
                self.state.creator_public_usage = False
            else:
                self.state.wrapper_schema_usage = False
        elif rendered.startswith("REVOKE CREATE ON DATABASE"):
            self.state.creator_database_create = False
        elif rendered.startswith("ALTER ROLE") and rendered.endswith("NOLOGIN"):
            self.state.actions.append(("role_nologin", self.state.advisory_lock_checks))
            if _CREATOR in rendered and self.state.creator_profile is not None:
                self.state.creator_profile = (*self.state.creator_profile[:-1], False)
            elif self.state.target_profile is not None:
                self.state.target_profile = (*self.state.target_profile[:-1], False)
        elif rendered.startswith("DROP FUNCTION"):
            self.state.actions.append(("wrapper_function_drop", self.state.advisory_lock_checks))
            self.state.wrapper_function_exists = False
            if self.state.fail_drop_function_after_commit:
                self.state.fail_drop_function_after_commit = False
                raise RuntimeError("injected drop-function commit ambiguity")
        elif rendered.startswith("DROP SCHEMA"):
            self.state.actions.append(("wrapper_schema_drop", self.state.advisory_lock_checks))
            if self.state.sessions_reappear_during_wrapper_cleanup:
                self.state.sessions_reappear_during_wrapper_cleanup = False
                self.state.creator_sessions.append(9922)
            self.state.wrapper_schema_exists = False
            if self.state.fail_drop_schema_after_commit:
                self.state.fail_drop_schema_after_commit = False
                raise RuntimeError("injected drop-schema commit ambiguity")

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._one

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._all


class _BootstrapCursor:
    def __init__(self, state: _State) -> None:
        self.state = state
        self._one: tuple[Any, ...] | None = None
        self._all: list[tuple[Any, ...]] = []

    def __enter__(self) -> _BootstrapCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, query: Any, _params: object = None) -> None:
        rendered = _render(query)
        self.state.bootstrap_statements.append(rendered)
        self._one = None
        self._all = []
        if "FROM pg_event_trigger event_trigger" in rendered:
            self._all = []
        elif (
            "FROM pg_stat_activity activity" in rendered
            and "activity.pid = pg_backend_pid()" in rendered
        ):
            self._all = [
                (
                    self.state.retained_backend_pid,
                    self.state.creator_oid,
                    _CREATOR,
                    _DATABASE,
                    self.state.retained_application_name,
                    self.state.retained_backend_start,
                    "client backend",
                    self.state.retained_client_addr,
                    _CREATOR,
                    self.state.bootstrap_session_user,
                )
            ]
        elif "JOIN pg_depend extension_membership" in rendered:
            self._one = (
                "public",
                "databricks_create_role",
                "text, text",
                "f",
                "text",
                "cloud_admin",
                "c",
                "v",
                "s",
                False,
                True,
                False,
                None,
                "$libdir/databricks_auth",
                "databricks_auth",
                "1.0",
                True,
                "public",
                "databricks_writer_42",
                self.state.function_source_sha256,
                bootstrap._ROLE_FUNCTION_SOURCE_BYTES,
                self.state.function_acl,
            )
        elif rendered == "SELECT oid FROM pg_database WHERE datname = current_database()":
            self._one = (42,)
        elif rendered == "SELECT current_user, session_user":
            self._one = (_CREATOR, self.state.bootstrap_session_user)
        elif "create_target_role" in rendered:
            assert "FROM pg_event_trigger event_trigger" in self.state.bootstrap_statements[-2]
            self.state.actions.append(("provider_call", self.state.advisory_lock_checks))
            if self.state.fail_provider_call_before_statement:
                raise RuntimeError("injected provider rejection")
            self.state.target_profile = self.state.create_profile
            self.state.target_relationships = list(self.state.create_relationships)
            if self.state.fail_provider_call_after_statement:
                raise RuntimeError("injected provider-call transport failure")
        elif "rolreplication" in rendered and "FROM pg_roles" in rendered:
            self._one = self.state.target_profile
        elif "pg_shseclabel" in rendered:
            self._all = [("databricks_auth", self.state.target_label)]
        elif "FROM pg_auth_members" in rendered:
            self._all = list(self.state.target_relationships)
        else:
            deployer_cursor = _DeployerCursor(self.state)
            deployer_cursor.execute(query, _params)
            self._one = deployer_cursor.fetchone()
            self._all = deployer_cursor.fetchall()

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._one

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._all

    def close(self) -> None:
        return None


class _BootstrapConnection:
    def __init__(self, state: _State, *, autocommit: bool, application_name: str) -> None:
        self.state = state
        self.autocommit = autocommit
        self.state.retained_backend_active = True
        self.state.retained_application_name = application_name
        self._snapshot = (
            state.target_profile,
            list(state.target_relationships),
            list(state.target_dependencies),
            list(state.target_sessions),
        )

    def __enter__(self) -> _BootstrapConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self) -> _BootstrapCursor:
        return _BootstrapCursor(self.state)

    def commit(self) -> None:
        self._snapshot = None
        if self.state.fail_provider_call_after_commit:
            self.state.fail_provider_call_after_commit = False
            self.state.provider_commit_ambiguity_seen = True
            if self.state.provider_commit_residual_relationships is not None:
                self.state.target_relationships = list(
                    self.state.provider_commit_residual_relationships
                )
            for field, value in self.state.provider_commit_residual_mutations.items():
                setattr(self.state, field, value)
            raise RuntimeError("injected provider-call commit ambiguity")

    def rollback(self) -> None:
        if self.state.fail_provider_rollback:
            self.state.fail_provider_rollback = False
            raise RuntimeError("injected bootstrap rollback failure")
        if self._snapshot is not None and not self.state.provider_rollback_leaves_target:
            (
                self.state.target_profile,
                self.state.target_relationships,
                self.state.target_dependencies,
                self.state.target_sessions,
            ) = self._snapshot
        self._snapshot = None

    def close(self) -> None:
        self.state.retained_backend_active = False


class _DeployerConnection:
    def __init__(self, state: _State, *, database_name: str) -> None:
        self.state = state
        self.database_name = database_name

    def __enter__(self) -> _DeployerConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self) -> _DeployerCursor:
        return _DeployerCursor(self.state, database_name=self.database_name)


def _client(state: _State, *, secret: str = "one-use-secret") -> MagicMock:
    client = MagicMock()
    client.config.host = "https://reviewed.cloud.databricks.com"
    client.current_user.me.return_value = SimpleNamespace(
        application_id="deployer",
        user_name="deployer",
    )
    client.database.get_database_instance.return_value = SimpleNamespace(
        name=_INSTANCE,
        read_write_dns="reviewed.database.example"
    )
    client.database.generate_database_credential.return_value = SimpleNamespace(
        token="database-token"
    )

    def principal() -> SimpleNamespace:
        return SimpleNamespace(
            id=_CREATOR_SCIM_ID,
            application_id=state.bootstrap_application_id,
            display_name=_signed_display_name(),
            external_id=None,
            active=state.bootstrap_sp_active,
            groups=[],
            roles=[],
            entitlements=[],
        )

    def create_sp(**kwargs: Any) -> SimpleNamespace:
        if kwargs.get("active") is False:
            marker_id = f"orphan-marker-{len(state.orphan_tombstones) + 1}"
            marker = SimpleNamespace(
                id=marker_id,
                application_id=kwargs["application_id"],
                display_name=kwargs["display_name"],
                external_id=None,
                active=False,
                groups=[],
                roles=[],
                entitlements=[],
            )
            state.orphan_tombstones[marker_id] = marker
            if state.tombstone_create_commit_then_error:
                state.tombstone_create_commit_then_error = False
                raise RuntimeError("ambiguous tombstone create after commit")
            return marker
        assert kwargs == {
            "display_name": _signed_display_name(),
            "active": True,
        }
        state.actions.append(("service_principal_create", state.advisory_lock_checks))
        state.bootstrap_sp_present = True
        if state.create_sp_commit_then_error:
            raise RuntimeError("ambiguous service-principal create")
        return principal()

    client.service_principals.create.side_effect = create_sp

    def list_principals(**kwargs: Any) -> Any:
        items = (
            [principal()]
            if state.bootstrap_sp_present
            and not state.hide_principal_from_list
            and not (state.hide_principal_after_patch and not state.bootstrap_sp_active)
            else []
        )
        items.extend(state.orphan_tombstones.values())
        filter_expr = str(kwargs.get("filter") or "")
        if filter_expr.startswith("displayName eq '"):
            expected = filter_expr.removeprefix("displayName eq '").removesuffix("'")
            items = [item for item in items if item.display_name == expected]
        elif filter_expr.startswith("externalId eq '"):
            expected = filter_expr.removeprefix("externalId eq '").removesuffix("'")
            items = [item for item in items if item.external_id == expected]
        return iter(items)

    client.service_principals.list.side_effect = list_principals

    def get_principal(principal_id: str) -> SimpleNamespace:
        if principal_id in state.orphan_tombstones:
            return state.orphan_tombstones[principal_id]
        if principal_id != _CREATOR_SCIM_ID:
            raise NotFound("unknown temporary principal")
        if not state.bootstrap_sp_present or (
            state.hide_principal_after_patch and not state.bootstrap_sp_active
        ):
            raise NotFound("hidden temporary principal")
        return principal()

    client.service_principals.get.side_effect = get_principal

    account_client = MagicMock()

    def get_account_principal(principal_id: str) -> SimpleNamespace:
        principal = get_principal(principal_id)
        if principal_id not in state.orphan_tombstones:
            return principal
        account_contract = vars(principal).copy()
        account_contract["active"] = True
        return SimpleNamespace(**account_contract)

    account_client.service_principals.get.side_effect = get_account_principal

    def list_account_principals(**kwargs: Any) -> Any:
        items = [principal()] if state.bootstrap_sp_present else []
        items.extend(state.orphan_tombstones.values())
        filter_expr = str(kwargs.get("filter") or "")
        if filter_expr.startswith('displayName sw "'):
            expected = filter_expr.removeprefix('displayName sw "').removesuffix('"')
            items = [item for item in items if item.display_name.startswith(expected)]
        elif filter_expr.startswith('applicationId eq "'):
            expected = filter_expr.removeprefix('applicationId eq "').removesuffix('"')
            items = [item for item in items if item.application_id == expected]
        return iter(items)

    account_client.service_principals.list.side_effect = list_account_principals
    account_client.workspaces.list.side_effect = lambda: iter([SimpleNamespace(workspace_id=42)])

    def list_assignments(workspace_id: int) -> Any:
        assert workspace_id == 42
        principal_ids = list(state.orphan_tombstones)
        if state.bootstrap_sp_present:
            principal_ids.append(_CREATOR_SCIM_ID)
        return iter(
            SimpleNamespace(
                error=None,
                principal=SimpleNamespace(principal_id=principal_id),
            )
            for principal_id in principal_ids
        )

    account_client.workspace_assignment.list.side_effect = list_assignments
    client.get_workspace_id.return_value = 42
    client._account_client = account_client

    def patch_sp(*, id: str, operations: list[Any], schemas: list[Any]) -> None:
        state.actions.append(("service_principal_disable", state.advisory_lock_checks))
        assert id == _CREATOR_SCIM_ID
        assert len(operations) == 1
        assert str(getattr(operations[0], "path", "")) == "active"
        assert getattr(operations[0], "value", None) is False
        assert len(schemas) == 1
        if state.fail_patch_attempts:
            state.fail_patch_attempts -= 1
            raise RuntimeError("INTERNAL_ERROR: transient SCIM propagation")
        state.bootstrap_sp_active = False

    client.service_principals.patch.side_effect = patch_sp

    def create_secret(_id: str, *, lifetime: str | None = None) -> SimpleNamespace:
        assert lifetime == "600s"
        state.actions.append(("secret_create", state.advisory_lock_checks))
        state.bootstrap_secrets = ["secret-id"]
        return SimpleNamespace(id="secret-id", secret=secret)

    def list_secrets(_id: str) -> Any:
        if _id in state.orphan_tombstones:
            return iter([])
        if state.fail_secret_list:
            raise RuntimeError("injected secret-list failure")
        return iter(SimpleNamespace(id=value) for value in state.bootstrap_secrets)

    def delete_secret(_sp_id: str, secret_id: str) -> None:
        state.actions.append(("secret_delete", state.advisory_lock_checks))
        if state.fail_secret_delete:
            raise RuntimeError("injected secret-delete failure")
        if secret_id in state.bootstrap_secrets:
            state.bootstrap_secrets.remove(secret_id)

    client.service_principal_secrets_proxy.create.side_effect = create_secret
    client.service_principal_secrets_proxy.list.side_effect = list_secrets
    client.service_principal_secrets_proxy.delete.side_effect = delete_secret
    account_client.service_principal_secrets.list.side_effect = list_secrets
    account_client.service_principal_secrets.delete.side_effect = delete_secret
    client.apps.list.return_value = iter([])

    def create_role(_instance: str, role: Any) -> None:
        state.actions.append(("bootstrap_role_create", state.advisory_lock_checks))
        assert any(
            "FROM pg_event_trigger event_trigger" in statement
            for statement in state.deployer_statements[-3:]
        )
        assert role.name == _CREATOR
        assert role.attributes.createrole is True
        assert role.attributes.createdb is False
        assert role.attributes.bypassrls is False
        state.created_creator_role = True
        state.creator_control_plane_present = True
        state.creator_profile = bootstrap._BOOTSTRAP_API_PROFILE

    def delete_role(_instance: str, role_name: str) -> None:
        # Absent-database recovery has no SQL cursor or database-local event
        # trigger inventory. Every SQL-available role deletion must still be
        # immediately preceded by the managed trigger postflight.
        if state.deployer_statements:
            assert any(
                "FROM pg_event_trigger event_trigger" in statement
                for statement in state.deployer_statements[-6:]
            )
        if role_name == _TARGET:
            state.actions.append(("target_delete", state.advisory_lock_checks))
            state.deployer_statements.append("CONTROL_PLANE_DELETE target")
            assert state.target_profile is not None
            if state.target_session_reappears_on_delete:
                state.target_session_reappears_on_delete = False
                state.target_sessions.append(9988)
                raise RuntimeError("injected target session at delete boundary")
            if state.fail_target_role_delete_attempts:
                state.fail_target_role_delete_attempts -= 1
                raise RuntimeError("injected target-role deletion failure")
            state.target_deleted = True
            state.target_profile = None
            state.target_relationships = []
            state.target_dependencies = []
            state.target_sessions = []
            return
        assert role_name == _CREATOR
        state.actions.append(("creator_delete", state.advisory_lock_checks))
        state.deployer_statements.append("CONTROL_PLANE_DELETE creator")
        if state.fail_creator_role_delete:
            if state.sessions_reappear_on_delete_failure and not state.creator_sessions:
                state.creator_sessions.append(9911)
            raise RuntimeError("injected creator-role cleanup failure")
        state.deleted_creator_role = True
        state.creator_control_plane_present = False
        state.creator_profile = None
        state.target_relationships = []
        state.creator_role_comment = None

    def delete_sp(sp_id: str) -> None:
        raise AssertionError(f"workspace unassignment is forbidden for {sp_id}")

    def delete_account_sp(sp_id: str) -> None:
        state.actions.append(("account_principal_delete", state.advisory_lock_checks))
        if state.fail_account_principal_delete:
            raise RuntimeError("injected account-principal cleanup failure")
        if sp_id in state.orphan_tombstones:
            state.orphan_tombstones.pop(sp_id)
            if state.tombstone_delete_commit_then_error:
                state.tombstone_delete_commit_then_error = False
                raise RuntimeError("ambiguous tombstone delete after commit")
            return
        assert sp_id == _CREATOR_SCIM_ID
        if state.fail_creator_sp_delete:
            raise RuntimeError("injected creator-SP cleanup failure")
        state.deleted_creator_sp = True
        state.bootstrap_sp_present = False

    client.database.create_database_instance_role.side_effect = create_role

    def list_roles(_name: str) -> Any:
        roles = (
            [
                SimpleNamespace(
                    name=_CREATOR,
                    identity_type=SimpleNamespace(value="SERVICE_PRINCIPAL"),
                )
            ]
            if state.creator_control_plane_present
            else []
        )
        if state.target_profile is not None:
            roles.append(
                SimpleNamespace(
                    name=_TARGET,
                    identity_type=SimpleNamespace(value=state.target_identity_type),
                )
            )
        return iter(roles)

    client.database.list_database_instance_roles.side_effect = list_roles
    client.database.delete_database_instance_role.side_effect = delete_role
    client.service_principals.delete.side_effect = delete_sp
    account_client.service_principals.delete.side_effect = delete_account_sp
    return client


def _run(
    state: _State,
    *,
    secret: str = "one-use-secret",
    allow_absent_managed_event_triggers: bool = True,
) -> None:
    class _DatabaseAuthRejected(RuntimeError):
        sqlstate = "28P01"

    def connect(**kwargs: Any) -> Any:
        database_name = str(kwargs["dbname"])
        database_user = str(kwargs["user"])
        if database_user == _CREATOR:
            assert database_name == _DATABASE
            application_name = str(kwargs["application_name"])
            if application_name.startswith("mip-bootstrap-reuse-"):
                raise _DatabaseAuthRejected("revoked database token")
            assert application_name.startswith("mip-bootstrap-admission-")
            assert kwargs["autocommit"] is True
            return _BootstrapConnection(
                state,
                autocommit=True,
                application_name=application_name,
            )
        assert database_user == "deployer"
        assert kwargs["autocommit"] is True
        if state.fail_fresh_deployer_connect and state.created_creator_role:
            raise RuntimeError("injected fresh deployer connection failure")
        return _DeployerConnection(state, database_name=database_name)

    def workspace_factory(**kwargs: Any) -> SimpleNamespace:
        client_id = str(kwargs.get("client_id") or "")
        assert kwargs == {
            "host": "https://reviewed.cloud.databricks.com",
            "client_id": client_id,
            "client_secret": (
                secret if client_id == _CREATOR else "control-secret-never-log"
            ),
            "auth_type": "oauth-m2m",
        }
        assert client_id in {_CREATOR, _CONTROL}

        def generate_database_credential(**_kwargs: Any) -> SimpleNamespace:
            if state.deleted_creator_sp:
                raise ValueError("invalid_client: retired bootstrap principal")
            return SimpleNamespace(
                token="database-token",
                expiration_time=(datetime.now(UTC) + timedelta(minutes=60)).isoformat(),
            )

        def get_database_instance(_name: str) -> SimpleNamespace:
            if client_id == _CREATOR and state.deleted_creator_sp:
                raise ValueError("invalid_client: retired bootstrap principal")
            return SimpleNamespace(
                name="mip-app-state",
                read_write_dns="reviewed.database.example",
            )

        return SimpleNamespace(
            config=SimpleNamespace(
                auth_type="oauth-m2m",
                oauth_token=lambda: SimpleNamespace(
                    jwt_claims=lambda: {
                        "exp": (datetime.now(UTC) + timedelta(minutes=60)).timestamp()
                    }
                ),
            ),
            current_user=SimpleNamespace(
                me=lambda: SimpleNamespace(application_id=client_id, user_name=client_id)
            ),
            database=SimpleNamespace(
                get_database_instance=get_database_instance,
                generate_database_credential=generate_database_credential,
            ),
        )

    client = _client(state, secret=secret)
    with patch.object(
        bootstrap_orchestration,
        "structured_database_auth_connect",
        connect,
    ), patch.object(
        bootstrap_orchestration,
        "_wait_through_bootstrap_auth_expiry",
        lambda *_args, **_kwargs: None,
    ):
        bootstrap.create_login_only_role(
            client,
            _DeployerCursor(state),
            account_client=client._account_client,
            instance_name="mip-app-state",
            database_name="mip_app_state",
            application_id=_TARGET,
            service_principal_id=_TARGET_SCIM_ID,
            connect=connect,
            workspace_client_factory=workspace_factory,
            allow_absent_managed_event_triggers=allow_absent_managed_event_triggers,
        )


def _seed_stale_creator(state: _State) -> None:
    state.bootstrap_sp_present = True
    state.bootstrap_sp_active = True
    state.bootstrap_secrets = ["stale-secret-id"]
    state.created_creator_role = True
    state.creator_control_plane_present = True
    state.creator_profile = bootstrap._BOOTSTRAP_API_PROFILE
    state.creator_role_comment = _EXTERNAL_ID
    state.wrapper_schema_exists = True
    state.wrapper_function_exists = True
    state.wrapper_schema_usage = True
    state.wrapper_function_execute = True
    state.target_profile = bootstrap.SAFE_OAUTH_PROFILE
    state.target_relationships = [(_TARGET, _CREATOR, True, False, False, "cloud_admin")]


def _seed_v2_tombstone(state: _State) -> str:
    signature = Ed25519PrivateKey.from_private_bytes(
        tombstone._decode(_SIGNING_KEY, length=32)
    ).sign(
        tombstone._v2_message(
            base_external_id=_EXTERNAL_ID,
            application_id=_CREATOR,
        )
    )
    display_name, marker_application_id = tombstone._render_v2_contract(
        base_external_id=_EXTERNAL_ID,
        application_id=_CREATOR,
        signature=signature,
    )
    marker_id = "78451793422043"
    state.orphan_tombstones[marker_id] = SimpleNamespace(
        id=marker_id,
        application_id=marker_application_id,
        display_name=display_name,
        external_id=None,
        active=False,
        groups=[],
        roles=[],
        entitlements=[],
    )
    return marker_id


def test_one_use_creator_leaves_safe_role_and_empty_membership_graph() -> None:
    state = _State()

    _run(state)

    assert state.target_profile == bootstrap.SAFE_OAUTH_PROFILE
    assert state.target_relationships == []
    assert state.created_creator_role is True
    assert state.deleted_creator_role is True
    assert state.deleted_creator_sp is True
    assert state.wrapper_schema_exists is False
    assert state.wrapper_function_exists is False
    assert state.wrapper_schema_usage is False
    assert state.wrapper_function_execute is False
    action_names = [action for action, _check in state.actions]
    first_secret_delete = action_names.index("secret_delete")
    account_deletes = [
        index for index, action in enumerate(action_names) if action == "account_principal_delete"
    ]
    assert first_secret_delete < account_deletes[0] < action_names.index("creator_delete")
    assert action_names.index("creator_delete") < account_deletes[-1]
    assert not any("GRANT CREATE ON DATABASE" in item for item in state.deployer_statements)
    assert not any("SCHEMA public TO" in item for item in state.deployer_statements)
    wrapper_ddl = next(
        statement
        for statement in state.deployer_statements
        if statement.startswith("CREATE FUNCTION")
    )
    assert "LANGUAGE sql VOLATILE PARALLEL UNSAFE SECURITY INVOKER" in wrapper_ddl
    assert "BEGIN ATOMIC" in wrapper_ddl
    assert f"CURRENT_USER = '{_CREATOR}'::pg_catalog.name" in wrapper_ddl
    assert f"SESSION_USER = '{_CREATOR}'::pg_catalog.name" in wrapper_ddl
    assert f"'{_TARGET}'::pg_catalog.text" in wrapper_ddl
    assert "SECURITY DEFINER" not in wrapper_ddl
    assert "LANGUAGE plpgsql" not in wrapper_ddl
    assert 'SET LOCAL ROLE "pg_database_owner"' in state.deployer_statements
    provider_revoke = next(
        statement
        for statement in state.deployer_statements
        if statement.startswith("REVOKE ALL PRIVILEGES ON SCHEMA")
    )
    assert 'FROM "databricks_superuser", "databricks_gateway" CASCADE' in provider_revoke
    assert "databricks_writer_42" not in provider_revoke
    assert "databricks_reader_42" not in provider_revoke
    executor_preflight = next(
        index
        for index, statement in enumerate(state.deployer_statements)
        if "has_database_privilege(current_user, current_database(), 'CREATE')" in statement
    )
    create_schema = next(
        index
        for index, statement in enumerate(state.deployer_statements)
        if statement.startswith("CREATE SCHEMA")
    )
    assume_owner = state.deployer_statements.index('SET LOCAL ROLE "pg_database_owner"')
    assert executor_preflight < create_schema < assume_owner

    previous_mutation = -1
    for index, statement in enumerate(state.deployer_statements):
        if statement.startswith(("CREATE ", "GRANT ", "REVOKE ", "COMMENT ", "DROP ")):
            assert any(
                "FROM pg_event_trigger event_trigger" in candidate
                for candidate in state.deployer_statements[previous_mutation + 1 : index]
            )
            previous_mutation = index


@pytest.mark.parametrize(
    "missing_privilege",
    ("deployer_database_create", "deployer_can_set_database_owner"),
)
def test_wrapper_creation_rejects_incomplete_deployer_owner_boundary(
    missing_privilege: str,
) -> None:
    state = _State()
    setattr(state, missing_privilege, False)

    with pytest.raises(RuntimeError, match="wrapper creation executor contract drifted"):
        _run(state)

    assert state.wrapper_schema_exists is False
    assert not any(action == "provider_call" for action, _ in state.actions)
    assert state.creator_profile is None
    assert state.bootstrap_sp_present is False


def test_wrapper_guard_makes_every_non_bootstrap_caller_inert() -> None:
    body = bootstrap_wrapper._wrapper_body(_TARGET, _CREATOR)

    assert body.startswith("BEGIN ATOMIC\n RETURN ( SELECT public.databricks_create_role(")
    assert f"CURRENT_USER = '{_CREATOR}'::pg_catalog.name" in body
    assert f"SESSION_USER = '{_CREATOR}'::pg_catalog.name" in body
    assert f"'{_TARGET}'::pg_catalog.text" in body
    assert "WHERE" in body
    assert "ELSE" not in body


def test_missing_managed_event_trigger_contract_blocks_first_database_mutation() -> None:
    state = _State()

    with pytest.raises(RuntimeError, match="global event-trigger inventory mismatch"):
        _run(state, allow_absent_managed_event_triggers=False)

    assert state.created_creator_role is False
    assert state.creator_control_plane_present is False
    assert state.wrapper_schema_exists is False
    assert state.bootstrap_sp_present is False


@pytest.mark.parametrize(
    ("schema_usage", "function_execute"),
    [(False, False), (True, False), (False, True), (True, True)],
)
def test_recovery_converges_all_four_finite_wrapper_acl_states(
    schema_usage: bool,
    function_execute: bool,
) -> None:
    state = _State()
    _seed_stale_creator(state)
    state.wrapper_schema_usage = schema_usage
    state.wrapper_function_execute = function_execute
    state.creator_sessions = [7101]
    state.assert_session_quarantine_order = True

    recovery.recover_stale_bootstrap_identities(
        _client(state),
        _DeployerCursor(state),
        instance_name=_INSTANCE,
        database_name=_DATABASE,
        target_application_id=_TARGET,
        allow_absent_managed_event_triggers=True,
    )

    assert state.creator_profile is None
    assert state.wrapper_schema_exists is False
    assert state.wrapper_function_exists is False
    assert state.wrapper_schema_usage is False
    assert state.wrapper_function_execute is False
    assert state.creator_sessions == []


@pytest.mark.parametrize(
    ("database_create", "public_usage"),
    [(False, False), (True, False), (False, True), (True, True)],
)
def test_recovery_converges_all_four_disjoint_legacy_acl_states(
    database_create: bool,
    public_usage: bool,
) -> None:
    state = _State()
    _seed_stale_creator(state)
    state.wrapper_schema_exists = False
    state.wrapper_function_exists = False
    state.wrapper_schema_usage = False
    state.wrapper_function_execute = False
    state.creator_database_create = database_create
    state.creator_public_usage = public_usage

    recovery.recover_stale_bootstrap_identities(
        _client(state),
        _DeployerCursor(state),
        instance_name=_INSTANCE,
        database_name=_DATABASE,
        target_application_id=_TARGET,
        allow_absent_managed_event_triggers=True,
    )

    assert state.creator_profile is None
    assert state.creator_database_create is False
    assert state.creator_public_usage is False


@pytest.mark.parametrize(
    ("field", "value", "error_match"),
    [
        ("creator_database_grantable", True, "database ACL drifted"),
        ("creator_public_grantable", True, "public-schema ACL drifted"),
        ("creator_database_privilege", "CONNECT", "database ACL drifted"),
        ("creator_public_privilege", "CREATE", "public-schema ACL drifted"),
    ],
)
def test_recovery_rejects_legacy_acl_privilege_or_grant_option_drift(
    field: str,
    value: Any,
    error_match: str,
) -> None:
    state = _State()
    _seed_stale_creator(state)
    state.wrapper_schema_exists = False
    state.wrapper_function_exists = False
    if field.startswith("creator_database"):
        state.creator_database_create = True
    else:
        state.creator_public_usage = True
    setattr(state, field, value)

    with pytest.raises(RuntimeError, match="cleanup was incomplete") as exc_info:
        recovery.recover_stale_bootstrap_identities(
            _client(state),
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
            allow_absent_managed_event_triggers=True,
        )

    assert error_match in str(exc_info.value)
    assert state.creator_profile is not None
    assert state.bootstrap_sp_present is False
    assert state.bootstrap_secrets == []
    assert state.deleted_creator_role is False


@pytest.mark.parametrize(
    ("schema_usage", "function_execute"),
    [(False, False), (True, False), (False, True), (True, True)],
)
@pytest.mark.parametrize(
    ("database_create", "public_usage"),
    [(True, False), (False, True), (True, True)],
)
def test_recovery_rejects_every_mixed_wrapper_and_legacy_acl_state(
    schema_usage: bool,
    function_execute: bool,
    database_create: bool,
    public_usage: bool,
) -> None:
    state = _State()
    _seed_stale_creator(state)
    state.wrapper_schema_usage = schema_usage
    state.wrapper_function_execute = function_execute
    state.creator_database_create = database_create
    state.creator_public_usage = public_usage

    with pytest.raises(RuntimeError, match="cleanup was incomplete"):
        recovery.recover_stale_bootstrap_identities(
            _client(state),
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
            allow_absent_managed_event_triggers=True,
        )

    assert state.creator_profile is not None
    assert state.bootstrap_sp_present is False
    assert state.bootstrap_secrets == []
    assert state.deleted_creator_role is False
    assert state.wrapper_schema_exists is True
    assert state.creator_database_create is database_create
    assert state.creator_public_usage is public_usage


def test_recovery_uses_signed_authority_without_sql_role_ownership() -> None:
    state = _State()
    _seed_stale_creator(state)
    state.creator_role_comment = None
    state.creator_sessions = [7102]

    recovery.recover_stale_bootstrap_identities(
        _client(state),
        _DeployerCursor(state),
        instance_name=_INSTANCE,
        database_name=_DATABASE,
        target_application_id=_TARGET,
        allow_absent_managed_event_triggers=True,
    )

    assert not any(
        statement.startswith(("ALTER ROLE", "COMMENT ON ROLE"))
        for statement in state.deployer_statements
    )
    assert state.creator_sessions == []
    assert state.creator_profile is None
    assert state.deleted_creator_role is True


@pytest.mark.parametrize(
    ("drift_field", "drift_value"),
    [
        ("creator_profile", (True, True, False, True, False, True, True)),
        ("creator_settings", (1, None, "********", None)),
        ("creator_settings", (-1, "2027-01-01", "********", None)),
        ("creator_settings", (-1, None, "unexpected", None)),
        ("creator_settings", (-1, None, "********", ["search_path=public"])),
        ("creator_database_settings", [(42, 5101, ["statement_timeout=0"])]),
        (
            "target_relationships",
            [("unreviewed-parent", _CREATOR, True, False, False, "cloud_admin")],
        ),
        ("creator_dependencies_override", [(42, "pg_class", 9901, 0, "a")]),
    ],
)
def test_mutable_bootstrap_drift_is_fenced_and_drained_but_never_deleted(
    drift_field: str,
    drift_value: Any,
) -> None:
    state = _State()
    _seed_stale_creator(state)
    setattr(state, drift_field, drift_value)
    state.creator_sessions = [7103]

    with pytest.raises(RuntimeError, match="cleanup was incomplete"):
        recovery.recover_stale_bootstrap_identities(
            _client(state),
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
            allow_absent_managed_event_triggers=True,
        )

    assert state.creator_profile is not None
    assert state.creator_sessions == []
    assert state.deleted_creator_role is False
    assert state.bootstrap_sp_present is False
    assert state.bootstrap_secrets == []
    assert state.wrapper_schema_exists is True


def test_recovery_contract_rejects_dependency_not_bound_to_exact_acl() -> None:
    state = _State()
    _seed_stale_creator(state)
    state.creator_dependencies_override = [(42, "pg_class", 9901, 0, "a")]

    with pytest.raises(RuntimeError, match="cleanup was incomplete"):
        recovery.recover_stale_bootstrap_identities(
            _client(state),
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
            allow_absent_managed_event_triggers=True,
        )

    assert state.deleted_creator_role is False
    assert state.bootstrap_sp_present is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("wrapper_prokind", "p"),
        ("wrapper_proallargtypes", [25]),
        ("wrapper_proargmodes", ["o"]),
        ("wrapper_proargnames", ["leaked_output"]),
    ],
)
def test_wrapper_function_shape_drift_is_quarantined_not_cleaned(
    field: str,
    value: Any,
) -> None:
    state = _State()
    _seed_stale_creator(state)
    setattr(state, field, value)

    with pytest.raises(RuntimeError, match="cleanup was incomplete"):
        recovery.recover_stale_bootstrap_identities(
            _client(state),
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
            allow_absent_managed_event_triggers=True,
        )

    assert state.creator_profile is not None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("wrapper_language", "plpgsql"),
        ("wrapper_security_definer", True),
        ("wrapper_prosqlbody_present", False),
    ],
)
def test_wrapper_execution_contract_drift_is_quarantined_not_cleaned(
    field: str,
    value: Any,
) -> None:
    state = _State()
    _seed_stale_creator(state)
    setattr(state, field, value)

    with pytest.raises(RuntimeError, match="cleanup was incomplete"):
        recovery.recover_stale_bootstrap_identities(
            _client(state),
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
            allow_absent_managed_event_triggers=True,
        )

    assert state.wrapper_schema_exists is True
    assert state.wrapper_function_exists is True
    assert state.deleted_creator_role is False


def test_wrapper_caller_guard_or_target_body_drift_is_quarantined() -> None:
    state = _State()
    _seed_stale_creator(state)
    exact = bootstrap_wrapper_contract.canonical_wrapper_definition(
        schema_name=_WRAPPER_SCHEMA,
        target_application_id=_TARGET,
        bootstrap_application_id=_CREATOR,
    )
    state.wrapper_definition_override = exact.replace(
        f"SESSION_USER = '{_CREATOR}'::name",
        "SESSION_USER = 'attacker'::name",
    )

    with pytest.raises(RuntimeError, match="cleanup was incomplete"):
        recovery.recover_stale_bootstrap_identities(
            _client(state),
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
            allow_absent_managed_event_triggers=True,
        )

    assert state.wrapper_schema_exists is True
    assert state.deleted_creator_role is False


def test_wrapper_provider_function_dependency_drift_is_quarantined() -> None:
    state = _State()
    _seed_stale_creator(state)
    state.wrapper_provider_dependencies_override = [
        (
            "pg_proc",
            3301,
            "pg_proc",
            "public",
            "unreviewed_role_creator",
            "text, text",
            "n",
        )
    ]

    with pytest.raises(RuntimeError, match="cleanup was incomplete"):
        recovery.recover_stale_bootstrap_identities(
            _client(state),
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
            allow_absent_managed_event_triggers=True,
        )

    assert state.wrapper_schema_exists is True
    assert state.deleted_creator_role is False
    assert state.bootstrap_sp_present is False
    assert state.bootstrap_secrets == []
    assert state.deleted_creator_role is False
    assert state.wrapper_schema_exists is True


def test_missing_role_function_fails_after_stale_identity_recovery() -> None:
    state = _State()
    _seed_stale_creator(state)
    state.function_exists = False

    with pytest.raises(RuntimeError, match="function contract is absent"):
        _run(state)

    assert state.bootstrap_sp_present is False
    assert state.creator_profile is None
    assert state.target_relationships == []


def test_role_function_metadata_drift_fails_before_identity_creation() -> None:
    state = _State()
    state.function_source_sha256 = "0" * 64

    with pytest.raises(RuntimeError, match="function contract drifted"):
        _run(state)

    assert state.bootstrap_sp_present is False
    assert state.created_creator_role is False


def test_provider_public_schema_drift_fails_before_identity_creation() -> None:
    state = _State()
    state.public_schema_acl.append(("PUBLIC", "USAGE", False, "pg_database_owner"))

    with pytest.raises(RuntimeError, match="public-schema ACL mismatch"):
        _run(state)

    assert state.bootstrap_sp_present is False
    assert state.created_creator_role is False


@pytest.mark.parametrize(
    ("failure_flag", "error_match"),
    [
        ("fail_schema_grant_after_commit", "schema-grant commit ambiguity"),
        ("fail_function_grant_after_commit", "function-grant commit ambiguity"),
    ],
)
def test_ambiguous_bootstrap_acl_grant_is_recovered_from_each_finite_state(
    failure_flag: str,
    error_match: str,
) -> None:
    state = _State()
    setattr(state, failure_flag, True)

    if failure_flag.startswith("fail_drop_"):
        _run(state)
    else:
        with pytest.raises(RuntimeError, match=error_match):
            _run(state)

    assert state.creator_profile is None
    assert state.creator_control_plane_present is False
    assert state.bootstrap_sp_present is False
    assert state.wrapper_schema_exists is False
    assert state.wrapper_function_exists is False
    assert state.wrapper_schema_usage is False
    assert state.wrapper_function_execute is False


@pytest.mark.parametrize(
    ("failure_flag", "error_match"),
    [
        ("fail_create_schema_after_commit", "create-schema commit ambiguity"),
        ("fail_provider_revoke_after_commit", "provider-revoke commit ambiguity"),
        ("fail_create_function_after_commit", "create-function commit ambiguity"),
        ("fail_drop_function_after_commit", "drop-function commit ambiguity"),
        ("fail_drop_schema_after_commit", "drop-schema commit ambiguity"),
    ],
)
def test_wrapper_ddl_commit_ambiguity_recovers_without_object_or_acl_residue(
    failure_flag: str,
    error_match: str,
) -> None:
    state = _State()
    setattr(state, failure_flag, True)

    if failure_flag.startswith("fail_drop_"):
        _run(state)
    else:
        with pytest.raises(RuntimeError, match=error_match):
            _run(state)

    assert state.creator_profile is None
    assert state.bootstrap_sp_present is False
    assert state.wrapper_schema_exists is False
    assert state.wrapper_function_exists is False
    assert state.wrapper_schema_usage is False
    assert state.wrapper_function_execute is False


def test_wrapper_publish_commit_response_loss_uses_fresh_cleanup_connection() -> None:
    state = _State()
    state.fail_wrapper_commit_after_apply = True

    with pytest.raises(RuntimeError, match="wrapper commit ambiguity"):
        _run(state)

    assert state.creator_profile is None
    assert state.bootstrap_sp_present is False
    assert state.wrapper_schema_exists is False
    assert state.wrapper_function_exists is False
    assert state.wrapper_schema_usage is False
    assert state.wrapper_function_execute is False


def test_wrapper_teardown_commit_response_loss_retries_atomic_outcome() -> None:
    state = _State()
    state.fail_wrapper_teardown_commit_after_apply = True

    _run(state)

    assert state.creator_profile is None
    assert state.bootstrap_sp_present is False
    assert state.wrapper_schema_exists is False
    assert state.wrapper_function_exists is False


def test_fresh_control_failure_retires_identity_and_retains_sql_recovery_residue() -> None:
    state = _State()
    state.fail_fresh_deployer_connect = True

    with pytest.raises(RuntimeError, match="fresh deployer connection failure"):
        _run(state)

    assert state.bootstrap_sp_present is False
    assert state.deleted_creator_sp is True
    assert state.bootstrap_secrets == []
    assert state.creator_profile == bootstrap._BOOTSTRAP_API_PROFILE
    assert state.wrapper_schema_exists is True
    assert state.wrapper_function_exists is True
    assert state.orphan_tombstones


def test_commit_ambiguous_exact_provider_product_forward_converges() -> None:
    state = _State()
    state.fail_provider_call_after_commit = True

    _run(state)

    assert state.target_profile == bootstrap.SAFE_OAUTH_PROFILE
    assert state.target_relationships == []
    assert state.creator_profile is None
    assert state.creator_control_plane_present is False
    assert state.bootstrap_sp_present is False
    assert state.wrapper_schema_exists is False
    assert state.wrapper_function_exists is False
    assert state.wrapper_schema_usage is False
    assert state.wrapper_function_execute is False


def test_exact_commit_response_loss_with_rollback_failure_forward_converges() -> None:
    state = _State()
    state.fail_provider_call_after_commit = True
    state.fail_provider_rollback = True

    _run(state)

    assert state.target_profile == bootstrap.SAFE_OAUTH_PROFILE
    assert state.target_deleted is False
    assert state.creator_profile is None
    assert state.bootstrap_sp_present is False


def test_definite_provider_rejection_preserves_stable_absence() -> None:
    state = _State()
    state.fail_provider_call_before_statement = True

    with pytest.raises(RuntimeError, match="provider rejection"):
        _run(state)

    assert state.target_profile is None
    assert state.target_deleted is False
    assert state.creator_profile is None


def test_provider_socket_loss_after_statement_rolls_back_to_stable_absence() -> None:
    state = _State()
    state.fail_provider_call_after_statement = True

    with pytest.raises(RuntimeError, match="transport failure"):
        _run(state)

    assert state.target_profile is None
    assert state.target_deleted is False
    assert state.creator_profile is None


@pytest.mark.parametrize("rollback_mode", ["failed", "residual"])
def test_provider_statement_loss_with_exact_residual_forward_converges(
    rollback_mode: str,
) -> None:
    state = _State()
    state.fail_provider_call_after_statement = True
    if rollback_mode == "failed":
        state.fail_provider_rollback = True
    else:
        state.provider_rollback_leaves_target = True

    _run(state)

    assert state.target_profile == bootstrap.SAFE_OAUTH_PROFILE
    assert state.target_deleted is False
    assert state.creator_profile is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("target_profile", bootstrap.LEGACY_API_OAUTH_PROFILE),
        ("target_settings", (1, None, "********", None)),
        ("target_settings", (-1, "2027-01-01", "********", None)),
        ("target_settings", (-1, None, "unexpected", None)),
        ("target_settings", (-1, None, "********", ["search_path=public"])),
        ("target_database_settings", [(42, 5102, ["statement_timeout=0"])]),
        ("target_dependencies", [(42, "pg_class", 9901, 0, "a")]),
        ("target_label", "id=wrong,type=service_principal"),
        ("target_identity_type", "USER"),
    ],
)
def test_each_residual_target_drift_blocks_without_target_deletion(
    field: str,
    value: Any,
) -> None:
    state = _State()
    state.fail_provider_call_after_commit = True
    state.provider_commit_residual_mutations[field] = value

    with pytest.raises(RuntimeError, match="target reconciliation"):
        _run(state)

    assert state.target_profile is not None
    assert state.target_deleted is False
    assert not any(action == "target_delete" for action, _ in state.actions)
    assert not any(statement.startswith("ALTER ROLE") for statement in state.deployer_statements)


def test_transient_first_residual_read_never_deletes_committed_exact_target() -> None:
    state = _State()
    state.fail_provider_call_after_commit = True
    state.reconciliation_target_profile_read_failures = 1

    _run(state)

    assert state.target_profile == bootstrap.SAFE_OAUTH_PROFILE
    assert state.target_deleted is False
    assert not any(action == "target_delete" for action, _ in state.actions)


def test_stably_drifted_residual_target_is_session_drained_not_deleted() -> None:
    state = _State()
    state.fail_provider_call_after_commit = True
    state.provider_commit_residual_relationships = [
        (_TARGET, "unreviewed-creator", True, False, False, "cloud_admin")
    ]

    with pytest.raises(RuntimeError, match="residual state was not stably exact or absent"):
        _run(state)

    assert state.target_profile is not None
    assert state.target_deleted is False
    assert not any(action == "target_delete" for action, _ in state.actions)
    assert not any(statement.startswith("ALTER ROLE") for statement in state.deployer_statements)


def test_unsafe_sql_created_profile_fails_and_cleans_creator() -> None:
    state = _State()
    state.create_profile = bootstrap.LEGACY_API_OAUTH_PROFILE

    with pytest.raises(RuntimeError, match="unsafe role attributes"):
        _run(state)

    assert state.deleted_creator_role is True
    assert state.deleted_creator_sp is True


def test_unreviewed_bootstrap_membership_fails_and_cleans_creator() -> None:
    state = _State()
    state.create_relationships = [(_TARGET, "other-identity", True, False, False, "cloud_admin")]

    with pytest.raises(RuntimeError, match="unreviewed bootstrap membership"):
        _run(state)

    assert state.deleted_creator_role is True
    assert state.deleted_creator_sp is True


def test_creator_role_cleanup_failure_is_release_blocking() -> None:
    state = _State()
    state.fail_creator_role_delete = True

    with pytest.raises(RuntimeError, match="cleanup was incomplete"):
        _run(state)

    assert state.deleted_creator_sp is True
    assert state.bootstrap_sp_present is False
    assert state.target_relationships
    assert state.bootstrap_secrets == []
    assert state.orphan_tombstones
    assert state.wrapper_schema_exists is False
    assert state.wrapper_function_exists is False
    assert state.wrapper_schema_usage is False
    assert state.wrapper_function_execute is False


def test_normal_cleanup_fails_closed_when_bootstrap_session_survives() -> None:
    state = _State()
    state.creator_sessions = [8101]
    state.sessions_survive_termination = True

    with pytest.raises(RuntimeError, match="cleanup was incomplete"):
        _run(state)

    assert state.bootstrap_sp_present is False
    assert state.bootstrap_secrets == []
    assert state.creator_control_plane_present is True
    assert state.deleted_creator_role is False
    assert state.creator_sessions == [8101]


def test_session_cleanup_rejects_bootstrap_identity_as_executor() -> None:
    state = _State()
    state.deployer_current_user = _CREATOR

    with pytest.raises(RuntimeError, match="executor identity is unsafe"):
        bootstrap_sessions.terminate_bootstrap_sessions(
            _DeployerCursor(state),
            application_id=_CREATOR,
            expected_executor="deployer",
        )


def test_secret_cleanup_never_hides_the_workspace_principal() -> None:
    state = _State()
    state.bootstrap_sp_present = True
    state.bootstrap_secrets = ["secret-id"]
    client = _client(state)
    bootstrap_credentials.disable_and_revoke_bootstrap_credentials(
        client,
        service_principal_id=_CREATOR_SCIM_ID,
        attempts=5,
    )

    assert state.bootstrap_sp_active is True
    assert state.bootstrap_secrets == []
    client.service_principals.patch.assert_not_called()


def test_emergency_secret_cleanup_keeps_the_signed_workspace_handle_visible() -> None:
    state = _State()
    state.bootstrap_sp_present = True
    state.bootstrap_secrets = ["secret-id"]
    client = _client(state)
    bootstrap_credentials.emergency_quarantine_verified_bootstrap_credentials(
        client,
        service_principal_id=_CREATOR_SCIM_ID,
        application_id=_CREATOR,
        display_name=_signed_display_name(),
        external_id=_EXTERNAL_ID,
    )

    assert state.bootstrap_sp_active is True
    assert state.bootstrap_secrets == []
    client.service_principals.patch.assert_not_called()


def test_emergency_secret_cleanup_accepts_only_proven_two_plane_absence() -> None:
    state = _State()
    client = _client(state)

    bootstrap_credentials.emergency_quarantine_verified_bootstrap_credentials(
        client,
        account_client=client._account_client,
        service_principal_id=_CREATOR_SCIM_ID,
        application_id=_CREATOR,
        display_name=_signed_display_name(),
        external_id=_EXTERNAL_ID,
    )

    client.service_principals.patch.assert_not_called()
    client._account_client.service_principals.list.assert_not_called()


def test_secret_list_retry_exhaustion_retains_active_credentials() -> None:
    state = _State()
    state.bootstrap_sp_present = True
    state.bootstrap_secrets = ["secret-id"]
    state.fail_secret_list = True

    with pytest.raises(RuntimeError, match="credential quarantine did not converge"):
        bootstrap_credentials.disable_and_revoke_bootstrap_credentials(
            _client(state),
            service_principal_id=_CREATOR_SCIM_ID,
            attempts=3,
        )

    assert state.bootstrap_sp_active is True
    assert state.bootstrap_secrets == ["secret-id"]
    assert not any(action == "service_principal_disable" for action, _ in state.actions)


def test_deployer_current_and_session_identity_must_match_before_mutation() -> None:
    state = _State()
    state.deployer_session_user = "mapped-session-identity"

    with pytest.raises(RuntimeError, match="executor identity is unsafe"):
        _run(state)

    assert state.bootstrap_sp_present is False
    assert state.created_creator_role is False


def test_bootstrap_connection_session_identity_mismatch_blocks_provider_call() -> None:
    state = _State()
    state.bootstrap_session_user = "wrong-bootstrap-session"

    with pytest.raises(RuntimeError, match="bootstrap backend identity mismatch"):
        _run(state)

    assert not any(action == "provider_call" for action, _ in state.actions)
    assert state.creator_profile is None
    assert state.bootstrap_sp_present is False


def test_bootstrap_role_oid_reuse_blocks_provider_deletion_after_session_fence() -> None:
    state = _State()
    _seed_stale_creator(state)
    state.creator_sessions = [8110]
    state.creator_oid_after_first_read = 9910

    with pytest.raises(RuntimeError, match="cleanup was incomplete"):
        recovery.recover_stale_bootstrap_identities(
            _client(state),
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
            allow_absent_managed_event_triggers=True,
        )

    assert state.creator_profile is not None
    assert state.bootstrap_sp_present is False
    assert state.bootstrap_secrets == []
    assert state.deleted_creator_role is False


def test_bootstrap_session_rebind_blocks_role_deletion() -> None:
    state = _State()
    _seed_stale_creator(state)
    state.creator_sessions = [8111]
    state.session_binding_override = (5101, "replacement-role-name")

    with pytest.raises(RuntimeError, match="cleanup was incomplete"):
        recovery.recover_stale_bootstrap_identities(
            _client(state),
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
            allow_absent_managed_event_triggers=True,
        )

    assert state.creator_profile is not None
    assert state.bootstrap_sp_present is False
    assert state.bootstrap_secrets == []
    assert state.deleted_creator_role is False


def test_deletion_ambiguity_rechecks_and_blocks_on_reappearing_session() -> None:
    state = _State()
    state.fail_creator_role_delete = True
    state.sessions_reappear_on_delete_failure = True
    state.sessions_survive_termination = True

    with pytest.raises(RuntimeError, match="cleanup was incomplete"):
        _run(state)

    assert state.bootstrap_sp_present is False
    assert state.bootstrap_secrets == []
    assert state.creator_control_plane_present is True
    assert state.deleted_creator_role is False
    assert state.creator_sessions == [9911]


def test_tombstone_recovery_retains_marker_when_session_survives() -> None:
    state = _State()
    _seed_stale_creator(state)
    state.bootstrap_sp_present = False
    state.bootstrap_sp_active = False
    state.bootstrap_secrets = []
    state.creator_sessions = [8201]
    state.sessions_survive_termination = True
    display_name, external_id = tombstone.orphan_tombstone_contract(
        base_external_id=_EXTERNAL_ID,
        application_id=_CREATOR,
        principal_id=_CREATOR_SCIM_ID,
        signing_key=_SIGNING_KEY,
    )
    state.orphan_tombstones["session-tombstone"] = SimpleNamespace(
        id="session-tombstone",
        application_id=external_id,
        display_name=display_name,
        external_id=None,
        active=False,
        groups=[],
        roles=[],
        entitlements=[],
    )

    with pytest.raises(RuntimeError, match="session cleanup"):
        recovery.recover_stale_bootstrap_identities(
            _client(state),
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
        )

    assert "session-tombstone" in state.orphan_tombstones
    assert state.creator_control_plane_present is True
    assert state.deleted_creator_role is False


def test_tombstone_recovers_hidden_principal_secret_and_provider_owned_role() -> None:
    state = _State()
    _seed_stale_creator(state)
    state.bootstrap_sp_present = False
    state.bootstrap_secrets = ["surviving-secret"]
    state.creator_role_comment = None
    client = _client(state)
    tombstone.ensure_orphan_tombstone(
        client,
        base_external_id=_EXTERNAL_ID,
        application_id=_CREATOR,
        principal_id=_CREATOR_SCIM_ID,
        signing_key=_SIGNING_KEY,
    )

    recovery.recover_stale_bootstrap_identities(
        client,
        _DeployerCursor(state),
        instance_name=_INSTANCE,
        database_name=_DATABASE,
        target_application_id=_TARGET,
        allow_absent_managed_event_triggers=True,
    )

    assert state.bootstrap_secrets == []
    assert state.creator_sessions == []
    assert state.creator_profile is None
    assert state.creator_control_plane_present is False
    assert state.orphan_tombstones == {}
    assert not any(
        statement.startswith(("ALTER ROLE", "COMMENT ON ROLE"))
        for statement in state.deployer_statements
    )


def test_tombstone_label_direct_get_recovers_principal_omitted_from_workspace_list() -> None:
    state = _State()
    _seed_stale_creator(state)
    state.hide_principal_from_list = True
    state.creator_role_comment = None
    client = _client(state)
    tombstone.ensure_orphan_tombstone(
        client,
        base_external_id=_EXTERNAL_ID,
        application_id=_CREATOR,
        principal_id=_CREATOR_SCIM_ID,
        signing_key=_SIGNING_KEY,
    )

    recovery.recover_stale_bootstrap_identities(
        client,
        _DeployerCursor(state),
        instance_name=_INSTANCE,
        database_name=_DATABASE,
        target_application_id=_TARGET,
    )

    assert state.bootstrap_secrets == []
    assert state.deleted_creator_sp is True
    assert state.deleted_creator_role is True
    assert state.orphan_tombstones == {}


@pytest.mark.parametrize(
    "interruption_flag",
    ["tombstone_create_commit_then_error", "tombstone_delete_commit_then_error"],
)
def test_v2_upgrade_adopts_ambiguous_marker_mutation_before_role_delete(
    interruption_flag: str,
) -> None:
    state = _State()
    _seed_stale_creator(state)
    _seed_v2_tombstone(state)
    setattr(state, interruption_flag, True)
    client = _client(state)

    recovery.recover_stale_bootstrap_identities(
        client,
        _DeployerCursor(state),
        instance_name=_INSTANCE,
        database_name=_DATABASE,
        target_application_id=_TARGET,
    )

    assert state.deleted_creator_role is True
    assert state.orphan_tombstones == {}
    role_delete = next(
        index for index, action in enumerate(state.actions) if action[0] == "creator_delete"
    )
    marker_deletes = [
        index
        for index, action in enumerate(state.actions)
        if action[0] == "account_principal_delete" and index < role_delete
    ]
    assert marker_deletes


@pytest.mark.parametrize("stage", ["after_v3_create", "after_v2_delete"])
def test_v2_upgrade_interruption_preserves_discoverable_authority(
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    state = _State()
    _seed_v2_tombstone(state)
    client = _client(state)
    real_delete = tombstone.delete_orphan_tombstone

    def interrupt_delete(*args: Any, **kwargs: Any) -> None:
        if stage == "after_v2_delete":
            real_delete(*args, **kwargs)
        raise RuntimeError(f"interrupted {stage}")

    with monkeypatch.context() as interruption:
        interruption.setattr(tombstone, "delete_orphan_tombstone", interrupt_delete)
        with pytest.raises(RuntimeError, match=f"interrupted {stage}"):
            tombstone.upgrade_v2_orphan_tombstone(
                client,
                account_client=client._account_client,
                base_external_id=_EXTERNAL_ID,
                application_id=_CREATOR,
                principal_id=_CREATOR_SCIM_ID,
                signing_key=_SIGNING_KEY,
                allow_unlocked_recovery_for_tests=True,
            )

    interrupted_markers = tombstone.orphan_tombstones(
        client,
        base_external_id=_EXTERNAL_ID,
        account_client=client._account_client,
    )
    expected_count = 2 if stage == "after_v3_create" else 1
    assert len(interrupted_markers) == expected_count
    assert any(marker[4] == _CREATOR_SCIM_ID for marker in interrupted_markers)
    if stage == "after_v3_create":
        assert any(marker[4] is None for marker in interrupted_markers)

    tombstone.upgrade_v2_orphan_tombstone(
        client,
        account_client=client._account_client,
        base_external_id=_EXTERNAL_ID,
        application_id=_CREATOR,
        principal_id=_CREATOR_SCIM_ID,
        signing_key=_SIGNING_KEY,
        allow_unlocked_recovery_for_tests=True,
    )
    final_markers = tombstone.orphan_tombstones(
        client,
        base_external_id=_EXTERNAL_ID,
        account_client=client._account_client,
    )
    assert len(final_markers) == 1
    assert final_markers[0][4] == _CREATOR_SCIM_ID


def test_role_delete_interruption_retains_only_exact_v3_then_retry_converges() -> None:
    state = _State()
    _seed_stale_creator(state)
    _seed_v2_tombstone(state)
    state.fail_creator_role_delete = True
    client = _client(state)

    with pytest.raises(RuntimeError, match="cleanup was incomplete"):
        recovery.recover_stale_bootstrap_identities(
            client,
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
        )

    markers = tombstone.orphan_tombstones(
        client,
        base_external_id=_EXTERNAL_ID,
        account_client=client._account_client,
    )
    assert len(markers) == 1
    assert markers[0][1] == _CREATOR
    assert markers[0][4] == _CREATOR_SCIM_ID
    assert state.creator_control_plane_present is True

    state.fail_creator_role_delete = False
    recovery.recover_stale_bootstrap_identities(
        client,
        _DeployerCursor(state),
        instance_name=_INSTANCE,
        database_name=_DATABASE,
        target_application_id=_TARGET,
    )

    assert state.deleted_creator_role is True
    assert state.orphan_tombstones == {}


def test_commented_contract_drift_still_blocks_on_surviving_session() -> None:
    state = _State()
    _seed_stale_creator(state)
    state.bootstrap_sp_present = False
    state.creator_profile = bootstrap.LEGACY_API_OAUTH_PROFILE
    state.creator_sessions = [8301]
    state.sessions_survive_termination = True

    with pytest.raises(RuntimeError, match="session cleanup"):
        recovery.recover_stale_bootstrap_identities(
            _client(state),
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
        )

    assert state.creator_control_plane_present is True
    assert state.deleted_creator_role is False


def test_cross_deployer_recovery_blocks_when_session_survives() -> None:
    state = _State()
    _seed_stale_creator(state)
    state.wrapper_owner = "previous-deployer"
    state.creator_sessions = [8401]
    state.sessions_survive_termination = True

    with pytest.raises(RuntimeError, match="session cleanup"):
        recovery.recover_stale_bootstrap_identities(
            _client(state),
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
        )

    assert state.bootstrap_sp_present is False
    assert state.bootstrap_secrets == []
    assert state.creator_control_plane_present is True
    assert state.deleted_creator_role is False


def test_pg_database_owner_wrapper_cleanup_converges_for_replacement_deployer() -> None:
    state = _State()
    _seed_stale_creator(state)
    state.deployer_current_user = "replacement-deployer"
    state.deployer_session_user = "replacement-deployer"

    recovery.recover_stale_bootstrap_identities(
        _client(state),
        _DeployerCursor(state),
        instance_name=_INSTANCE,
        database_name=_DATABASE,
        target_application_id=_TARGET,
    )

    assert state.wrapper_owner == "pg_database_owner"
    assert state.wrapper_schema_exists is False
    assert state.wrapper_function_exists is False
    assert state.deleted_creator_role is True
    assert state.deleted_creator_sp is True
    assert 'SET LOCAL ROLE "pg_database_owner"' in state.deployer_statements


def test_profile_drift_with_unproven_role_deletion_retains_inactive_marker() -> None:
    state = _State()
    _seed_stale_creator(state)
    state.creator_profile = bootstrap.LEGACY_API_OAUTH_PROFILE
    state.fail_creator_role_delete = True

    with pytest.raises(RuntimeError, match="cleanup was incomplete"):
        recovery.recover_stale_bootstrap_identities(
            _client(state),
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
        )

    assert state.bootstrap_sp_present is False
    assert state.bootstrap_secrets == []
    assert state.orphan_tombstones


def test_cross_deployer_wrapper_is_quarantined_not_deleted_after_session_proof() -> None:
    state = _State()
    _seed_stale_creator(state)
    state.wrapper_owner = "previous-deployer"
    state.creator_sessions = [8842]

    with pytest.raises(RuntimeError, match="cleanup was incomplete"):
        recovery.recover_stale_bootstrap_identities(
            _client(state),
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
            allow_absent_managed_event_triggers=True,
        )

    assert state.bootstrap_sp_present is False
    assert state.bootstrap_secrets == []
    assert state.creator_control_plane_present is True
    assert state.deleted_creator_role is False
    assert state.creator_sessions == []


def test_missing_one_use_secret_fails_after_zero_secret_role_preparation() -> None:
    state = _State()

    with pytest.raises(RuntimeError, match="credential response is incomplete"):
        _run(state, secret="")

    assert state.created_creator_role is True
    assert state.deleted_creator_role is True
    assert state.deleted_creator_sp is True


def test_stale_creator_is_recovered_before_another_bootstrap() -> None:
    state = _State()
    _seed_stale_creator(state)

    recovery.recover_stale_bootstrap_identities(
        _client(state),
        _DeployerCursor(state),
        instance_name=_INSTANCE,
        database_name=_DATABASE,
        target_application_id=_TARGET,
    )

    assert state.bootstrap_sp_present is False
    assert state.creator_profile is None
    assert state.bootstrap_secrets == []
    assert state.target_relationships == []


def test_ambiguous_principal_create_is_recovered() -> None:
    state = _State()
    state.create_sp_commit_then_error = True

    with pytest.raises(RuntimeError, match="ambiguous service-principal create"):
        _run(state)

    assert state.bootstrap_sp_present is False
    assert state.creator_profile is None


@pytest.mark.parametrize("failure", ["list", "delete"])
def test_credential_cleanup_failure_retains_role_principal_and_tombstone(failure: str) -> None:
    state = _State()
    if failure == "list":
        state.fail_secret_list = True
    else:
        state.fail_secret_delete = True

    with pytest.raises(RuntimeError, match="cleanup was incomplete"):
        _run(state)

    assert state.creator_profile == bootstrap._BOOTSTRAP_API_PROFILE
    assert state.bootstrap_sp_present is True
    assert state.bootstrap_sp_active is True
    assert state.orphan_tombstones
    assert not any(action == "creator_delete" for action, _ in state.actions)


def test_account_principal_delete_failure_retains_provider_role_and_tombstone() -> None:
    state = _State()
    state.fail_account_principal_delete = True

    with pytest.raises(RuntimeError, match="cleanup was incomplete"):
        _run(state)

    assert state.creator_profile == bootstrap._BOOTSTRAP_API_PROFILE
    assert state.bootstrap_secrets == []
    assert state.bootstrap_sp_active is True
    assert state.bootstrap_sp_present is True
    assert state.orphan_tombstones
    assert not any(action == "creator_delete" for action, _ in state.actions)


def test_reserved_display_name_with_wrong_external_marker_is_never_deleted() -> None:
    state = _State()
    client = _client(state)
    conflicting = SimpleNamespace(
        id="unrelated-sp",
        application_id="unrelated-app",
        display_name=_DISPLAY_NAME,
        external_id="urn:someone-else",
    )
    client.service_principals.list.side_effect = lambda **_kwargs: iter([conflicting])
    client.service_principals.get.return_value = conflicting
    client.service_principals.get.side_effect = None

    with pytest.raises(RuntimeError, match="marker is ambiguous"):
        recovery.recover_stale_bootstrap_identities(
            client,
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
        )

    client.service_principals.delete.assert_not_called()


def test_signed_bootstrap_display_rejects_tampering() -> None:
    signed = _signed_display_name()

    with pytest.raises(RuntimeError, match="signature is invalid"):
        scim_marker.assert_bootstrap_principal_display_name(
            f"{signed[:-1]}{'A' if signed[-1] != 'A' else 'B'}",
            expected_name=_DISPLAY_NAME,
            ownership_marker=_EXTERNAL_ID,
        )


def test_signed_bootstrap_display_survives_proof_key_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_signing_key = base64.urlsafe_b64encode(b"o" * 32).decode().rstrip("=")
    old_verify_key = derive_gateway_proof_verify_key(old_signing_key)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", old_verify_key)
    signed = scim_marker.bootstrap_principal_display_name(
        reservation_name=_DISPLAY_NAME,
        ownership_marker=_EXTERNAL_ID,
        signing_key=old_signing_key,
    )
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", _VERIFY_KEY)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY", old_verify_key)

    scim_marker.assert_bootstrap_principal_display_name(
        signed,
        expected_name=_DISPLAY_NAME,
        ownership_marker=_EXTERNAL_ID,
    )


def test_signed_bootstrap_marker_requires_live_null_external_id() -> None:
    state = _State()
    state.bootstrap_sp_present = True
    client = _client(state)
    signed = SimpleNamespace(
        id=_CREATOR_SCIM_ID,
        application_id=_CREATOR,
        display_name=_signed_display_name(),
        external_id=_EXTERNAL_ID,
        active=True,
        groups=[],
        roles=[],
        entitlements=[],
    )
    client.service_principals.list.side_effect = lambda **_kwargs: iter([signed])
    client.service_principals.get.side_effect = lambda _principal_id: signed

    with pytest.raises(RuntimeError, match="marker is ambiguous"):
        recovery.recover_stale_bootstrap_identities(
            client,
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
        )

    client.service_principals.patch.assert_not_called()
    client.service_principals.delete.assert_not_called()


def test_duplicate_signed_bootstrap_markers_are_never_mutated() -> None:
    state = _State()
    client = _client(state)
    candidates = [
        SimpleNamespace(
            id=f"duplicate-{index}",
            application_id=f"duplicate-app-{index}",
            display_name=_signed_display_name(),
            external_id=None,
            active=True,
            groups=[],
            roles=[],
            entitlements=[],
        )
        for index in range(2)
    ]
    by_id = {item.id: item for item in candidates}
    client.service_principals.list.side_effect = lambda **_kwargs: iter(candidates)
    client.service_principals.get.side_effect = lambda principal_id: by_id[principal_id]

    with pytest.raises(RuntimeError, match="marker is duplicated"):
        recovery.recover_stale_bootstrap_identities(
            client,
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
        )

    client.service_principals.patch.assert_not_called()
    client.service_principals.delete.assert_not_called()


def test_delayed_workspace_marker_visibility_cannot_create_a_duplicate() -> None:
    state = _State()
    state.bootstrap_sp_present = True
    state.bootstrap_secrets = ["stale-secret-id"]
    client = _client(state)
    candidate = SimpleNamespace(
        id=_CREATOR_SCIM_ID,
        application_id=_CREATOR,
        display_name=_signed_display_name(),
        external_id=None,
        active=True,
        groups=[],
        roles=[],
        entitlements=[],
    )
    calls = 0

    def delayed_list(**_kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        items = list(state.orphan_tombstones.values())
        if calls > 4 and state.bootstrap_sp_present:
            items.append(candidate)
        return iter(items)

    client.service_principals.list.side_effect = delayed_list

    recovery.recover_stale_bootstrap_identities(
        client,
        _DeployerCursor(state),
        instance_name=_INSTANCE,
        database_name=_DATABASE,
        target_application_id=_TARGET,
    )

    assert calls >= 10
    assert state.deleted_creator_sp is True


def test_live_bootstrap_principal_cannot_collide_with_target_before_mutation() -> None:
    state = _State()
    _seed_stale_creator(state)
    state.bootstrap_application_id = _TARGET
    original_secrets = list(state.bootstrap_secrets)

    with pytest.raises(RuntimeError, match="target runtime identity"):
        recovery.recover_stale_bootstrap_identities(
            _client(state),
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
        )

    assert state.bootstrap_sp_active is True
    assert state.bootstrap_secrets == original_secrets
    assert state.deleted_creator_sp is False


def test_absent_instance_target_collision_is_rejected_before_credential_mutation() -> None:
    state = _State()
    _seed_stale_creator(state)
    state.bootstrap_application_id = _TARGET
    original_secrets = list(state.bootstrap_secrets)

    with pytest.raises(RuntimeError, match="target runtime identity"):
        recovery.recover_bootstrap_principals_for_absent_instance(
            _client(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
            resource_absence_probe=lambda: True,
        )

    assert state.bootstrap_sp_active is True
    assert state.bootstrap_secrets == original_secrets
    assert state.deleted_creator_sp is False


def test_existing_instance_probe_never_mutates_inflight_bootstrap_principal() -> None:
    state = _State()
    _seed_stale_creator(state)
    client = _client(state)
    client.database.list_database_instances.return_value = iter([SimpleNamespace(name=_INSTANCE)])
    original_secrets = list(state.bootstrap_secrets)

    recovered = recovery.recover_bootstrap_principals_for_absent_instance(
        client,
        instance_name=_INSTANCE,
        database_name=_DATABASE,
        target_application_id=_TARGET,
    )

    assert recovered is False
    assert state.bootstrap_sp_active is True
    assert state.bootstrap_secrets == original_secrets
    client.service_principals.patch.assert_not_called()
    client.service_principal_secrets_proxy.delete.assert_not_called()


def test_absent_instance_reappearance_during_principal_list_prevents_all_mutation() -> None:
    state = _State()
    _seed_stale_creator(state)
    client = _client(state)
    original_list = client.service_principals.list.side_effect
    instance_present = False

    def competing_list(**kwargs: Any) -> Any:
        nonlocal instance_present
        instance_present = True
        return original_list(**kwargs)

    client.service_principals.list.side_effect = competing_list
    original_secrets = list(state.bootstrap_secrets)

    recovered = recovery.recover_bootstrap_principals_for_absent_instance(
        client,
        instance_name=_INSTANCE,
        database_name=_DATABASE,
        target_application_id=_TARGET,
        resource_absence_probe=lambda: not instance_present,
    )

    assert recovered is False
    assert state.bootstrap_sp_active is True
    assert state.bootstrap_secrets == original_secrets
    client.service_principals.patch.assert_not_called()
    client.service_principal_secrets_proxy.delete.assert_not_called()
    client.service_principals.delete.assert_not_called()


def test_lock_loss_after_principal_list_prevents_all_workspace_mutation() -> None:
    state = _State()
    _seed_stale_creator(state)
    state.advisory_lock_held = True
    state.lose_lock_on_check = 1
    client = _client(state)
    lock_cursor = _DeployerCursor(state)
    original_secrets = list(state.bootstrap_secrets)

    with pytest.raises(RuntimeError, match="advisory lock was lost"):
        recovery.recover_stale_bootstrap_identities(
            client,
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
            bootstrap_lock_cursor=lock_cursor,
            bootstrap_lock_key=bootstrap_lock.BootstrapLockLease(
                key=1234,
                backend_pid=state.advisory_backend_pid,
            ),
            allow_absent_managed_event_triggers=True,
        )

    assert state.bootstrap_sp_active is True
    assert state.bootstrap_secrets == original_secrets
    client.service_principals.patch.assert_not_called()
    client.service_principal_secrets_proxy.delete.assert_not_called()
    client.service_principals.delete.assert_not_called()


def test_absent_recovery_never_mutates_replacement_principal_with_same_markers() -> None:
    old = SimpleNamespace(
        id="old-bootstrap-id",
        application_id="old-bootstrap-app",
        display_name=_signed_display_name(),
        external_id=None,
        active=True,
        groups=[],
        roles=[],
        entitlements=[],
    )
    replacement = SimpleNamespace(
        id="replacement-bootstrap-id",
        application_id="replacement-bootstrap-app",
        display_name=_signed_display_name(),
        external_id=None,
        active=True,
        groups=[],
        roles=[],
        entitlements=[],
    )
    client = MagicMock()
    exact_inventory_calls = 0

    def list_principals(**kwargs: Any) -> Any:
        nonlocal exact_inventory_calls
        filter_expr = str(kwargs.get("filter") or "")
        if "mip-lakebase-role-bootstrap-orphan" in filter_expr:
            return iter([])
        exact_inventory_calls += 1
        return iter([old] if exact_inventory_calls <= 2 else [replacement])

    get_calls = 0

    def get_principal(principal_id: str) -> Any:
        nonlocal get_calls
        get_calls += 1
        assert principal_id == "old-bootstrap-id"
        if get_calls <= 2:
            return old
        raise RuntimeError("old immutable principal was concurrently deleted")

    client.service_principals.list.side_effect = list_principals
    client.service_principals.get.side_effect = get_principal
    client.apps.list.return_value = iter([])

    with pytest.raises(RuntimeError, match="account bootstrap identity inventory changed"):
        recovery.recover_bootstrap_principals_for_absent_instance(
            client,
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
            resource_absence_probe=lambda: True,
        )

    client.service_principals.patch.assert_not_called()
    client.service_principal_secrets_proxy.delete.assert_not_called()
    client.service_principals.delete.assert_not_called()


@pytest.mark.parametrize("loss_check", range(1, 35))
def test_every_bootstrap_lock_loss_boundary_blocks_non_emergency_mutation(
    loss_check: int,
) -> None:
    state = _State()
    state.lose_lock_on_check = loss_check

    with pytest.raises(RuntimeError):
        _run(state)

    emergency_actions = {"service_principal_disable", "secret_delete"}
    assert all(check < loss_check or action in emergency_actions for action, check in state.actions)
    assert not any(action == "target_delete" for action, _ in state.actions)
    if state.bootstrap_sp_present:
        assert state.bootstrap_sp_active is True
        assert state.bootstrap_secrets == []


def test_marker_inventory_failure_quarantines_credentials_before_failing() -> None:
    state = _State()
    _seed_stale_creator(state)
    state.fail_marker_inventory = True

    with pytest.raises(RuntimeError, match="database marker inventory"):
        recovery.recover_stale_bootstrap_identities(
            _client(state),
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
        )

    assert state.bootstrap_sp_present is False
    assert state.bootstrap_secrets == []
    assert state.creator_profile is None


def test_control_plane_only_role_is_retained_without_exact_sql_contract() -> None:
    state = _State()
    state.bootstrap_sp_present = True
    state.bootstrap_secrets = ["stale-secret-id"]
    state.creator_control_plane_present = True

    with pytest.raises(RuntimeError, match="SQL role is absent"):
        recovery.recover_stale_bootstrap_identities(
            _client(state),
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
        )

    assert state.deleted_creator_role is False
    assert state.creator_control_plane_present is True
    assert state.orphan_tombstones


def test_absent_instance_residual_principal_is_retained_without_any_mutation() -> None:
    principal_id = "78879891843204"
    application_id = "33333333-3333-4333-8333-333333333333"
    principals = {
        principal_id: SimpleNamespace(
            id=principal_id,
            application_id=application_id,
            display_name=_signed_display_name(),
            external_id=None,
            active=True,
            groups=[],
            roles=[],
            entitlements=[],
        )
    }
    client = MagicMock()
    client.service_principals.list.side_effect = lambda **_kwargs: iter(principals.values())

    def get_principal(principal_id: str) -> Any:
        try:
            return principals[principal_id]
        except KeyError:
            raise NotFound("exact principal is absent") from None

    client.service_principals.get.side_effect = get_principal
    client.apps.list.return_value = iter([])
    client.service_principal_secrets_proxy.list.side_effect = lambda _id: iter([])
    client.database.get_database_instance.side_effect = NotFound("instance absent")
    deleted: list[str] = []

    def delete(principal_id: str) -> None:
        deleted.append(principal_id)
        principals.pop(principal_id, None)

    account_client = MagicMock()
    account_client.service_principals.get.side_effect = get_principal
    account_client.service_principals.delete.side_effect = delete
    account_client.service_principals.list.side_effect = lambda **_kwargs: iter(principals.values())
    account_client.service_principal_secrets.list.side_effect = lambda _id: iter([])
    account_client.workspaces.list.side_effect = lambda: iter([SimpleNamespace(workspace_id=42)])
    account_client.workspace_assignment.list.side_effect = lambda workspace_id: iter(
        [
            SimpleNamespace(
                error=None,
                principal=SimpleNamespace(principal_id=principal_id),
            )
        ]
        if workspace_id == 42 and principals
        else []
    )
    client.get_workspace_id.return_value = 42
    client._account_client = account_client

    with pytest.raises(RuntimeError, match="unlocked recovery cannot prove SQL role absence"):
        recovery.recover_bootstrap_principals_for_absent_instance(
            client,
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
        )

    assert deleted == []
    assert set(principals) == {principal_id}
    client.service_principals.patch.assert_not_called()
    client.service_principals.delete.assert_not_called()
    account_client.service_principals.delete.assert_not_called()


def test_cursorless_absent_database_cleanup_retains_role_and_principal() -> None:
    state = _State()
    state.bootstrap_sp_present = True
    state.bootstrap_secrets = ["stale-secret-id"]
    state.creator_control_plane_present = True

    with pytest.raises(RuntimeError, match="unlocked recovery cannot prove SQL role absence"):
        recovery.recover_bootstrap_principals_for_absent_instance(
            _client(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
            marker_signing_key=_SIGNING_KEY,
            resource_absence_probe=lambda: True,
        )

    assert state.deleted_creator_role is False
    assert state.deleted_creator_sp is False
    assert state.creator_control_plane_present is True
    assert state.bootstrap_sp_present is True
    assert state.bootstrap_secrets == ["stale-secret-id"]


def test_absent_database_cleanup_never_deletes_target_role_from_signed_marker() -> None:
    state = _State()
    state.target_profile = bootstrap.SAFE_OAUTH_PROFILE
    display_name, external_id = tombstone.orphan_tombstone_contract(
        base_external_id=_EXTERNAL_ID,
        application_id=_TARGET,
        principal_id=_TARGET_SCIM_ID,
        signing_key=_SIGNING_KEY,
    )
    state.orphan_tombstones["signed-target-marker"] = SimpleNamespace(
        id="signed-target-marker",
        application_id=external_id,
        display_name=display_name,
        external_id=None,
        active=False,
        groups=[],
        roles=[],
        entitlements=[],
    )
    client = _client(state)

    with pytest.raises(RuntimeError, match="target runtime identity"):
        recovery.recover_bootstrap_principals_for_absent_instance(
            client,
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
            marker_signing_key=_SIGNING_KEY,
            resource_absence_probe=lambda: True,
        )

    assert state.target_profile == bootstrap.SAFE_OAUTH_PROFILE
    client.database.delete_database_instance_role.assert_not_called()


def test_triple_failure_persists_orphan_handle_and_retry_removes_everything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", _SIGNING_KEY)
    state = _State()
    state.bootstrap_sp_present = True
    state.bootstrap_secrets = ["stale-secret-id"]
    state.creator_control_plane_present = True
    state.fail_secret_list = True
    state.fail_creator_role_delete = True
    state.fail_role_comment = True
    client = _client(state)
    cursor = _DeployerCursor(state)

    with pytest.raises(RuntimeError, match="cleanup was incomplete"):
        recovery.recover_stale_bootstrap_identities(
            client,
            cursor,
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
        )

    assert state.bootstrap_sp_present is True
    assert state.creator_control_plane_present is True
    assert len(state.orphan_tombstones) == 1
    marker = next(iter(state.orphan_tombstones.values()))
    assert marker.active is False
    assert marker.groups == marker.roles == marker.entitlements == []

    state.fail_secret_list = False
    state.fail_creator_role_delete = False
    state.fail_role_comment = False
    state.creator_profile = bootstrap._BOOTSTRAP_API_PROFILE
    recovery.recover_stale_bootstrap_identities(
        client,
        cursor,
        instance_name=_INSTANCE,
        database_name=_DATABASE,
        target_application_id=_TARGET,
    )

    assert state.creator_control_plane_present is False
    assert state.orphan_tombstones == {}


def test_signed_orphan_marker_survives_proof_key_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_signing_key = base64.urlsafe_b64encode(b"o" * 32).decode().rstrip("=")
    old_verify_key = derive_gateway_proof_verify_key(old_signing_key)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", old_verify_key)
    display_name, external_id = tombstone.orphan_tombstone_contract(
        base_external_id=_EXTERNAL_ID,
        application_id=_CREATOR,
        principal_id=_CREATOR_SCIM_ID,
        signing_key=old_signing_key,
    )
    state = _State()
    state.orphan_tombstones["old-key-marker"] = SimpleNamespace(
        id="old-key-marker",
        application_id=external_id,
        display_name=display_name,
        external_id=None,
        active=False,
        groups=[],
        roles=[],
        entitlements=[],
    )
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", _VERIFY_KEY)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY", old_verify_key)

    markers = tombstone.orphan_tombstones(
        _client(state),
        base_external_id=_EXTERNAL_ID,
    )

    assert [(marker[0], marker[1]) for marker in markers] == [("old-key-marker", _CREATOR)]


def test_absent_database_inventory_failure_mutates_nothing_before_locked_sql_retry() -> None:
    state = _State()
    state.bootstrap_sp_present = True
    state.bootstrap_secrets = ["stale-secret-id"]
    state.creator_control_plane_present = True
    state.fail_secret_list = True
    state.fail_creator_role_delete = True
    client = _client(state)

    with pytest.raises(RuntimeError):
        recovery.recover_bootstrap_principals_for_absent_instance(
            client,
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
            resource_absence_probe=lambda: True,
        )

    assert state.bootstrap_sp_present is True
    assert state.creator_control_plane_present is True
    assert state.orphan_tombstones == {}

    state.fail_secret_list = False
    state.fail_creator_role_delete = False
    state.creator_profile = bootstrap._BOOTSTRAP_API_PROFILE
    recovery.recover_stale_bootstrap_identities(
        client,
        _DeployerCursor(state),
        instance_name=_INSTANCE,
        database_name=_DATABASE,
        target_application_id=_TARGET,
    )

    assert state.creator_control_plane_present is False
    assert state.orphan_tombstones == {}


def test_scim_external_markers_remain_within_documented_limit() -> None:
    assert len(_EXTERNAL_ID) <= 64
    bootstrap_display = _signed_display_name()
    assert len(bootstrap_display) == 100
    assert len(bootstrap_display.encode("utf-8")) == 100
    tombstone_display, marker_application_id = tombstone.orphan_tombstone_contract(
        base_external_id=_EXTERNAL_ID,
        application_id=_CREATOR,
        principal_id=_CREATOR_SCIM_ID,
        signing_key=_SIGNING_KEY,
    )
    assert len(tombstone_display) == 100
    assert len(tombstone_display.encode("utf-8")) == 100
    assert str(UUID(marker_application_id)) == marker_application_id


def test_legacy_owner_only_role_function_acl_is_rejected_without_provider_repair() -> None:
    state = _State()
    state.function_acl = ("cloud_admin=X/cloud_admin",)
    cursor = _DeployerCursor(state)

    with pytest.raises(RuntimeError, match="not executable by bootstrap identities"):
        bootstrap._assert_role_function_contract(cursor)

    assert state.function_acl == ("cloud_admin=X/cloud_admin",)
    assert not any(
        statement.startswith("GRANT EXECUTE") for statement in cursor.state.deployer_statements
    )


def test_role_function_contract_rejects_unreviewed_grantee_without_repair() -> None:
    state = _State()
    state.function_acl = ("attacker=X/cloud_admin", "cloud_admin=X/cloud_admin")
    cursor = _DeployerCursor(state)

    with pytest.raises(RuntimeError, match="role-creation function contract drifted"):
        bootstrap._assert_role_function_contract(cursor)

    assert not any(
        statement.startswith("GRANT EXECUTE") for statement in cursor.state.deployer_statements
    )


def test_bootstrap_function_catalog_lookup_requires_no_public_schema_usage() -> None:
    state = _State()
    assert state.creator_public_usage is False
    cursor = _BootstrapCursor(state)

    bootstrap._assert_role_function_contract(cursor)

    statement = cursor.state.bootstrap_statements[0]
    assert "to_regprocedure" not in statement
    assert "namespace.nspname = 'public'" in statement
    assert "routine.proname = 'databricks_create_role'" in statement
    assert "routine.proargtypes = '25 25'::oidvector" in statement


def test_bootstrap_principal_rejects_unreviewed_group_membership() -> None:
    principal = SimpleNamespace(id=_CREATOR_SCIM_ID)
    exact = SimpleNamespace(
        id=_CREATOR_SCIM_ID,
        application_id=_CREATOR,
        display_name=_DISPLAY_NAME,
        external_id=_EXTERNAL_ID,
        groups=[SimpleNamespace(display="users", value="users")],
        roles=[],
        entitlements=[],
    )
    client = MagicMock()
    client.service_principals.get.return_value = exact
    client.apps.list.return_value = iter([])

    with pytest.raises(RuntimeError, match="principal contract drifted"):
        recovery._assert_bootstrap_principal_contract(
            client,
            principal,
            display_name=_DISPLAY_NAME,
            external_id=_EXTERNAL_ID,
        )


def test_forged_orphan_marker_cannot_delete_target_runtime_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", _SIGNING_KEY)
    state = _State()
    state.target_profile = bootstrap.SAFE_OAUTH_PROFILE
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", _ATTACKER_VERIFY_KEY)
    display_name, external_id = tombstone.orphan_tombstone_contract(
        base_external_id=_EXTERNAL_ID,
        application_id=_TARGET,
        principal_id=_TARGET_SCIM_ID,
        signing_key=_ATTACKER_SIGNING_KEY,
    )
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", _VERIFY_KEY)
    state.orphan_tombstones["forged-marker"] = SimpleNamespace(
        id="forged-marker",
        application_id=external_id,
        display_name=display_name,
        external_id=None,
        active=False,
        groups=[],
        roles=[],
        entitlements=[],
    )
    client = _client(state)

    with pytest.raises(RuntimeError, match="orphan marker inventory"):
        recovery.recover_stale_bootstrap_identities(
            client,
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
        )

    assert state.target_profile == bootstrap.SAFE_OAUTH_PROFILE
    client.database.delete_database_instance_role.assert_not_called()


def test_signed_orphan_marker_can_never_name_target_runtime_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", _SIGNING_KEY)
    state = _State()
    state.target_profile = bootstrap.SAFE_OAUTH_PROFILE
    display_name, external_id = tombstone.orphan_tombstone_contract(
        base_external_id=_EXTERNAL_ID,
        application_id=_TARGET,
        principal_id=_TARGET_SCIM_ID,
        signing_key=_SIGNING_KEY,
    )
    state.orphan_tombstones["signed-target-marker"] = SimpleNamespace(
        id="signed-target-marker",
        application_id=external_id,
        display_name=display_name,
        external_id=None,
        active=False,
        groups=[],
        roles=[],
        entitlements=[],
    )
    client = _client(state)

    with pytest.raises(RuntimeError, match="target runtime identity"):
        recovery.recover_stale_bootstrap_identities(
            client,
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
        )

    assert state.target_profile == bootstrap.SAFE_OAUTH_PROFILE
    client.database.delete_database_instance_role.assert_not_called()


def test_malformed_signed_marker_cannot_authorize_role_deletion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", _SIGNING_KEY)
    state = _State()
    display_name, external_id = tombstone.orphan_tombstone_contract(
        base_external_id=_EXTERNAL_ID,
        application_id=_CREATOR,
        principal_id=_CREATOR_SCIM_ID,
        signing_key=_SIGNING_KEY,
    )
    state.orphan_tombstones["malformed-marker"] = SimpleNamespace(
        id="malformed-marker",
        application_id=external_id,
        display_name=f"{display_name}:injected",
        external_id=None,
        active=False,
        groups=[],
        roles=[],
        entitlements=[],
    )
    client = _client(state)

    with pytest.raises(RuntimeError, match="orphan marker inventory"):
        recovery.recover_stale_bootstrap_identities(
            client,
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
        )

    client.database.delete_database_instance_role.assert_not_called()


def test_duplicate_signed_markers_cannot_authorize_role_deletion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", _SIGNING_KEY)
    state = _State()
    display_name, external_id = tombstone.orphan_tombstone_contract(
        base_external_id=_EXTERNAL_ID,
        application_id=_CREATOR,
        principal_id=_CREATOR_SCIM_ID,
        signing_key=_SIGNING_KEY,
    )
    for suffix in ("a", "b"):
        marker_id = f"duplicate-marker-{suffix}"
        state.orphan_tombstones[marker_id] = SimpleNamespace(
            id=marker_id,
            application_id=external_id,
            display_name=display_name,
            external_id=None,
            active=False,
            groups=[],
            roles=[],
            entitlements=[],
        )
    client = _client(state)

    with pytest.raises(RuntimeError, match="orphan marker inventory"):
        recovery.recover_stale_bootstrap_identities(
            client,
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
        )

    client.database.delete_database_instance_role.assert_not_called()


def _configure_account_only_inventory(
    client: MagicMock,
    state: _State,
    *,
    originals: dict[str, SimpleNamespace] | None = None,
    account_only_markers: dict[str, SimpleNamespace] | None = None,
    secrets: dict[str, list[str]] | None = None,
    fail_after_delete_once: set[str] | None = None,
    omit_originals_from_list: bool = False,
) -> tuple[MagicMock, list[str], list[str]]:
    originals = originals or {}
    account_only_markers = account_only_markers or {}
    secrets = secrets or {}
    fail_after_delete_once = fail_after_delete_once or set()
    filters: list[str] = []
    deleted: list[str] = []
    account_client = MagicMock()

    def all_account_principals() -> dict[str, SimpleNamespace]:
        principals = {**originals, **account_only_markers}
        principals.update(
            {
                marker_id: SimpleNamespace(
                    id=marker.id,
                    application_id=marker.application_id,
                    display_name=marker.display_name,
                    external_id=None,
                    active=True,
                    groups=[],
                    roles=[],
                    entitlements=[],
                )
                for marker_id, marker in state.orphan_tombstones.items()
            }
        )
        return principals

    def list_account_principals(**kwargs: Any) -> Any:
        filter_expr = str(kwargs.get("filter") or "")
        filters.append(filter_expr)
        visible = all_account_principals()
        if omit_originals_from_list:
            visible = {
                principal_id: principal
                for principal_id, principal in visible.items()
                if principal_id not in originals
            }
        items = list(visible.values())
        if filter_expr.startswith('displayName sw "'):
            prefix = filter_expr.removeprefix('displayName sw "').removesuffix('"')
            items = [item for item in items if item.display_name.startswith(prefix)]
        elif filter_expr.startswith('applicationId eq "'):
            application_id = filter_expr.removeprefix('applicationId eq "').removesuffix('"')
            items = [item for item in items if item.application_id == application_id]
        else:
            raise AssertionError(f"unbounded account principal inventory: {filter_expr!r}")
        return iter(items)

    def get_account_principal(principal_id: str) -> SimpleNamespace:
        try:
            return all_account_principals()[principal_id]
        except KeyError:
            raise NotFound("exact account principal is absent") from None

    def delete_account_principal(principal_id: str) -> None:
        deleted.append(principal_id)
        originals.pop(principal_id, None)
        account_only_markers.pop(principal_id, None)
        state.orphan_tombstones.pop(principal_id, None)
        if principal_id in fail_after_delete_once:
            fail_after_delete_once.remove(principal_id)
            raise RuntimeError("ambiguous account delete after commit")

    def list_account_secrets(principal_id: str) -> Any:
        return iter(SimpleNamespace(id=value) for value in secrets.get(principal_id, []))

    def delete_account_secret(principal_id: str, secret_id: str) -> None:
        secrets[principal_id].remove(secret_id)

    account_client.service_principals.list.side_effect = list_account_principals
    account_client.service_principals.get.side_effect = get_account_principal
    account_client.service_principals.delete.side_effect = delete_account_principal
    account_client.service_principal_secrets.list.side_effect = list_account_secrets
    account_client.service_principal_secrets.delete.side_effect = delete_account_secret
    account_client.workspaces.list.side_effect = lambda: iter(
        SimpleNamespace(workspace_id=workspace_id) for workspace_id in range(40, 45)
    )

    def list_assignments(workspace_id: int) -> Any:
        if workspace_id != 42:
            return iter([])
        return iter(
            SimpleNamespace(
                error=None,
                principal=SimpleNamespace(principal_id=marker_id),
            )
            for marker_id in state.orphan_tombstones
        )

    account_client.workspace_assignment.list.side_effect = list_assignments
    client.get_workspace_id.return_value = 42
    client._account_client = account_client
    return account_client, filters, deleted


def test_account_only_signed_tombstones_recover_by_exact_account_ids() -> None:
    state = _State()
    client = _client(state)
    account_only_markers: dict[str, SimpleNamespace] = {}
    original_application_ids = (
        _CREATOR,
        "33333333-3333-4333-8333-333333333333",
    )
    for index, application_id in enumerate(original_application_ids):
        display_name, marker_application_id = tombstone.orphan_tombstone_contract(
            base_external_id=_EXTERNAL_ID,
            application_id=application_id,
            principal_id=str(int(_CREATOR_SCIM_ID) + index),
            signing_key=_SIGNING_KEY,
        )
        marker_id = f"account-only-marker-{index}"
        account_only_markers[marker_id] = SimpleNamespace(
            id=marker_id,
            application_id=marker_application_id,
            display_name=display_name,
            external_id=None,
            active=True,
            groups=[],
            roles=[],
            entitlements=[],
        )
    account_client, filters, deleted = _configure_account_only_inventory(
        client,
        state,
        account_only_markers=account_only_markers,
    )

    recovery.recover_stale_bootstrap_identities(
        client,
        _DeployerCursor(state),
        account_client=account_client,
        instance_name=_INSTANCE,
        database_name=_DATABASE,
        target_application_id=_TARGET,
        allow_absent_managed_event_triggers=True,
        allow_unlocked_recovery_for_tests=True,
    )

    assert account_only_markers == {}
    assert set(deleted) == {"account-only-marker-0", "account-only-marker-1"}
    assert filters
    assert all(
        value.startswith('displayName sw "') or value.startswith('applicationId eq "')
        for value in filters
    )
    client.service_principals.delete.assert_not_called()


def test_v3_tombstone_recovers_direct_id_when_account_filter_omits_original() -> None:
    state = _State()
    client = _client(state)
    original = SimpleNamespace(
        id=_CREATOR_SCIM_ID,
        application_id=_CREATOR,
        display_name=_signed_display_name(),
        external_id=None,
        active=True,
        groups=[],
        roles=[],
        entitlements=[],
    )
    display_name, marker_application_id = tombstone.orphan_tombstone_contract(
        base_external_id=_EXTERNAL_ID,
        application_id=_CREATOR,
        principal_id=_CREATOR_SCIM_ID,
        signing_key=_SIGNING_KEY,
    )
    marker_id = "78451793422042"
    markers = {
        marker_id: SimpleNamespace(
            id=marker_id,
            application_id=marker_application_id,
            display_name=display_name,
            external_id=None,
            active=True,
            groups=[],
            roles=[],
            entitlements=[],
        )
    }
    originals = {_CREATOR_SCIM_ID: original}
    account_client, filters, deleted = _configure_account_only_inventory(
        client,
        state,
        originals=originals,
        account_only_markers=markers,
        omit_originals_from_list=True,
    )

    recovery.recover_stale_bootstrap_identities(
        client,
        _DeployerCursor(state),
        account_client=account_client,
        instance_name=_INSTANCE,
        database_name=_DATABASE,
        target_application_id=_TARGET,
        allow_absent_managed_event_triggers=True,
        allow_unlocked_recovery_for_tests=True,
    )

    assert originals == {}
    assert markers == {}
    assert set(deleted) == {_CREATOR_SCIM_ID, marker_id}
    assert filters
    assert all(value.startswith('displayName sw "') for value in filters)


def test_v2_tombstone_without_independent_id_is_retained_fail_closed() -> None:
    state = _State()
    client = _client(state)
    signature = Ed25519PrivateKey.from_private_bytes(
        tombstone._decode(_SIGNING_KEY, length=32)
    ).sign(
        tombstone._v2_message(
            base_external_id=_EXTERNAL_ID,
            application_id=_CREATOR,
        )
    )
    display_name, marker_application_id = tombstone._render_v2_contract(
        base_external_id=_EXTERNAL_ID,
        application_id=_CREATOR,
        signature=signature,
    )
    marker_id = "78451793422043"
    marker = SimpleNamespace(
        id=marker_id,
        application_id=marker_application_id,
        display_name=display_name,
        external_id=None,
        active=True,
        groups=[],
        roles=[],
        entitlements=[],
    )
    markers = {marker_id: marker}
    account_client, filters, deleted = _configure_account_only_inventory(
        client,
        state,
        account_only_markers=markers,
    )

    with pytest.raises(RuntimeError, match="legacy v2 tombstone lacks"):
        recovery.recover_stale_bootstrap_identities(
            client,
            _DeployerCursor(state),
            account_client=account_client,
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
            allow_absent_managed_event_triggers=True,
            allow_unlocked_recovery_for_tests=True,
        )

    assert markers == {marker_id: marker}
    assert deleted == []
    assert filters
    assert all(value.startswith('displayName sw "') for value in filters)


def test_account_only_bootstrap_recovery_revokes_both_secret_planes_and_retries() -> None:
    state = _State()
    state.bootstrap_secrets = ["stale-secret"]
    client = _client(state)
    original = SimpleNamespace(
        id=_CREATOR_SCIM_ID,
        application_id=_CREATOR,
        display_name=_signed_display_name(),
        external_id=None,
        active=True,
        groups=[],
        roles=[],
        entitlements=[],
    )
    originals = {_CREATOR_SCIM_ID: original}
    secrets = {_CREATOR_SCIM_ID: state.bootstrap_secrets}
    account_client, _filters, deleted = _configure_account_only_inventory(
        client,
        state,
        originals=originals,
        secrets=secrets,
        fail_after_delete_once={_CREATOR_SCIM_ID},
    )
    original_workspace_secret_list = client.service_principal_secrets_proxy.list.side_effect

    def list_workspace_secrets(principal_id: str) -> Any:
        if principal_id == _CREATOR_SCIM_ID:
            raise NotFound("account-only principal has no workspace secret proxy")
        return original_workspace_secret_list(principal_id)

    client.service_principal_secrets_proxy.list.side_effect = list_workspace_secrets

    recovery.recover_stale_bootstrap_identities(
        client,
        _DeployerCursor(state),
        account_client=account_client,
        instance_name=_INSTANCE,
        database_name=_DATABASE,
        target_application_id=_TARGET,
        allow_absent_managed_event_triggers=True,
        allow_unlocked_recovery_for_tests=True,
    )

    assert originals == {}
    assert state.bootstrap_secrets == []
    assert state.orphan_tombstones == {}
    assert deleted.count(_CREATOR_SCIM_ID) == 1
    assert any(identifier.startswith("orphan-marker-") for identifier in deleted)
    client.service_principals.delete.assert_not_called()


def test_account_only_tombstone_assignment_drift_blocks_deletion() -> None:
    state = _State()
    client = _client(state)
    display_name, marker_application_id = tombstone.orphan_tombstone_contract(
        base_external_id=_EXTERNAL_ID,
        application_id=_CREATOR,
        principal_id=_CREATOR_SCIM_ID,
        signing_key=_SIGNING_KEY,
    )
    marker = SimpleNamespace(
        id="assigned-account-marker",
        application_id=marker_application_id,
        display_name=display_name,
        external_id=None,
        active=True,
        groups=[],
        roles=[],
        entitlements=[],
    )
    markers = {marker.id: marker}
    account_client, _filters, deleted = _configure_account_only_inventory(
        client,
        state,
        account_only_markers=markers,
    )
    account_client.workspace_assignment.list.side_effect = lambda workspace_id: iter(
        [
            SimpleNamespace(
                error=None,
                principal=SimpleNamespace(principal_id=marker.id),
            )
        ]
        if workspace_id == 44
        else []
    )

    with pytest.raises(RuntimeError, match="account-only principal remains assigned"):
        tombstone.orphan_tombstones(
            client,
            base_external_id=_EXTERNAL_ID,
            account_client=account_client,
        )

    assert markers == {marker.id: marker}
    assert deleted == []


def test_account_inventory_rejects_duplicate_and_active_drift_without_mutation() -> None:
    state = _State()
    client = _client(state)
    display_name, marker_application_id = tombstone.orphan_tombstone_contract(
        base_external_id=_EXTERNAL_ID,
        application_id=_CREATOR,
        principal_id=_CREATOR_SCIM_ID,
        signing_key=_SIGNING_KEY,
    )
    markers = {
        marker_id: SimpleNamespace(
            id=marker_id,
            application_id=marker_application_id,
            display_name=display_name,
            external_id=None,
            active=True,
            groups=[],
            roles=[],
            entitlements=[],
        )
        for marker_id in ("duplicate-account-marker-a", "duplicate-account-marker-b")
    }
    account_client, _filters, deleted = _configure_account_only_inventory(
        client,
        state,
        account_only_markers=markers,
    )

    with pytest.raises(RuntimeError, match="inventory is ambiguous"):
        tombstone.orphan_tombstones(
            client,
            base_external_id=_EXTERNAL_ID,
            account_client=account_client,
        )
    assert deleted == []


@pytest.mark.parametrize("drift", ["relationship", "secret", "app"])
def test_account_only_tombstone_contract_drift_blocks_mutation(drift: str) -> None:
    state = _State()
    client = _client(state)
    display_name, marker_application_id = tombstone.orphan_tombstone_contract(
        base_external_id=_EXTERNAL_ID,
        application_id=_CREATOR,
        principal_id=_CREATOR_SCIM_ID,
        signing_key=_SIGNING_KEY,
    )
    marker = SimpleNamespace(
        id=f"{drift}-drift-marker",
        application_id=marker_application_id,
        display_name=display_name,
        external_id=None,
        active=True,
        groups=[SimpleNamespace(value="unexpected")] if drift == "relationship" else [],
        roles=[],
        entitlements=[],
    )
    markers = {marker.id: marker}
    account_client, _filters, deleted = _configure_account_only_inventory(
        client,
        state,
        account_only_markers=markers,
        secrets={marker.id: ["unexpected-secret"]} if drift == "secret" else None,
    )
    if drift == "app":
        client.apps.list.side_effect = lambda: iter(
            [SimpleNamespace(service_principal_client_id=_CREATOR)]
        )

    expected = {
        "relationship": "account marker contract drifted",
        "secret": "account tombstone has credentials",
        "app": "account marker is bound to an App",
    }[drift]
    with pytest.raises(RuntimeError, match=expected):
        tombstone.orphan_tombstones(
            client,
            base_external_id=_EXTERNAL_ID,
            account_client=account_client,
        )

    assert markers == {marker.id: marker}
    assert deleted == []


def test_account_inventory_checks_direct_workspace_active_after_list_omission() -> None:
    state = _State()
    client = _client(state)
    display_name, marker_application_id = tombstone.orphan_tombstone_contract(
        base_external_id=_EXTERNAL_ID,
        application_id=_CREATOR,
        principal_id=_CREATOR_SCIM_ID,
        signing_key=_SIGNING_KEY,
    )
    marker = SimpleNamespace(
        id="workspace-list-omitted-marker",
        application_id=marker_application_id,
        display_name=display_name,
        external_id=None,
        active=True,
        groups=[],
        roles=[],
        entitlements=[],
    )
    markers = {marker.id: marker}
    account_client, _filters, deleted = _configure_account_only_inventory(
        client,
        state,
        account_only_markers=markers,
    )
    client.service_principals.list.side_effect = lambda **_kwargs: iter([])
    client.service_principals.get.side_effect = lambda principal_id: (
        marker if principal_id == marker.id else (_ for _ in ()).throw(NotFound("absent"))
    )
    account_client.workspace_assignment.list.side_effect = lambda workspace_id: iter(
        [
            SimpleNamespace(
                error=None,
                principal=SimpleNamespace(principal_id=marker.id),
            )
        ]
        if workspace_id == 42
        else []
    )

    with pytest.raises(RuntimeError, match="workspace marker contract drifted"):
        tombstone.orphan_tombstones(
            client,
            base_external_id=_EXTERNAL_ID,
            account_client=account_client,
        )

    assert markers == {marker.id: marker}
    assert deleted == []


def test_ambiguous_account_delete_reconciles_exact_two_plane_absence() -> None:
    client = MagicMock()
    account_client = MagicMock()
    client.service_principals.get.side_effect = NotFound("workspace principal is absent")
    account_client.service_principals.get.side_effect = NotFound("account principal is absent")

    recovery_identity.prove_deleted_bootstrap_principal_absent(
        client,
        account_client,
        principal_id=_CREATOR_SCIM_ID,
        application_id=_CREATOR,
    )

    assert account_client.service_principals.get.call_count > 3
    assert (
        client.service_principals.get.call_count == account_client.service_principals.get.call_count
    )
    account_client.service_principals.list.assert_not_called()
