from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest
from databricks.sdk.errors import ResourceDoesNotExist

from tools.databricks.app_rollback_bootstrap_gate import assert_rollback_record_absent


class _Secrets:
    def __init__(self, value: str | None) -> None:
        self.value = value

    def get_secret(self, _scope: str, _key: str) -> object:
        if self.value is None:
            raise ResourceDoesNotExist("missing")
        encoded = base64.b64encode(self.value.encode()).decode()
        return SimpleNamespace(value=encoded)


def test_unsigned_rebase_gate_accepts_authoritative_record_absence() -> None:
    workspace = SimpleNamespace(secrets=_Secrets(None))

    assert_rollback_record_absent(workspace, app_name="mip-app", scope="rollback")


@pytest.mark.parametrize("stored", ['{"signed":true}', "not-json"])
def test_unsigned_rebase_gate_refuses_any_existing_record(stored: str) -> None:
    workspace = SimpleNamespace(secrets=_Secrets(stored))

    with pytest.raises(RuntimeError, match="already exists"):
        assert_rollback_record_absent(workspace, app_name="mip-app", scope="rollback")
