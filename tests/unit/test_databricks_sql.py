from __future__ import annotations

from typing import Any

import pytest

from backend.services.databricks_sql import DatabricksSqlClient


def test_statement_execution_request_uses_valid_maximum_wait_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    client = DatabricksSqlClient(
        "https://workspace.example",
        "test-token",
        "warehouse-id",
        timeout_s=50,
    )

    def fake_post(url: str, body: dict[str, Any]) -> dict[str, Any]:
        captured.update({"url": url, "body": body})
        return {
            "status": {"state": "SUCCEEDED"},
            "manifest": {"schema": {"columns": []}},
            "result": {"data_array": []},
        }

    monkeypatch.setattr(client, "_post", fake_post)

    assert client.execute("SELECT 1") == []
    assert captured["url"] == "https://workspace.example/api/2.0/sql/statements/"
    assert captured["body"] == {
        "statement": "SELECT 1",
        "warehouse_id": "warehouse-id",
        "wait_timeout": "50s",
        "on_wait_timeout": "CANCEL",
        "disposition": "INLINE",
        "format": "JSON_ARRAY",
    }

    for invalid_timeout in (-1, 1, 4, 51):
        with pytest.raises(ValueError, match="must be 0 or between 5 and 50s"):
            DatabricksSqlClient(
                "https://workspace.example",
                "test-token",
                "warehouse-id",
                timeout_s=invalid_timeout,
            )

    assert DatabricksSqlClient(
        "https://workspace.example",
        "test-token",
        "warehouse-id",
        timeout_s=0,
    )._build_request_body("SELECT 1", None)["wait_timeout"] == "0s"
