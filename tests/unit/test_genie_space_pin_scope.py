"""The Genie pin lives beside the rollback record under the real scope-ownership proof (M1-PR3).

tests/unit/test_genie_space_pin.py stands in for the proof
(``patch_rollback_scope``); here nothing does. The fake secrets API below is
the one the proof reads (scopes, ACLs, keys, the ownership marker), so a pin
key the proof does not admit fails every later read of the scope: the next
pin read, the rollback record's capture and load, and the unsigned-rebase
gate. Only the rollback record's payload-shape validator is replaced, because
a full signed-blue payload is not what this test is about.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.errors import NotFound
from databricks.sdk.service.workspace import AclPermission

from tests.fixtures.genie_pin_workspace import signing_env
from tools.databricks import app_rollback_record_contract as contract
from tools.databricks import genie_space_pin as pin
from tools.databricks.app_rollback_bootstrap_gate import assert_rollback_record_absent
from tools.databricks.app_rollback_secret_scope import (
    MARKER_KEY,
    assert_owned_app_rollback_scope,
    ensure_owned_app_rollback_scope,
)

APP = "mip-app"
SCOPE = "mip-app-rollback"
INSTANCE = "mip-app-state"
TITLE = "Mortgage Lead Intelligence"
SPACE = "space-governed"
DEPLOYER = "deployer@example.invalid"


class _Secrets:
    """One deployer's secret scopes, with the ACL and key inventory the proof reads."""

    def __init__(self) -> None:
        self.acls: dict[str, dict[str, str]] = {}
        self.keys: dict[str, dict[str, str]] = {}

    def list_scopes(self) -> list[Any]:
        return [SimpleNamespace(name=name) for name in sorted(self.keys)]

    def create_scope(self, *, scope: str) -> None:
        self.acls[scope] = {DEPLOYER: "MANAGE"}
        self.keys[scope] = {}

    def list_acls(self, *, scope: str) -> list[Any]:
        return [
            SimpleNamespace(principal=principal, permission=permission)
            for principal, permission in sorted(self.acls[scope].items())
        ]

    def put_acl(self, *, scope: str, principal: str, permission: AclPermission) -> None:
        self.acls[scope][principal] = permission.value

    def put_secret(self, *, scope: str, key: str, string_value: str) -> None:
        self.keys[scope][key] = string_value

    def get_secret(self, scope: str, key: str) -> Any:
        if key not in self.keys.get(scope, {}):
            raise NotFound("no such secret")
        return SimpleNamespace(value=base64.b64encode(self.keys[scope][key].encode()).decode())

    def list_secrets(self, *, scope: str) -> list[Any]:
        return [SimpleNamespace(key=key) for key in sorted(self.keys[scope])]


def _workspace(secrets: _Secrets) -> Any:
    return SimpleNamespace(
        secrets=secrets,
        current_user=SimpleNamespace(me=lambda: SimpleNamespace(user_name=DEPLOYER, application_id="")),
        genie=SimpleNamespace(get_space=lambda space_id: SimpleNamespace(space_id=space_id)),
    )


@pytest.fixture
def owned(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, _Secrets]:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    for name, value in signing_env().items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY", raising=False)
    secrets = _Secrets()
    workspace = _workspace(secrets)
    # The real first-run claim: the scope gets its marker and the exact ACL.
    ensure_owned_app_rollback_scope(workspace, app_name=APP, scope=SCOPE)
    return workspace, secrets


def test_a_written_pin_keeps_the_scope_provably_owned_for_every_later_reader(
    owned: tuple[Any, _Secrets], monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, secrets = owned

    written = pin.write_pin(
        workspace, scope=SCOPE, app_name=APP, title=TITLE, space_id=SPACE, provenance="created"
    )

    assert set(secrets.keys[SCOPE]) == {MARKER_KEY, pin.pin_key(APP)}
    # The next preflight and bundle_env read the pin through the real proof.
    assert_owned_app_rollback_scope(workspace, app_name=APP, scope=SCOPE)
    assert pin.read_pin(workspace, scope=SCOPE, app_name=APP) == written
    # The unsigned-rebase gate still proves the scope, then finds no record.
    assert_rollback_record_absent(workspace, app_name=APP, scope=SCOPE)
    # A capture writes the rollback record beside the pin (proof before and after the write).
    contract._write_record(
        workspace,
        scope=SCOPE,
        record={"version": contract.RECORD_VERSION, "app_name": APP, "genie_space_id": SPACE},
        key=contract._record_key(APP),
    )
    # A rollback loads it; only its payload-shape validation is not under test here.
    monkeypatch.setattr(contract, "_validated_record", lambda value, **_kwargs: value)
    loaded = contract._load_record(workspace, app_name=APP, scope=SCOPE, expected_lakebase_instance=INSTANCE)
    assert loaded["genie_space_id"] == SPACE
    # Pin and record agree, so the governed space resolves by both.
    assert pin.pinned_space_id(workspace, app_name=APP, scope=SCOPE, lakebase_instance=INSTANCE) == SPACE
    assert set(secrets.keys[SCOPE]) == {MARKER_KEY, pin.pin_key(APP), contract._record_key(APP)}


@pytest.mark.parametrize("key", ("genie-space-pin-v1-other-app", "genie-space-pin-v2-mip-app", "unreviewed-key"))
def test_the_reviewed_key_set_admits_only_this_apps_pin(owned: tuple[Any, _Secrets], key: str) -> None:
    workspace, secrets = owned
    secrets.keys[SCOPE][key] = "{}"

    with pytest.raises(RuntimeError, match="contains an unreviewed key"):
        assert_owned_app_rollback_scope(workspace, app_name=APP, scope=SCOPE)


def test_the_pin_key_is_the_one_the_scope_proof_admits() -> None:
    assert pin.pin_key(APP) == f"{pin.PIN_KEY_PREFIX}-{APP}"
    for invalid in ("", "Mip-App", "mip_app", "-mip-app"):
        with pytest.raises(ValueError, match="Genie pin App name is invalid"):
            pin.pin_key(invalid)
