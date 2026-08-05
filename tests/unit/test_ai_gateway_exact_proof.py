from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from backend.config.settings import AI_GATEWAY_PROOF_FRESHNESS_MAX_S
from backend.services.ai_gateway_proof_attestation import (
    derive_gateway_proof_verify_key,
    sign_gateway_proof,
)
from backend.services.ai_gateway_proof_ledger import (
    AI_GATEWAY_PROOF_CLOCK_SKEW_S,
    insert_pending_proof,
    latest_verified_proof,
    mark_expired_pending_proofs,
    mark_proof_verified,
)
from tools.databricks import verify_ai_gateway_exact_proof

_TEST_GIT_SHA = "75ea6680b7f04bbaa6d0bbf38d7676218ae6c1cc"
_OTHER_GIT_SHA = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
_ENDPOINT = "mip-supervisor-endpoint"
_INFERENCE_TABLE = "mip.audit.mip_agent_gateway_llama"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEST_SIGNING_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")
_TEST_VERIFY_KEY = derive_gateway_proof_verify_key(_TEST_SIGNING_KEY)
_OTHER_SIGNING_KEY = base64.urlsafe_b64encode(bytes(reversed(range(32)))).decode().rstrip("=")
_OTHER_VERIFY_KEY = derive_gateway_proof_verify_key(_OTHER_SIGNING_KEY)


@pytest.fixture(autouse=True)
def _hermetic_lakebase_env(monkeypatch):
    """Keep main()-driving tests fully offline (CI has no Databricks auth).

    A real-shaped LAKEBASE_HOST makes ensure_lakebase_env early-return, so
    no WorkspaceClient is ever constructed and no credential resolution can
    leak into these tests (CI run 28945210525 failure, 2026-07-08). Fakes
    supplied per-test still exercise the mint path explicitly elsewhere.
    """
    monkeypatch.setenv("LAKEBASE_HOST", "unit-test-lakebase.database.example")
    monkeypatch.setenv("LAKEBASE_USER", "unit-test@example.com")
    monkeypatch.setenv("LAKEBASE_PASSWORD", "unit-test-token")
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", _TEST_SIGNING_KEY)


class _ProofLakebase:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = list(rows or [])
        self.fetchone_calls: list[tuple[str, dict[str, Any]]] = []

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        params = params or {}
        self.fetchone_calls.append((sql, params))
        if "INSERT INTO mip_app.ai_gateway_proof_ledger" in sql:
            row = {
                "proof_id": str(params["proof_id"]),
                "git_sha": params["git_sha"],
                "client_request_id": params["client_request_id"],
                "endpoint_name": params["endpoint_name"],
                "inference_table": params["inference_table"],
                "sent_at": params["sent_at"],
                "verified_at": None,
                "verify_latency_s": None,
                "status": "pending",
                "attestation_alg": None,
                "attestation_key_id": None,
                "attestation_signature": None,
            }
            self.rows.append(row)
            return dict(row)
        if "UPDATE mip_app.ai_gateway_proof_ledger" in sql and "SET status = 'failed'" in sql:
            proof_id = str(params["proof_id"])
            for row in self.rows:
                if str(row["proof_id"]) == proof_id and row["status"] == "pending":
                    row["status"] = "failed"
                    row["verified_at"] = None
                    row["verify_latency_s"] = None
                    row["attestation_alg"] = None
                    row["attestation_key_id"] = None
                    row["attestation_signature"] = None
                    return dict(row)
            return None
        if "UPDATE mip_app.ai_gateway_proof_ledger" in sql and "SET status = 'verified'" in sql:
            proof_id = str(params["proof_id"])
            for row in self.rows:
                if str(row["proof_id"]) == proof_id and row["status"] == "pending":
                    row["status"] = "verified"
                    row["verified_at"] = params["verified_at"]
                    row["verify_latency_s"] = params["verify_latency_s"]
                    row["attestation_alg"] = params["attestation_alg"]
                    row["attestation_key_id"] = params["attestation_key_id"]
                    row["attestation_signature"] = params["attestation_signature"]
                    return dict(row)
            return None
        if "WITH updated AS" in sql:
            older_than = params["older_than"]
            git_sha = params.get("git_sha")
            count = 0
            for row in self.rows:
                if row["status"] != "pending" or row["sent_at"] >= older_than:
                    continue
                if git_sha and row["git_sha"] != git_sha:
                    continue
                row["status"] = "expired"
                count += 1
            return {"row_count": count}
        if "FROM mip_app.ai_gateway_proof_ledger" in sql and "status = 'pending'" in sql:
            proof_id = str(params.get("proof_id") or "")
            for row in self.rows:
                if str(row["proof_id"]) == proof_id and row["status"] == "pending":
                    return dict(row)
            return None
        if "FROM mip_app.ai_gateway_proof_ledger" in sql and "status = 'verified'" in sql:
            git_sha = params["git_sha"]
            endpoint_name = params["endpoint_name"]
            inference_table = params["inference_table"]
            cutoff = params["cutoff"]
            future_cutoff = params["future_cutoff"]
            clock_skew = timedelta(seconds=int(params["clock_skew_s"]))
            matches = [
                dict(row)
                for row in self.rows
                if row["git_sha"] == git_sha
                and row["endpoint_name"] == endpoint_name
                and row["inference_table"] == inference_table
                and row["status"] == "verified"
                and row.get("attestation_alg") == params["attestation_alg"]
                and row.get("attestation_key_id") == params["attestation_key_id"]
                and row["verified_at"] is not None
                and row["verified_at"] >= cutoff
                and row["verified_at"] <= future_cutoff
                and row["sent_at"] <= future_cutoff
                and row["verified_at"] >= row["sent_at"] - clock_skew
            ]
            matches.sort(key=lambda row: row["verified_at"], reverse=True)
            return matches[0] if matches else None
        raise AssertionError(f"unexpected SQL: {sql}")

    def fetchall(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        params = params or {}
        if "FROM mip_app.ai_gateway_proof_ledger" not in sql or "status = 'pending'" not in sql:
            raise AssertionError(f"unexpected SQL: {sql}")
        git_sha = params.get("git_sha")
        rows = [
            dict(row)
            for row in self.rows
            if row["status"] == "pending" and (not git_sha or row["git_sha"] == git_sha)
        ]
        rows.sort(key=lambda row: row["sent_at"])
        return rows[: limit or params.get("limit") or len(rows)]


class _ProofSql:
    table_names = ["mip_agent_gateway_llama_payload"]
    valid_row = {
        "status_code": 200,
        "response": json.dumps(
            {
                "status": "completed",
                "output": [{"content": [{"text": "Gateway logging acknowledged."}]}],
            }
        ),
    }

    def __init__(
        self,
        *,
        exact_count: int = 0,
        counts_by_request_id: dict[str, int] | None = None,
        rows_by_request_id: dict[str, list[dict[str, Any]]] | None = None,
        column_names: list[str] | None = None,
    ) -> None:
        self.exact_count = exact_count
        self.counts_by_request_id = counts_by_request_id
        self.rows_by_request_id = rows_by_request_id
        self.column_names = column_names or [
            "client_request_id",
            "request",
            "databricks_request_id",
            "status_code",
            "response",
            "timestamp_ms",
        ]
        self.statements: list[str] = []
        self.parameters: list[object | None] = []

    def execute(self, statement: str, parameters: object | None = None) -> list[dict[str, Any]]:
        self.statements.append(statement)
        self.parameters.append(parameters)
        if "system.information_schema.columns" in statement:
            return [{"column_name": column_name} for column_name in self.column_names]
        if "system.information_schema.tables" in statement:
            return [{"table_name": table_name} for table_name in self.table_names]
        request_id = self._request_id(parameters)
        if "SELECT status_code, response" in statement:
            if self.rows_by_request_id is not None:
                rows = [dict(row) for row in self.rows_by_request_id.get(request_id, [])]
            else:
                rows = [dict(self.valid_row) for _ in range(self._count(request_id))]
            return self._with_timestamp_bounds(rows, parameters)
        if "COUNT(*) AS row_count" in statement:
            return [{"row_count": self._count(request_id)}]
        raise AssertionError(f"unexpected SQL: {statement}")

    def _with_timestamp_bounds(
        self,
        rows: list[dict[str, Any]],
        parameters: object | None,
    ) -> list[dict[str, Any]]:
        assert isinstance(parameters, dict)
        lower = parameters["proof_time_lower_bound"]
        upper = parameters["proof_time_upper_bound"]
        timestamp_column = next(
            column
            for column in ("event_time", "timestamp_ms", "request_time")
            if column in self.column_names
        )
        for row in rows:
            if timestamp_column not in row:
                if isinstance(lower, datetime) and isinstance(upper, datetime):
                    row[timestamp_column] = lower + (upper - lower) / 2
                else:
                    row[timestamp_column] = (int(lower) + int(upper)) // 2
            try:
                row["proof_timestamp_in_bounds"] = lower <= row[timestamp_column] <= upper
            except TypeError:
                row["proof_timestamp_in_bounds"] = False
        return rows

    def _count(self, request_id: str) -> int:
        if self.rows_by_request_id is not None:
            return len(self.rows_by_request_id.get(request_id, []))
        if self.counts_by_request_id is not None:
            return self.counts_by_request_id.get(request_id, 0)
        return self.exact_count

    @staticmethod
    def _request_id(parameters: object | None) -> str:
        if not isinstance(parameters, dict):
            return ""
        request_id = str(
            parameters.get("client_request_id") or parameters.get("client_request_marker") or ""
        )
        return request_id.strip("%")


def _tool_span(*, count: int | bool = 42) -> dict[str, object]:
    return {
        "trace_id": "AAAAAAAAAAAAAAAAAAAAAQ==",
        "span_id": "AAAAAAAAAAE=",
        "parent_span_id": None,
        "name": "mip__gold__fn_build_cohort",
        "start_time_unix_nano": 1,
        "end_time_unix_nano": 2,
        "events": [],
        "status": {"code": "STATUS_CODE_OK", "message": ""},
        "attributes": {
            "mlflow.traceRequestId": "tr-hosted-tool-test",
            "mlflow.spanType": json.dumps("TOOL"),
            "mlflow.spanInputs": json.dumps(
                {"segment_codes": ["itm"], "segment_mode": "any", "states": ["CA"]}
            ),
            "mlflow.spanOutputs": json.dumps(
                {
                    "result": json.dumps(
                        {
                            "is_truncated": False,
                            "columns": ["output"],
                            "rows": [[count]],
                        }
                    )
                }
            ),
        },
        "links": [],
    }


def _platform_trace_response(spans: list[dict[str, object]]) -> dict[str, object]:
    return {
        "custom_outputs": {
            "upstream_databricks_output": {
                "trace": {"info": {"request_id": "trace-request"}, "data": {"spans": spans}}
            }
        }
    }


class _Workspace:
    def __init__(self) -> None:
        self.api_client = type(
            "ApiClient",
            (),
            {
                "do": lambda _self, *_args, **_kwargs: {
                    "status": "completed",
                    **_platform_trace_response([_tool_span()]),
                    "output": [
                        {"content": [{"text": '{"tool":"fn_build_cohort","cohort_count":42}'}]}
                    ],
                }
            },
        )()
        self.serving_endpoints = type(
            "ServingEndpoints",
            (),
            {
                "get": lambda _self, _endpoint: type(
                    "Endpoint", (), {"task": "agent/v1/responses"}
                )()
            },
        )()


class _NoPayloadWorkspace(_Workspace):
    def __init__(self) -> None:
        super().__init__()
        self.api_client = type(
            "ApiClient",
            (),
            {
                "do": lambda _self, *_args, **_kwargs: {
                    "status": "completed",
                    "output": [],
                }
            },
        )()


class _NonTerminalWorkspace(_Workspace):
    def __init__(self) -> None:
        super().__init__()
        self.api_client = type(
            "ApiClient",
            (),
            {
                "do": lambda _self, *_args, **_kwargs: {
                    "status": "in_progress",
                    "output": [{"content": [{"text": "not terminal"}]}],
                }
            },
        )()


def test_workspace_client_disables_sdk_transport_retries(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def _config(**kwargs: Any) -> dict[str, Any]:
        captured["config_kwargs"] = kwargs
        return kwargs

    def _workspace_client(*, config: object) -> object:
        captured["config"] = config
        return object()

    monkeypatch.setattr(verify_ai_gateway_exact_proof, "Config", _config)
    monkeypatch.setattr(verify_ai_gateway_exact_proof, "WorkspaceClient", _workspace_client)
    monkeypatch.setenv("DATABRICKS_HOST", "https://verifier-workspace.example")
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "verifier-client")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "verifier-secret")
    monkeypatch.setenv("DATABRICKS_TOKEN", "hostile-deployer-pat")

    verify_ai_gateway_exact_proof._workspace_client()

    assert captured["config_kwargs"] == {
        "host": "https://verifier-workspace.example",
        "client_id": "verifier-client",
        "client_secret": "verifier-secret",
        "auth_type": "oauth-m2m",
        "http_timeout_seconds": 300,
        "retry_timeout_seconds": 0,
    }
    assert captured["config"] == captured["config_kwargs"]


def _patch_strict_runtime(monkeypatch, *, sql_client: _ProofSql) -> None:
    monkeypatch.setattr(
        verify_ai_gateway_exact_proof,
        "ensure_lakebase_env",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        verify_ai_gateway_exact_proof,
        "_verifier_sql_client",
        lambda _workspace, *, warehouse_id: sql_client,
    )


def test_verifier_runtime_overwrites_hostile_sql_and_lakebase_auth(monkeypatch) -> None:
    class _Config:
        host = "https://verifier-workspace.example"

        @staticmethod
        def authenticate() -> dict[str, str]:
            return {"Authorization": "Bearer verifier-workspace-token"}

    class _Database:
        @staticmethod
        def get_database_instance(_name: str) -> object:
            return type("Instance", (), {"read_write_dns": "verifier-lakebase.example"})()

        @staticmethod
        def generate_database_credential(**_kwargs: Any) -> object:
            return type("Credential", (), {"token": "verifier-lakebase-token"})()

    workspace = type(
        "VerifierWorkspace",
        (),
        {
            "config": _Config(),
            "database": _Database(),
            "current_user": type(
                "CurrentUser",
                (),
                {"me": staticmethod(lambda: type("Me", (), {"user_name": "verifier-client"})())},
            )(),
        },
    )()
    stale = verify_ai_gateway_exact_proof.get_settings()
    monkeypatch.setattr(stale, "lakebase_host", "hostile-dotenv-lakebase.example")
    monkeypatch.setattr(stale, "lakebase_user", "hostile-dotenv-user")
    monkeypatch.setattr(stale, "lakebase_password", None)
    for name, value in {
        "LAKEBASE_HOST": "hostile-ambient-lakebase.example",
        "LAKEBASE_USER": "hostile-ambient-user",
        "LAKEBASE_PASSWORD": "hostile-ambient-password",
        "PGHOST": "hostile-pg.example",
        "PGUSER": "hostile-pg-user",
        "PGPASSWORD": "hostile-pg-password",
        "DATABRICKS_TOKEN": "hostile-deployer-pat",
    }.items():
        monkeypatch.setenv(name, value)

    assert verify_ai_gateway_exact_proof.ensure_lakebase_env(
        lambda: workspace,
        force_refresh=True,
        instance_name="mip-pr105-state",
        database_name="mip_pr105_database",
    )
    sql_client = verify_ai_gateway_exact_proof._verifier_sql_client(
        workspace,
        warehouse_id="verifier-warehouse",
    )

    assert os.environ["LAKEBASE_HOST"] == "verifier-lakebase.example"
    assert os.environ["LAKEBASE_USER"] == "verifier-client"
    assert os.environ["LAKEBASE_PASSWORD"] == "verifier-lakebase-token"
    assert os.environ["PGHOST"] == "verifier-lakebase.example"
    assert os.environ["PGUSER"] == "verifier-client"
    assert os.environ["PGPASSWORD"] == "verifier-lakebase-token"
    assert os.environ["LAKEBASE_DATABASE"] == "mip_pr105_database"
    assert os.environ["PGDATABASE"] == "mip_pr105_database"
    assert stale.lakebase_host == "verifier-lakebase.example"
    assert stale.lakebase_user == "verifier-client"
    assert stale.lakebase_password.get_secret_value() == "verifier-lakebase-token"
    assert stale.lakebase_database == "mip_pr105_database"
    assert sql_client._host == "https://verifier-workspace.example"
    assert sql_client._warehouse_id == "verifier-warehouse"
    assert sql_client._token_provider() == "verifier-workspace-token"


@pytest.mark.parametrize("mode", ("verify-pending", "send"))
def test_every_proof_mode_requires_verifier_derived_auth(mode: str) -> None:
    with pytest.raises(
        ValueError,
        match="exact proof requires --require-verifier-derived-auth for every mode",
    ):
        verify_ai_gateway_exact_proof.main(
            [
                mode,
                "--git-sha",
                _TEST_GIT_SHA,
                "--endpoint",
                _ENDPOINT,
                "--inference-table",
                _INFERENCE_TABLE,
            ]
        )


def _verified_row(*, git_sha: str, verified_at: datetime) -> dict[str, Any]:
    sent_at = verified_at - timedelta(minutes=4)
    row = {
        "proof_id": "11111111-1111-4111-8111-111111111111",
        "git_sha": git_sha,
        "client_request_id": f"mip-capability-{git_sha}-0123456789abcdef",
        "endpoint_name": _ENDPOINT,
        "inference_table": _INFERENCE_TABLE,
        "sent_at": sent_at,
        "verified_at": verified_at,
        "verify_latency_s": 240.0,
        "status": "verified",
    }
    alg, key_id, signature = sign_gateway_proof(
        signing_key=_TEST_SIGNING_KEY,
        proof_id=row["proof_id"],
        git_sha=row["git_sha"],
        client_request_id=row["client_request_id"],
        endpoint_name=row["endpoint_name"],
        inference_table=row["inference_table"],
        sent_at=row["sent_at"],
        verified_at=row["verified_at"],
    )
    row.update(
        attestation_alg=alg,
        attestation_key_id=key_id,
        attestation_signature=signature,
    )
    return row


def test_latest_verified_proof_requires_current_sha_and_freshness() -> None:
    now = datetime.now(UTC)
    lakebase = _ProofLakebase(
        [
            _verified_row(git_sha=_OTHER_GIT_SHA, verified_at=now),
            _verified_row(git_sha=_TEST_GIT_SHA, verified_at=now - timedelta(hours=30)),
            _verified_row(git_sha=_TEST_GIT_SHA, verified_at=now - timedelta(minutes=5)),
        ]
    )

    proof = latest_verified_proof(
        lakebase,
        git_sha=_TEST_GIT_SHA,
        endpoint_name=_ENDPOINT,
        inference_table=_INFERENCE_TABLE,
        freshness_s=26 * 60 * 60,
        attestation_verify_key=_TEST_VERIFY_KEY,
        now=now,
    )

    assert proof is not None
    assert proof.git_sha == _TEST_GIT_SHA
    assert proof.verified_at == now - timedelta(minutes=5)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("git_sha", _OTHER_GIT_SHA),
        ("client_request_id", f"mip-capability-{_TEST_GIT_SHA}-ffffffffffffffff"),
        ("endpoint_name", "tampered-endpoint"),
        ("inference_table", "mip.audit.tampered_gateway_table"),
    ],
)
def test_latest_verified_proof_rejects_tampered_signed_coordinates(
    field: str,
    replacement: str,
) -> None:
    now = datetime.now(UTC)
    row = _verified_row(git_sha=_TEST_GIT_SHA, verified_at=now - timedelta(minutes=1))
    row[field] = replacement
    query_sha = str(row["git_sha"])
    query_endpoint = str(row["endpoint_name"])
    query_table = str(row["inference_table"])

    proof = latest_verified_proof(
        _ProofLakebase([row]),
        git_sha=query_sha,
        endpoint_name=query_endpoint,
        inference_table=query_table,
        freshness_s=60,
        attestation_verify_key=_TEST_VERIFY_KEY,
        now=now,
    )

    assert proof is None


def test_latest_verified_proof_rejects_unsigned_or_wrongly_signed_writer_rows() -> None:
    now = datetime.now(UTC)
    unsigned = _verified_row(git_sha=_TEST_GIT_SHA, verified_at=now - timedelta(minutes=1))
    unsigned.update(
        attestation_alg=None,
        attestation_key_id=None,
        attestation_signature=None,
    )
    wrong_key = _verified_row(git_sha=_TEST_GIT_SHA, verified_at=now - timedelta(minutes=1))
    alg, key_id, signature = sign_gateway_proof(
        signing_key=_OTHER_SIGNING_KEY,
        proof_id=str(wrong_key["proof_id"]),
        git_sha=str(wrong_key["git_sha"]),
        client_request_id=str(wrong_key["client_request_id"]),
        endpoint_name=str(wrong_key["endpoint_name"]),
        inference_table=str(wrong_key["inference_table"]),
        sent_at=wrong_key["sent_at"],
        verified_at=wrong_key["verified_at"],
    )
    wrong_key.update(
        attestation_alg=alg,
        attestation_key_id=key_id,
        attestation_signature=signature,
    )

    for row in (unsigned, wrong_key):
        assert (
            latest_verified_proof(
                _ProofLakebase([row]),
                git_sha=_TEST_GIT_SHA,
                endpoint_name=_ENDPOINT,
                inference_table=_INFERENCE_TABLE,
                freshness_s=60,
                attestation_verify_key=_TEST_VERIFY_KEY,
                now=now,
            )
            is None
        )
    assert _OTHER_VERIFY_KEY != _TEST_VERIFY_KEY


def test_latest_verified_proof_requires_matching_endpoint_and_table() -> None:
    now = datetime.now(UTC)
    lakebase = _ProofLakebase([_verified_row(git_sha=_TEST_GIT_SHA, verified_at=now)])

    wrong_endpoint = latest_verified_proof(
        lakebase,
        git_sha=_TEST_GIT_SHA,
        endpoint_name="different-endpoint",
        inference_table=_INFERENCE_TABLE,
        freshness_s=60,
        attestation_verify_key=_TEST_VERIFY_KEY,
        now=now,
    )
    wrong_table = latest_verified_proof(
        lakebase,
        git_sha=_TEST_GIT_SHA,
        endpoint_name=_ENDPOINT,
        inference_table="mip.audit.different_gateway_table",
        freshness_s=60,
        attestation_verify_key=_TEST_VERIFY_KEY,
        now=now,
    )

    assert wrong_endpoint is None
    assert wrong_table is None


def test_latest_verified_proof_caps_defensive_callers_at_26_hours() -> None:
    now = datetime.now(UTC)
    lakebase = _ProofLakebase(
        [_verified_row(git_sha=_TEST_GIT_SHA, verified_at=now - timedelta(hours=27))]
    )

    proof = latest_verified_proof(
        lakebase,
        git_sha=_TEST_GIT_SHA,
        endpoint_name=_ENDPOINT,
        inference_table=_INFERENCE_TABLE,
        freshness_s=7 * 24 * 60 * 60,
        attestation_verify_key=_TEST_VERIFY_KEY,
        now=now,
    )

    assert proof is None
    _sql, params = lakebase.fetchone_calls[-1]
    assert params["cutoff"] == now - timedelta(seconds=AI_GATEWAY_PROOF_FRESHNESS_MAX_S)


def test_latest_verified_proof_rejects_future_timestamp_beyond_clock_tolerance() -> None:
    now = datetime.now(UTC)
    lakebase = _ProofLakebase(
        [
            _verified_row(
                git_sha=_TEST_GIT_SHA,
                verified_at=now + timedelta(seconds=AI_GATEWAY_PROOF_CLOCK_SKEW_S + 1),
            )
        ]
    )

    proof = latest_verified_proof(
        lakebase,
        git_sha=_TEST_GIT_SHA,
        endpoint_name=_ENDPOINT,
        inference_table=_INFERENCE_TABLE,
        freshness_s=60,
        attestation_verify_key=_TEST_VERIFY_KEY,
        now=now,
    )

    assert proof is None
    sql, params = lakebase.fetchone_calls[-1]
    assert "verified_at <= %(future_cutoff)s" in sql
    assert "sent_at <= %(future_cutoff)s" in sql
    assert params["future_cutoff"] == now + timedelta(seconds=AI_GATEWAY_PROOF_CLOCK_SKEW_S)


def test_pending_proof_insert_rejects_future_sent_at_before_lakebase_write() -> None:
    lakebase = _ProofLakebase()

    with pytest.raises(ValueError, match="sent_at exceeds"):
        insert_pending_proof(
            lakebase,
            git_sha=_TEST_GIT_SHA,
            client_request_id=f"mip-capability-{_TEST_GIT_SHA}-1010101010101010",
            endpoint_name=_ENDPOINT,
            inference_table=_INFERENCE_TABLE,
            sent_at=datetime.now(UTC) + timedelta(seconds=AI_GATEWAY_PROOF_CLOCK_SKEW_S + 10),
        )

    assert lakebase.fetchone_calls == []


def test_verified_proof_write_rejects_future_verified_at_before_lakebase_update() -> None:
    lakebase = _ProofLakebase()
    proof = insert_pending_proof(
        lakebase,
        git_sha=_TEST_GIT_SHA,
        client_request_id=f"mip-capability-{_TEST_GIT_SHA}-2020202020202020",
        endpoint_name=_ENDPOINT,
        inference_table=_INFERENCE_TABLE,
    )
    calls_before_update = len(lakebase.fetchone_calls)

    with pytest.raises(ValueError, match="verified_at exceeds"):
        mark_proof_verified(
            lakebase,
            proof_id=proof.proof_id,
            sent_at=proof.sent_at,
            attestation_signing_key=_TEST_SIGNING_KEY,
            verified_at=datetime.now(UTC) + timedelta(seconds=AI_GATEWAY_PROOF_CLOCK_SKEW_S + 10),
        )

    assert len(lakebase.fetchone_calls) == calls_before_update


def test_pending_proof_verification_and_expiry() -> None:
    lakebase = _ProofLakebase()
    old_sent_at = datetime.now(UTC) - timedelta(hours=7)
    proof = insert_pending_proof(
        lakebase,
        git_sha=_TEST_GIT_SHA,
        client_request_id=f"mip-capability-{_TEST_GIT_SHA}-0123456789abcdef",
        endpoint_name=_ENDPOINT,
        inference_table=_INFERENCE_TABLE,
        sent_at=old_sent_at,
    )

    verified = mark_proof_verified(
        lakebase,
        proof_id=proof.proof_id,
        sent_at=proof.sent_at,
        attestation_signing_key=_TEST_SIGNING_KEY,
        verified_at=old_sent_at + timedelta(minutes=3),
    )
    expired = mark_expired_pending_proofs(
        lakebase,
        older_than=datetime.now(UTC) - timedelta(hours=6),
        git_sha=_TEST_GIT_SHA,
    )

    assert verified.status == "verified"
    assert verified.verify_latency_s == 180.0
    assert expired == 0


def test_mark_expired_pending_proofs_marks_expired_not_failed() -> None:
    lakebase = _ProofLakebase()
    proof = insert_pending_proof(
        lakebase,
        git_sha=_TEST_GIT_SHA,
        client_request_id=f"mip-capability-{_TEST_GIT_SHA}-fedcba9876543210",
        endpoint_name=_ENDPOINT,
        inference_table=_INFERENCE_TABLE,
        sent_at=datetime.now(UTC) - timedelta(hours=7),
    )

    expired = mark_expired_pending_proofs(
        lakebase,
        older_than=datetime.now(UTC) - timedelta(hours=6),
        git_sha=proof.git_sha,
    )

    assert expired == 1
    assert lakebase.rows[0]["status"] == "expired"


def test_verifier_send_wait_records_exact_verified_row(monkeypatch) -> None:
    lakebase = _ProofLakebase()
    monkeypatch.setattr(verify_ai_gateway_exact_proof, "get_lakebase_client", lambda: lakebase)
    _patch_strict_runtime(monkeypatch, sql_client=_ProofSql(exact_count=1))
    monkeypatch.setattr(verify_ai_gateway_exact_proof, "_workspace_client", lambda: _Workspace())

    exit_code = verify_ai_gateway_exact_proof.main(
        [
            "send",
            "--wait",
            "--require-verified",
            "--require-verifier-derived-auth",
            "--warehouse-id",
            "warehouse-id",
            "--git-sha",
            _TEST_GIT_SHA,
            "--endpoint",
            _ENDPOINT,
            "--inference-table",
            _INFERENCE_TABLE,
            "--expected-tool-count",
            "42",
            "--timeout-s",
            "1",
            "--interval-s",
            "1",
        ]
    )

    assert exit_code == 0
    assert len(lakebase.rows) == 1
    assert lakebase.rows[0]["status"] == "verified"
    assert str(lakebase.rows[0]["client_request_id"]).startswith(f"mip-capability-{_TEST_GIT_SHA}-")


def test_verifier_require_verified_accepts_existing_current_sha_proof(monkeypatch) -> None:
    lakebase = _ProofLakebase(
        [_verified_row(git_sha=_TEST_GIT_SHA, verified_at=datetime.now(UTC) - timedelta(minutes=5))]
    )
    monkeypatch.setattr(verify_ai_gateway_exact_proof, "get_lakebase_client", lambda: lakebase)
    _patch_strict_runtime(monkeypatch, sql_client=_ProofSql(exact_count=0))
    monkeypatch.setattr(verify_ai_gateway_exact_proof, "_workspace_client", lambda: _Workspace())

    exit_code = verify_ai_gateway_exact_proof.main(
        [
            "verify-pending",
            "--require-verified",
            "--require-verifier-derived-auth",
            "--warehouse-id",
            "warehouse-id",
            "--git-sha",
            _TEST_GIT_SHA,
            "--endpoint",
            _ENDPOINT,
            "--inference-table",
            _INFERENCE_TABLE,
        ]
    )

    assert exit_code == 0


def test_verifier_require_verified_rejects_wrong_endpoint_pending_false_pass(monkeypatch) -> None:
    lakebase = _ProofLakebase()
    pending = insert_pending_proof(
        lakebase,
        git_sha=_TEST_GIT_SHA,
        client_request_id=f"mip-capability-{_TEST_GIT_SHA}-9999999999999999",
        endpoint_name="wrong-gateway-endpoint",
        inference_table=_INFERENCE_TABLE,
        sent_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    monkeypatch.setattr(verify_ai_gateway_exact_proof, "get_lakebase_client", lambda: lakebase)
    _patch_strict_runtime(monkeypatch, sql_client=_ProofSql(exact_count=1))
    monkeypatch.setattr(verify_ai_gateway_exact_proof, "_workspace_client", lambda: _Workspace())

    exit_code = verify_ai_gateway_exact_proof.main(
        [
            "verify-pending",
            "--require-verified",
            "--require-verifier-derived-auth",
            "--warehouse-id",
            "warehouse-id",
            "--git-sha",
            _TEST_GIT_SHA,
            "--endpoint",
            _ENDPOINT,
            "--inference-table",
            _INFERENCE_TABLE,
        ]
    )

    assert exit_code == 1
    assert lakebase.rows[0]["proof_id"] == pending.proof_id
    assert lakebase.rows[0]["status"] == "pending"


def test_verifier_requires_exact_client_request_id(monkeypatch) -> None:
    lakebase = _ProofLakebase()
    pending_id = f"mip-capability-{_TEST_GIT_SHA}-aaaaaaaaaaaaaaaa"
    other_id = f"mip-capability-{_TEST_GIT_SHA}-bbbbbbbbbbbbbbbb"
    pending = insert_pending_proof(
        lakebase,
        git_sha=_TEST_GIT_SHA,
        client_request_id=pending_id,
        endpoint_name=_ENDPOINT,
        inference_table=_INFERENCE_TABLE,
        sent_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    monkeypatch.setattr(verify_ai_gateway_exact_proof, "get_lakebase_client", lambda: lakebase)
    _patch_strict_runtime(
        monkeypatch,
        sql_client=_ProofSql(counts_by_request_id={other_id: 1}),
    )
    monkeypatch.setattr(verify_ai_gateway_exact_proof, "_workspace_client", lambda: _Workspace())

    exit_code = verify_ai_gateway_exact_proof.main(
        [
            "verify-pending",
            "--require-verified",
            "--require-verifier-derived-auth",
            "--warehouse-id",
            "warehouse-id",
            "--git-sha",
            _TEST_GIT_SHA,
            "--endpoint",
            _ENDPOINT,
            "--inference-table",
            _INFERENCE_TABLE,
        ]
    )

    assert exit_code == 1
    assert lakebase.rows[0]["proof_id"] == pending.proof_id
    assert lakebase.rows[0]["status"] == "failed"


def test_wait_for_exact_row_ignores_row_under_a_different_request_id() -> None:
    """A logged inference row under some OTHER client_request_id must not
    verify this pending proof -- the exact-id match is what makes the proof
    trustworthy, so a wrong-id row fails the in-process proof."""
    lakebase = _ProofLakebase()
    proof = insert_pending_proof(
        lakebase,
        git_sha=_TEST_GIT_SHA,
        client_request_id=f"mip-capability-{_TEST_GIT_SHA}-1111111111111111",
        endpoint_name=_ENDPOINT,
        inference_table=_INFERENCE_TABLE,
        sent_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    other_request_id = f"mip-capability-{_TEST_GIT_SHA}-2222222222222222"

    verified = verify_ai_gateway_exact_proof.wait_for_exact_row(
        lakebase=lakebase,
        sql_client=_ProofSql(counts_by_request_id={other_request_id: 5}),
        proof=proof,
        timeout_s=0,
        interval_s=1,
    )

    assert verified == []
    assert lakebase.rows[0]["proof_id"] == proof.proof_id
    assert lakebase.rows[0]["status"] == "failed"


def test_exact_row_check_defensively_ignores_nonliteral_prefix_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lakebase = _ProofLakebase()
    proof = insert_pending_proof(
        lakebase,
        git_sha=_TEST_GIT_SHA,
        client_request_id=f"mip-capability-{_TEST_GIT_SHA}-1212121212121212",
        endpoint_name=_ENDPOINT,
        inference_table=_INFERENCE_TABLE,
    )
    sql = _ProofSql(exact_count=1)
    monkeypatch.setattr(
        verify_ai_gateway_exact_proof,
        "inference_log_table_names",
        lambda *_args: [
            "mipXagentXgatewayXllama_payload",
            "mip_agent_gateway_llama_payload",
        ],
    )

    check = verify_ai_gateway_exact_proof._check_exact_inference_row(sql, proof)

    assert check.outcome == "verified"
    assert all("mipXagentXgatewayXllama_payload" not in statement for statement in sql.statements)


@pytest.mark.parametrize("timestamp_column", ["event_time", "timestamp_ms"])
@pytest.mark.parametrize(
    "offset_s",
    [-(AI_GATEWAY_PROOF_CLOCK_SKEW_S + 1), AI_GATEWAY_PROOF_CLOCK_SKEW_S + 1],
    ids=["before-lower-bound", "future"],
)
def test_exact_row_check_rejects_out_of_window_inference_timestamp(
    timestamp_column: str,
    offset_s: int,
) -> None:
    lakebase = _ProofLakebase()
    proof = insert_pending_proof(
        lakebase,
        git_sha=_TEST_GIT_SHA,
        client_request_id=f"mip-capability-{_TEST_GIT_SHA}-3030303030303030",
        endpoint_name=_ENDPOINT,
        inference_table=_INFERENCE_TABLE,
    )
    event_time = proof.sent_at + timedelta(seconds=offset_s)
    timestamp_value: object = event_time
    if timestamp_column == "timestamp_ms":
        timestamp_value = int(event_time.timestamp() * 1000)
    sql = _ProofSql(
        rows_by_request_id={
            proof.client_request_id: [
                {
                    **_ProofSql.valid_row,
                    timestamp_column: timestamp_value,
                }
            ]
        },
        column_names=[
            "client_request_id",
            "status_code",
            "response",
            timestamp_column,
        ],
    )

    check = verify_ai_gateway_exact_proof._check_exact_inference_row(sql, proof)

    assert check.outcome == "failed"
    assert check.reason == "timestamp_out_of_bounds"
    bounded_params = next(
        params
        for params in sql.parameters
        if isinstance(params, dict) and "proof_time_upper_bound" in params
    )
    assert "proof_time_lower_bound" in bounded_params


def test_lakebase_schema_rejects_future_gateway_proof_writes() -> None:
    schema = (_REPO_ROOT / "lakebase" / "schema.sql").read_text(encoding="utf-8")

    assert (
        "CREATE OR REPLACE FUNCTION mip_app.enforce_ai_gateway_proof_timestamp_bounds()" in schema
    )
    assert "clock_tolerance CONSTANT INTERVAL := INTERVAL '5 minutes'" in schema
    assert "IF NEW.status IN ('pending', 'verified')" in schema
    assert "AND NEW.sent_at > observed_now + clock_tolerance" in schema
    assert "NEW.verified_at > observed_now + clock_tolerance" in schema
    assert "CREATE TRIGGER trg_ai_gateway_proof_timestamp_bounds" in schema


def test_wait_for_exact_row_timeout_fails_unattested_proof() -> None:
    lakebase = _ProofLakebase()
    proof = insert_pending_proof(
        lakebase,
        git_sha=_TEST_GIT_SHA,
        client_request_id=f"mip-capability-{_TEST_GIT_SHA}-cccccccccccccccc",
        endpoint_name=_ENDPOINT,
        inference_table=_INFERENCE_TABLE,
        sent_at=datetime.now(UTC) - timedelta(minutes=5),
    )

    verified = verify_ai_gateway_exact_proof.wait_for_exact_row(
        lakebase=lakebase,
        sql_client=_ProofSql(counts_by_request_id={}),
        proof=proof,
        timeout_s=0,
        interval_s=1,
    )

    assert verified == []
    assert lakebase.rows[0]["status"] == "failed"


def test_verify_pending_rejects_duplicate_exact_rows() -> None:
    lakebase = _ProofLakebase()
    proof = insert_pending_proof(
        lakebase,
        git_sha=_TEST_GIT_SHA,
        client_request_id=f"mip-capability-{_TEST_GIT_SHA}-eeeeeeeeeeeeeeee",
        endpoint_name=_ENDPOINT,
        inference_table=_INFERENCE_TABLE,
        sent_at=datetime.now(UTC) - timedelta(minutes=5),
    )

    verified = verify_ai_gateway_exact_proof.verify_pending(
        lakebase=lakebase,
        sql_client=_ProofSql(counts_by_request_id={proof.client_request_id: 2}),
        git_sha=_TEST_GIT_SHA,
        endpoint=_ENDPOINT,
        inference_table=_INFERENCE_TABLE,
        limit=100,
    )

    assert verified == []
    assert lakebase.rows[0]["status"] == "failed"


def test_correct_count_without_structured_tool_call_is_not_proof() -> None:
    response = {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"text": '{"tool":"fn_build_cohort","cohort_count":42}'}],
            }
        ],
    }

    assert not verify_ai_gateway_exact_proof.response_proves_build_cohort_tool(
        response,
        expected_count=42,
    )


@pytest.mark.parametrize(
    ("trace_overrides", "expected_count"),
    [
        ({"inputs": {"segment_codes": ["itm"], "segment_mode": "all", "states": ["CA"]}}, 42),
        ({"outputs": {"cohort_count": 41}}, 42),
        ({"status": {"status_code": "ERROR"}}, 42),
    ],
)
def test_trace_requires_exact_successful_tool_arguments_and_result(
    trace_overrides: dict[str, object],
    expected_count: int,
) -> None:
    span = _tool_span()
    if "status" in trace_overrides:
        span["status"] = trace_overrides["status"]
    attributes = dict(span["attributes"])  # type: ignore[arg-type]
    if "inputs" in trace_overrides:
        attributes["mlflow.spanInputs"] = json.dumps(trace_overrides["inputs"])
    if "outputs" in trace_overrides:
        attributes["mlflow.spanOutputs"] = json.dumps(trace_overrides["outputs"])
    span["attributes"] = attributes
    response = _platform_trace_response([span])

    assert not verify_ai_gateway_exact_proof.response_proves_build_cohort_tool(
        response,
        expected_count=expected_count,
    )


def test_pending_function_call_is_not_execution_proof() -> None:
    response = {
        "custom_outputs": {
            "databricks_trace": {
                "spans": [
                    {
                        "name": "build_cohort",
                        "span_type": "TOOL",
                        "status": {"status_code": "IN_PROGRESS"},
                        "inputs": {
                            "segment_codes": [],
                            "segment_mode": "any",
                            "states": ["CA"],
                        },
                    }
                ]
            }
        }
    }

    assert not verify_ai_gateway_exact_proof.response_proves_build_cohort_tool(
        response,
        expected_count=42,
    )


def test_negative_marker_substrings_and_unrelated_result_are_not_trace_proof() -> None:
    response = {
        "custom_outputs": {
            "databricks_trace": {
                "spans": [
                    {
                        "name": "not_build_cohort",
                        "span_type": "NOT_A_TOOL",
                        "status": {"status_code": "NOT_OK"},
                        "inputs": {
                            "segment_codes": [],
                            "segment_mode": "any",
                            "states": ["CA"],
                        },
                        "outputs": {"unrelated_total": 42},
                    }
                ]
            }
        }
    }

    assert not verify_ai_gateway_exact_proof.response_proves_build_cohort_tool(
        response,
        expected_count=42,
    )


@pytest.mark.parametrize("trace_key", ["evil_trace", "not_a_trace"])
def test_trace_like_fields_outside_exact_proxy_path_are_not_proof(trace_key: str) -> None:
    response = {
        "output": [
            {
                trace_key: {
                    "spans": [
                        {
                            "name": "build_cohort",
                            "span_type": "TOOL",
                            "status": {"status_code": "OK"},
                            "inputs": {
                                "segment_codes": [],
                                "segment_mode": "any",
                                "states": ["CA"],
                            },
                            "outputs": {"cohort_count": 42},
                        }
                    ]
                }
            }
        ]
    }

    assert not verify_ai_gateway_exact_proof.response_proves_build_cohort_tool(
        response,
        expected_count=42,
    )


def test_duplicate_hosted_tool_spans_are_not_exact_execution_proof() -> None:
    span = _tool_span()
    response = _platform_trace_response([span, dict(span)])

    assert not verify_ai_gateway_exact_proof.response_proves_build_cohort_tool(
        response,
        expected_count=42,
    )


def test_boolean_tool_result_is_not_numeric_count_proof() -> None:
    response = _platform_trace_response([_tool_span(count=True)])

    assert not verify_ai_gateway_exact_proof.response_proves_build_cohort_tool(
        response,
        expected_count=1,
    )


def test_wait_for_exact_row_rejects_duplicate_exact_rows() -> None:
    lakebase = _ProofLakebase()
    proof = insert_pending_proof(
        lakebase,
        git_sha=_TEST_GIT_SHA,
        client_request_id=f"mip-capability-{_TEST_GIT_SHA}-ffffffffffffffff",
        endpoint_name=_ENDPOINT,
        inference_table=_INFERENCE_TABLE,
        sent_at=datetime.now(UTC) - timedelta(minutes=5),
    )

    verified = verify_ai_gateway_exact_proof.wait_for_exact_row(
        lakebase=lakebase,
        sql_client=_ProofSql(counts_by_request_id={proof.client_request_id: 2}),
        proof=proof,
        timeout_s=60,
        interval_s=1,
    )

    assert verified == []
    assert lakebase.rows[0]["status"] == "failed"


def test_verify_pending_fails_closed_when_schema_cannot_substantiate_success() -> None:
    lakebase = _ProofLakebase()
    proof = insert_pending_proof(
        lakebase,
        git_sha=_TEST_GIT_SHA,
        client_request_id=f"mip-capability-{_TEST_GIT_SHA}-abababababababab",
        endpoint_name=_ENDPOINT,
        inference_table=_INFERENCE_TABLE,
    )

    verified = verify_ai_gateway_exact_proof.verify_pending(
        lakebase=lakebase,
        sql_client=_ProofSql(
            counts_by_request_id={proof.client_request_id: 1},
            column_names=["client_request_id", "request", "databricks_request_id"],
        ),
        git_sha=_TEST_GIT_SHA,
        endpoint=_ENDPOINT,
        inference_table=_INFERENCE_TABLE,
        limit=100,
    )

    assert verified == []
    assert lakebase.rows[0]["status"] == "failed"


@pytest.mark.parametrize(
    "logged_row",
    [
        {
            "status_code": 500,
            "response": json.dumps(
                {"status": "completed", "output": [{"content": [{"text": "bad"}]}]}
            ),
        },
        {
            "status_code": 200,
            "response": json.dumps(
                {"status": "in_progress", "output": [{"content": [{"text": "later"}]}]}
            ),
        },
        {
            "status_code": 200,
            "response": json.dumps({"status": "completed", "output": []}),
        },
    ],
    ids=["non-2xx", "nonterminal", "completed-no-payload"],
)
def test_verify_pending_rejects_unsuccessful_or_nonterminal_row(
    logged_row: dict[str, Any],
) -> None:
    lakebase = _ProofLakebase()
    proof = insert_pending_proof(
        lakebase,
        git_sha=_TEST_GIT_SHA,
        client_request_id=f"mip-capability-{_TEST_GIT_SHA}-cdcdcdcdcdcdcdcd",
        endpoint_name=_ENDPOINT,
        inference_table=_INFERENCE_TABLE,
    )

    verified = verify_ai_gateway_exact_proof.verify_pending(
        lakebase=lakebase,
        sql_client=_ProofSql(rows_by_request_id={proof.client_request_id: [logged_row]}),
        git_sha=_TEST_GIT_SHA,
        endpoint=_ENDPOINT,
        inference_table=_INFERENCE_TABLE,
        limit=100,
    )

    assert verified == []
    assert lakebase.rows[0]["status"] == "failed"


@pytest.mark.parametrize(
    "submission_error",
    [TimeoutError("request timed out"), RuntimeError("503 Service Unavailable")],
)
def test_exact_proof_timeout_or_503_fails_and_is_never_retried(
    submission_error: Exception,
) -> None:
    lakebase = _ProofLakebase()

    class _ApiClient:
        def __init__(self) -> None:
            self.request_ids: list[str] = []

        def do(self, _method: str, _path: str, *, body: dict[str, Any]) -> dict[str, Any]:
            request_id = str(body["client_request_id"])
            self.request_ids.append(request_id)
            if request_id.startswith("mip-capability-"):
                raise submission_error
            return {"status": "completed", "output": [{"content": [{"text": "warm"}]}]}

    workspace = _Workspace()
    workspace.api_client = _ApiClient()

    with pytest.raises(RuntimeError, match="ambiguous and cannot become proof"):
        verify_ai_gateway_exact_proof.send_probe(
            lakebase=lakebase,
            workspace=workspace,
            endpoint=_ENDPOINT,
            inference_table=_INFERENCE_TABLE,
            git_sha=_TEST_GIT_SHA,
            expected_tool_count=42,
        )

    proof_ids = [
        request_id
        for request_id in workspace.api_client.request_ids
        if request_id.startswith("mip-capability-")
    ]
    warmup_ids = [
        request_id
        for request_id in workspace.api_client.request_ids
        if request_id.startswith("mip-warmup-")
    ]
    assert len(proof_ids) == 1
    assert len(warmup_ids) == 1
    assert proof_ids[0] not in warmup_ids
    assert lakebase.rows[0]["status"] == "failed"


def test_strict_send_wait_requires_the_sent_proof(monkeypatch, capsys) -> None:
    lakebase = _ProofLakebase(
        [_verified_row(git_sha=_TEST_GIT_SHA, verified_at=datetime.now(UTC) - timedelta(minutes=5))]
    )
    monkeypatch.setattr(verify_ai_gateway_exact_proof, "get_lakebase_client", lambda: lakebase)
    _patch_strict_runtime(
        monkeypatch,
        sql_client=_ProofSql(counts_by_request_id={}),
    )
    monkeypatch.setattr(verify_ai_gateway_exact_proof, "_workspace_client", lambda: _Workspace())

    exit_code = verify_ai_gateway_exact_proof.main(
        [
            "send",
            "--wait",
            "--require-verified",
            "--require-verifier-derived-auth",
            "--warehouse-id",
            "warehouse-id",
            "--git-sha",
            _TEST_GIT_SHA,
            "--endpoint",
            _ENDPOINT,
            "--inference-table",
            _INFERENCE_TABLE,
            "--expected-tool-count",
            "42",
            "--timeout-s",
            "0",
            "--interval-s",
            "1",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert lakebase.rows[0]["status"] == "verified"
    assert lakebase.rows[1]["status"] == "failed"
    assert _ENDPOINT not in output
    assert _INFERENCE_TABLE not in output
    assert str(lakebase.rows[1]["client_request_id"]) not in output


def test_proof_summary_redacts_coordinates() -> None:
    lakebase = _ProofLakebase()
    pending = insert_pending_proof(
        lakebase,
        git_sha=_TEST_GIT_SHA,
        client_request_id=f"mip-capability-{_TEST_GIT_SHA}-dddddddddddddddd",
        endpoint_name=_ENDPOINT,
        inference_table=_INFERENCE_TABLE,
        sent_at=datetime.now(UTC) - timedelta(minutes=2),
    )
    proof = mark_proof_verified(
        lakebase,
        proof_id=pending.proof_id,
        sent_at=pending.sent_at,
        attestation_signing_key=_TEST_SIGNING_KEY,
    )
    payload = verify_ai_gateway_exact_proof._proof_json(proof)
    encoded = json.dumps(payload)

    assert payload is not None
    assert payload["client_request_id"] == "<redacted>"
    assert payload["endpoint_name"] == "<redacted>"
    assert payload["inference_table"] == "<redacted>"
    assert proof.client_request_id not in encoded
    assert proof.endpoint_name not in encoded
    assert proof.inference_table not in encoded


@pytest.mark.parametrize("workspace", [_NoPayloadWorkspace(), _NonTerminalWorkspace()])
def test_rejected_send_is_failed_and_later_row_cannot_promote(
    workspace: _Workspace,
) -> None:
    lakebase = _ProofLakebase()

    with pytest.raises(RuntimeError) as exc:
        verify_ai_gateway_exact_proof.send_probe(
            lakebase=lakebase,
            workspace=workspace,
            endpoint=_ENDPOINT,
            inference_table=_INFERENCE_TABLE,
            git_sha=_TEST_GIT_SHA,
            expected_tool_count=42,
        )

    message = str(exc.value)
    assert "did not return a terminal completed payload" in message
    assert _ENDPOINT not in message
    assert _INFERENCE_TABLE not in message
    assert len(lakebase.rows) == 1
    assert lakebase.rows[0]["status"] == "failed"


def test_acknowledgement_only_response_cannot_prove_tool_execution() -> None:
    lakebase = _ProofLakebase()
    workspace = _Workspace()
    workspace.api_client = type(
        "ApiClient",
        (),
        {
            "do": lambda _self, *_args, **_kwargs: {
                "status": "completed",
                "output": [{"content": [{"text": "Gateway logging acknowledged."}]}],
            }
        },
    )()

    with pytest.raises(RuntimeError, match="did not prove reviewed fn_build_cohort execution"):
        verify_ai_gateway_exact_proof.send_probe(
            lakebase=lakebase,
            workspace=workspace,
            endpoint=_ENDPOINT,
            inference_table=_INFERENCE_TABLE,
            git_sha=_TEST_GIT_SHA,
            expected_tool_count=42,
        )

    assert lakebase.rows[0]["status"] == "failed"

    verified = verify_ai_gateway_exact_proof.verify_pending(
        lakebase=lakebase,
        sql_client=_ProofSql(exact_count=1),
        git_sha=_TEST_GIT_SHA,
        endpoint=_ENDPOINT,
        inference_table=_INFERENCE_TABLE,
        limit=100,
    )

    assert verified == []
    assert lakebase.rows[0]["status"] == "failed"


def test_verifier_script_path_help_runs_without_pythonpath() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(_REPO_ROOT / "tools/databricks/verify_ai_gateway_exact_proof.py"),
            "--help",
        ],
        cwd=_REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "verify-pending" in result.stdout
