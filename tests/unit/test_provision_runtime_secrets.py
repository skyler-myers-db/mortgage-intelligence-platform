from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.databricks import provision_runtime_secrets as subject


def _test_secret(label: str) -> str:
    return f"{label}-" + ("x" * 32)


MASK_SECRET = _test_secret("mask")
CURRENT_SECRET = _test_secret("current")
PREVIOUS_SECRET = _test_secret("previous")


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
    assert written == (
        "cotality-id-mask-v1",
        "genie-action-current",
    )
    assert secrets_api.puts == [
        (subject.DEFAULT_SCOPE, "cotality-id-mask-v1", MASK_SECRET),
        (subject.DEFAULT_SCOPE, "genie-action-current", CURRENT_SECRET),
    ]
    assert secrets_api.deletes == []


def test_provisions_previous_only_for_explicit_rotation_grace(monkeypatch) -> None:
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", MASK_SECRET)
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_CURRENT", CURRENT_SECRET)
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_PREVIOUS", PREVIOUS_SECRET)
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_PREVIOUS_KID", "v1")
    secrets_api = _Secrets()

    written = subject.provision_runtime_secrets(client=SimpleNamespace(secrets=secrets_api))

    assert written == (
        "cotality-id-mask-v1",
        "genie-action-current",
        "genie-action-previous",
    )
    assert secrets_api.puts[-1] == (
        subject.DEFAULT_SCOPE,
        "genie-action-previous",
        PREVIOUS_SECRET,
    )


def test_retires_existing_previous_key_when_grace_is_not_configured(monkeypatch) -> None:
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", MASK_SECRET)
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_CURRENT", CURRENT_SECRET)
    secrets_api = _Secrets(
        scope_exists=True,
        keys=("genie-action-previous",),
    )

    written = subject.provision_runtime_secrets(client=SimpleNamespace(secrets=secrets_api))

    assert written == ("cotality-id-mask-v1", "genie-action-current")
    assert [key for _scope, key, _value in secrets_api.puts] == [
        "cotality-id-mask-v1",
        "genie-action-current",
    ]
    assert secrets_api.deletes == [(subject.DEFAULT_SCOPE, "genie-action-previous")]


def test_explicit_retire_previous_does_not_require_other_secret_values() -> None:
    secrets_api = _Secrets(
        scope_exists=True,
        keys=("genie-action-previous",),
    )

    deleted = subject.retire_previous_secret(client=SimpleNamespace(secrets=secrets_api))

    assert deleted is True
    assert secrets_api.deletes == [(subject.DEFAULT_SCOPE, "genie-action-previous")]


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
