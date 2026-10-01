from __future__ import annotations

from typing import Any

import pytest

from backend.services.databricks_sql import (
    DatabricksSqlClient,
    DatabricksSqlError,
    DatabricksSqlObjectMissingError,
)


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


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "[SCHEMA_NOT_FOUND] The schema `mip`.`gold` cannot be found. Verify the spelling "
            "and correctness of the schema and catalog. SQLSTATE: 42704",
            DatabricksSqlObjectMissingError,
        ),
        (
            "[UNRESOLVED_ROUTINE] Cannot resolve routine `mip`.`gold`.`fn_lead_score` on search "
            "path [`system`.`builtin`, `system`.`session`]. SQLSTATE: 42883",
            DatabricksSqlObjectMissingError,
        ),
        (
            "[TABLE_OR_VIEW_NOT_FOUND] The table or view `mip`.`gold`.`x` cannot be found. "
            "SQLSTATE: 42P01",
            DatabricksSqlObjectMissingError,
        ),
        # The shared SQLSTATEs alone are NOT a missing object.
        ("[DATATYPE_MISMATCH] Cannot resolve the expression. SQLSTATE: 42704", DatabricksSqlError),
        ("[WRONG_NUM_ARGS] The function requires 2 parameters. SQLSTATE: 42883", DatabricksSqlError),
    ],
    ids=["schema", "routine", "table", "42704-other-class", "42883-other-class"],
)
def test_a_failed_statement_classifies_documented_missing_object_formats(
    monkeypatch: pytest.MonkeyPatch, message: str, expected: type[DatabricksSqlError]
) -> None:
    client = DatabricksSqlClient("https://workspace.example", "test-token", "warehouse-id", timeout_s=50)

    def fake_post(url: str, body: dict[str, Any]) -> dict[str, Any]:
        return {
            "statement_id": "stmt-1",
            "status": {"state": "FAILED", "error": {"error_code": "BAD_REQUEST", "message": message}},
        }

    monkeypatch.setattr(client, "_post", fake_post)
    with pytest.raises(DatabricksSqlError) as raised:
        client.execute("SELECT 1")
    assert type(raised.value) is expected
