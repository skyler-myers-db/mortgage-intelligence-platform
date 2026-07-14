from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.databricks import provision_runtime_secrets as subject


def _test_secret(label: str) -> str:
    return f"{label}-" + ("x" * 32)


MASK_SECRET = _test_secret("mask")
CURRENT_SECRET = _test_secret("current")
PREVIOUS_SECRET = _test_secret("previous")


class _Secrets:
    def __init__(self, *, scope_exists: bool = False, keys: tuple[str, ...] = ()) -> None:
        self.scope_exists = scope_exists
        self.keys = set(keys)
        self.puts: list[tuple[str, str, str]] = []

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


def test_provisions_required_values_and_generated_previous_without_printing(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", MASK_SECRET)
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_CURRENT", CURRENT_SECRET)
    monkeypatch.delenv("MIP_GENIE_ACTION_SECRET_PREVIOUS", raising=False)
    monkeypatch.setattr(subject, "ENV_LOCAL", subject.REPO / ".missing-test-env")
    monkeypatch.setattr(subject.secrets, "token_urlsafe", lambda _size: PREVIOUS_SECRET)
    secrets_api = _Secrets()

    written = subject.provision_runtime_secrets(
        client=SimpleNamespace(secrets=secrets_api)
    )

    assert secrets_api.scope_exists is True
    assert written == (
        "cotality-id-mask-v1",
        "genie-action-current",
        "genie-action-previous",
    )
    assert secrets_api.puts == [
        (subject.DEFAULT_SCOPE, "cotality-id-mask-v1", MASK_SECRET),
        (subject.DEFAULT_SCOPE, "genie-action-current", CURRENT_SECRET),
        (subject.DEFAULT_SCOPE, "genie-action-previous", PREVIOUS_SECRET),
    ]


def test_preserves_existing_previous_key_when_operator_does_not_rotate(monkeypatch) -> None:
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", MASK_SECRET)
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_CURRENT", CURRENT_SECRET)
    monkeypatch.delenv("MIP_GENIE_ACTION_SECRET_PREVIOUS", raising=False)
    monkeypatch.setattr(subject, "ENV_LOCAL", subject.REPO / ".missing-test-env")
    secrets_api = _Secrets(
        scope_exists=True,
        keys=("genie-action-previous",),
    )

    subject.provision_runtime_secrets(client=SimpleNamespace(secrets=secrets_api))

    assert [key for _scope, key, _value in secrets_api.puts] == [
        "cotality-id-mask-v1",
        "genie-action-current",
    ]


def test_rejects_missing_or_placeholder_required_values(monkeypatch) -> None:
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", "REDACTED")
    monkeypatch.delenv("MIP_GENIE_ACTION_SECRET_CURRENT", raising=False)
    monkeypatch.setattr(subject, "ENV_LOCAL", subject.REPO / ".missing-test-env")

    with pytest.raises(ValueError, match="MIP_COTALITY_ID_MASK_SECRET"):
        subject.provision_runtime_secrets(
            client=SimpleNamespace(secrets=_Secrets())
        )


def test_rejects_present_but_weak_runtime_secret(monkeypatch) -> None:
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", "x")
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_CURRENT", CURRENT_SECRET)
    monkeypatch.setattr(subject, "ENV_LOCAL", subject.REPO / ".missing-test-env")

    with pytest.raises(ValueError, match="at least 32"):
        subject.provision_runtime_secrets(client=SimpleNamespace(secrets=_Secrets()))


def test_rejects_duplicate_rotation_values(monkeypatch) -> None:
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", MASK_SECRET)
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_CURRENT", CURRENT_SECRET)
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_PREVIOUS", CURRENT_SECRET)
    monkeypatch.setattr(subject, "ENV_LOCAL", subject.REPO / ".missing-test-env")

    with pytest.raises(ValueError, match="must differ"):
        subject.provision_runtime_secrets(client=SimpleNamespace(secrets=_Secrets()))
