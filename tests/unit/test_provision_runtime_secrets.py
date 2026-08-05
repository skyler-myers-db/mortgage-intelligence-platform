from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest

from tools.databricks import provision_runtime_secrets as subject


def test_direct_execution_resolves_repository_imports() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(subject.REPO / "tools/databricks/provision_runtime_secrets.py"),
            "--help",
        ],
        cwd="/tmp",
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Provision Databricks Secret keys" in completed.stdout


def _test_secret(label: str) -> str:
    return f"{label}-" + ("x" * 32)


MASK_SECRET = _test_secret("mask")
CURRENT_SECRET = _test_secret("current")
PREVIOUS_SECRET = _test_secret("previous")
DEFAULT_WRITTEN_KEYS = tuple(subject.ENV_TO_KEY.values())


@pytest.fixture(autouse=True)
def _hermetic_secret_config(monkeypatch) -> None:
    monkeypatch.setattr(subject, "ENV_LOCAL", subject.REPO / ".missing-test-env")
    for name in (*subject.ENV_TO_KEY, subject.PREVIOUS_KID_ENV):
        monkeypatch.delenv(name, raising=False)


class _Secrets:
    def __init__(self, *, scope_exists: bool = False, keys: tuple[str, ...] = ()) -> None:
        self.scope_exists = scope_exists
        self.keys = set(keys)
        self.puts: list[tuple[str, str, str]] = []
        self.deletes: list[tuple[str, str]] = []

    def list_scopes(self):
        return [SimpleNamespace(name=subject.DEFAULT_SCOPE)] if self.scope_exists else []

    def create_scope(self, *, scope: str) -> None:
        assert scope == subject.DEFAULT_SCOPE
        self.scope_exists = True

    def list_secrets(self, *, scope: str):
        assert scope == subject.DEFAULT_SCOPE
        return [SimpleNamespace(key=key) for key in sorted(self.keys)]

    def put_secret(self, *, scope: str, key: str, string_value: str) -> None:
        self.puts.append((scope, key, string_value))
        self.keys.add(key)

    def delete_secret(self, *, scope: str, key: str) -> None:
        self.deletes.append((scope, key))
        self.keys.discard(key)


def test_provisions_required_values_without_previous_by_default(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", MASK_SECRET)
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_CURRENT", CURRENT_SECRET)
    secrets_api = _Secrets()

    written = subject.provision_runtime_secrets(client=SimpleNamespace(secrets=secrets_api))

    assert secrets_api.scope_exists is True
    assert written == DEFAULT_WRITTEN_KEYS
    assert [key for _scope, key, _value in secrets_api.puts] == list(DEFAULT_WRITTEN_KEYS)
    disabled_value = next(
        value for _scope, key, value in secrets_api.puts if key == "genie-action-previous"
    )
    assert disabled_value.startswith(subject.DISABLED_PREVIOUS_PREFIX)
    assert disabled_value not in {MASK_SECRET, CURRENT_SECRET}
    assert secrets_api.deletes == []


def test_provisions_previous_only_for_explicit_rotation_grace(monkeypatch) -> None:
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", MASK_SECRET)
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_CURRENT", CURRENT_SECRET)
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_PREVIOUS", PREVIOUS_SECRET)
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_PREVIOUS_KID", "v1")
    secrets_api = _Secrets()

    written = subject.provision_runtime_secrets(client=SimpleNamespace(secrets=secrets_api))

    assert written == DEFAULT_WRITTEN_KEYS
    assert (
        subject.DEFAULT_SCOPE,
        "genie-action-previous",
        PREVIOUS_SECRET,
    ) in secrets_api.puts


def test_retires_existing_previous_key_when_grace_is_not_configured(monkeypatch) -> None:
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", MASK_SECRET)
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_CURRENT", CURRENT_SECRET)
    secrets_api = _Secrets(
        scope_exists=True,
        keys=("genie-action-previous",),
    )

    written = subject.provision_runtime_secrets(client=SimpleNamespace(secrets=secrets_api))

    assert written == DEFAULT_WRITTEN_KEYS
    assert [key for _scope, key, _value in secrets_api.puts] == list(DEFAULT_WRITTEN_KEYS)
    previous_value = next(
        value for _scope, key, value in secrets_api.puts if key == "genie-action-previous"
    )
    assert previous_value.startswith(subject.DISABLED_PREVIOUS_PREFIX)
    assert secrets_api.deletes == []


def test_explicit_retire_previous_does_not_require_other_secret_values() -> None:
    secrets_api = _Secrets(
        scope_exists=True,
        keys=("genie-action-previous",),
    )

    existed = subject.retire_previous_secret(client=SimpleNamespace(secrets=secrets_api))

    assert existed is True
    assert secrets_api.puts[-1][1] == "genie-action-previous"
    assert secrets_api.puts[-1][2].startswith(subject.DISABLED_PREVIOUS_PREFIX)
    assert secrets_api.deletes == []


def test_explicit_retire_creates_disabled_binding_when_scope_is_absent() -> None:
    secrets_api = _Secrets()

    existed = subject.retire_previous_secret(client=SimpleNamespace(secrets=secrets_api))

    assert existed is False
    assert secrets_api.scope_exists is True
    assert secrets_api.puts[-1][1] == "genie-action-previous"
    assert secrets_api.puts[-1][2].startswith(subject.DISABLED_PREVIOUS_PREFIX)


def test_rejects_missing_or_placeholder_required_values(monkeypatch) -> None:
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", "REDACTED")

    with pytest.raises(ValueError, match="MIP_COTALITY_ID_MASK_SECRET"):
        subject.provision_runtime_secrets(client=SimpleNamespace(secrets=_Secrets()))


def test_rejects_present_but_weak_runtime_secret(monkeypatch) -> None:
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", "x")
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_CURRENT", CURRENT_SECRET)

    with pytest.raises(ValueError, match="at least 32"):
        subject.provision_runtime_secrets(client=SimpleNamespace(secrets=_Secrets()))


def test_rejects_duplicate_rotation_values(monkeypatch) -> None:
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", MASK_SECRET)
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_CURRENT", CURRENT_SECRET)
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_PREVIOUS", CURRENT_SECRET)
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_PREVIOUS_KID", "v1")

    with pytest.raises(ValueError, match="must differ"):
        subject.provision_runtime_secrets(client=SimpleNamespace(secrets=_Secrets()))


def test_rejects_previous_secret_without_explicit_rotation_kid(monkeypatch) -> None:
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", MASK_SECRET)
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_CURRENT", CURRENT_SECRET)
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_PREVIOUS", PREVIOUS_SECRET)

    with pytest.raises(ValueError, match="MIP_GENIE_ACTION_SECRET_PREVIOUS_KID"):
        subject.provision_runtime_secrets(client=SimpleNamespace(secrets=_Secrets()))


def test_provisions_optional_salesforce_credentials_without_logging_values(monkeypatch) -> None:
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", MASK_SECRET)
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_CURRENT", CURRENT_SECRET)
    monkeypatch.setenv("SALESFORCE_CLIENT_SECRET", "sf-client-secret")
    monkeypatch.setenv("SALESFORCE_PASSWORD", "sf-password")
    monkeypatch.setenv("SALESFORCE_SECURITY_TOKEN", "sf-token")
    secrets_api = _Secrets()

    subject.provision_runtime_secrets(client=SimpleNamespace(secrets=secrets_api))

    assert (subject.DEFAULT_SCOPE, "salesforce-client-secret", "sf-client-secret") in secrets_api.puts
    assert (subject.DEFAULT_SCOPE, "salesforce-password", "sf-password") in secrets_api.puts
    assert (subject.DEFAULT_SCOPE, "salesforce-security-token", "sf-token") in secrets_api.puts
