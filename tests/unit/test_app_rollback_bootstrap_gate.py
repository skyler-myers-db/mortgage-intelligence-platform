from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest
from databricks.sdk.errors import ResourceDoesNotExist

from tools.databricks import app_rollback_bootstrap_gate as gate


class _Secrets:
    def __init__(self, value: str | None) -> None:
        self.value = value

    def get_secret(self, _scope: str, _key: str) -> object:
        if self.value is None:
            raise ResourceDoesNotExist("missing")
        encoded = base64.b64encode(self.value.encode()).decode()
        return SimpleNamespace(value=encoded)


class _LegacyOnlySecrets:
    def get_secret(self, _scope: str, key: str) -> object:
        if "app-last-good-v5-" not in key:
            raise ResourceDoesNotExist("missing")
        encoded = base64.b64encode(b'{"signed":true}').decode()
        return SimpleNamespace(value=encoded)


def test_unsigned_rebase_gate_accepts_authoritative_record_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = SimpleNamespace(secrets=_Secrets(None))
    monkeypatch.setattr(gate, "assert_owned_app_rollback_scope", lambda *_a, **_kw: None)

    gate.assert_rollback_record_absent(
        workspace,
        app_name="mip-app",
        scope="rollback",
    )


@pytest.mark.parametrize("stored", ['{"signed":true}', "not-json"])
def test_unsigned_rebase_gate_refuses_any_existing_record(
    stored: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = SimpleNamespace(secrets=_Secrets(stored))
    monkeypatch.setattr(gate, "assert_owned_app_rollback_scope", lambda *_a, **_kw: None)

    with pytest.raises(RuntimeError, match="already exists"):
        gate.assert_rollback_record_absent(
            workspace,
            app_name="mip-app",
            scope="rollback",
        )


def test_unsigned_rebase_gate_refuses_legacy_v5_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = SimpleNamespace(secrets=_LegacyOnlySecrets())
    monkeypatch.setattr(gate, "assert_owned_app_rollback_scope", lambda *_a, **_kw: None)

    with pytest.raises(RuntimeError, match="already exists"):
        gate.assert_rollback_record_absent(
            workspace,
            app_name="mip-app",
            scope="rollback",
        )
