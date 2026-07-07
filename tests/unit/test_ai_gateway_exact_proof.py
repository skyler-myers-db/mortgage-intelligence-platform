from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from backend.services.ai_gateway_proof_ledger import (
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


class _ProofLakebase:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = list(rows or [])

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        params = params or {}
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
            }
            self.rows.append(row)
            return dict(row)
        if "UPDATE mip_app.ai_gateway_proof_ledger" in sql and "SET status = 'verified'" in sql:
            proof_id = str(params["proof_id"])
            for row in self.rows:
                if str(row["proof_id"]) == proof_id:
                    row["status"] = "verified"
                    row["verified_at"] = params["verified_at"]
                    row["verify_latency_s"] = params["verify_latency_s"]
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
        if "FROM mip_app.ai_gateway_proof_ledger" in sql and "status = 'verified'" in sql:
            git_sha = params["git_sha"]
            endpoint_name = params["endpoint_name"]
            inference_table = params["inference_table"]
            cutoff = params["cutoff"]
            matches = [
                dict(row)
                for row in self.rows
                if row["git_sha"] == git_sha
                and row["endpoint_name"] == endpoint_name
                and row["inference_table"] == inference_table
                and row["status"] == "verified"
                and row["verified_at"] is not None
                and row["verified_at"] >= cutoff
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

    def __init__(
        self,
        *,
        exact_count: int = 0,
        counts_by_request_id: dict[str, int] | None = None,
    ) -> None:
        self.exact_count = exact_count
        self.counts_by_request_id = counts_by_request_id

    def execute(self, statement: str, parameters: object | None = None) -> list[dict[str, Any]]:
        if "system.information_schema.tables" in statement:
            return [{"table_name": table_name} for table_name in self.table_names]
        if "COUNT(*) AS row_count" in statement:
            if self.counts_by_request_id is not None:
                if not isinstance(parameters, dict):
                    return [{"row_count": 0}]
                return [{"row_count": self.counts_by_request_id.get(str(parameters.get("client_request_id")), 0)}]
            return [{"row_count": self.exact_count}]
        raise AssertionError(f"unexpected SQL: {statement}")


class _Workspace:
    def __init__(self) -> None:
        self.api_client = type(
            "ApiClient",
            (),
            {"do": lambda _self, *_args, **_kwargs: {"id": "resp-ai-gateway-proof"}},
        )()
        self.serving_endpoints = type(
            "ServingEndpoints",
            (),
            {"get": lambda _self, _endpoint: type("Endpoint", (), {"task": "agent/v1/responses"})()},
        )()


class _NoPayloadWorkspace(_Workspace):
    def __init__(self) -> None:
        super().__init__()
        self.api_client = type(
            "ApiClient",
            (),
            {"do": lambda _self, *_args, **_kwargs: {}},
        )()


def _verified_row(*, git_sha: str, verified_at: datetime) -> dict[str, Any]:
    sent_at = verified_at - timedelta(minutes=4)
    return {
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
        now=now,
    )

    assert proof is not None
    assert proof.git_sha == _TEST_GIT_SHA
    assert proof.verified_at == now - timedelta(minutes=5)


def test_latest_verified_proof_requires_matching_endpoint_and_table() -> None:
    now = datetime.now(UTC)
    lakebase = _ProofLakebase([_verified_row(git_sha=_TEST_GIT_SHA, verified_at=now)])

    wrong_endpoint = latest_verified_proof(
        lakebase,
        git_sha=_TEST_GIT_SHA,
        endpoint_name="different-endpoint",
        inference_table=_INFERENCE_TABLE,
        freshness_s=60,
        now=now,
    )
    wrong_table = latest_verified_proof(
        lakebase,
        git_sha=_TEST_GIT_SHA,
        endpoint_name=_ENDPOINT,
        inference_table="mip.audit.different_gateway_table",
        freshness_s=60,
        now=now,
    )

    assert wrong_endpoint is None
    assert wrong_table is None


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
    monkeypatch.setattr(verify_ai_gateway_exact_proof, "get_sql_client", lambda: _ProofSql(exact_count=1))
    monkeypatch.setattr(verify_ai_gateway_exact_proof, "WorkspaceClient", lambda: _Workspace())

    exit_code = verify_ai_gateway_exact_proof.main(
        [
            "send",
            "--wait",
            "--require-verified",
            "--git-sha",
            _TEST_GIT_SHA,
            "--endpoint",
            _ENDPOINT,
            "--inference-table",
            _INFERENCE_TABLE,
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
    monkeypatch.setattr(verify_ai_gateway_exact_proof, "get_sql_client", lambda: _ProofSql(exact_count=0))
    monkeypatch.setattr(verify_ai_gateway_exact_proof, "WorkspaceClient", lambda: _Workspace())

    exit_code = verify_ai_gateway_exact_proof.main(
        [
            "verify-pending",
            "--require-verified",
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
    monkeypatch.setattr(verify_ai_gateway_exact_proof, "get_sql_client", lambda: _ProofSql(exact_count=1))
    monkeypatch.setattr(verify_ai_gateway_exact_proof, "WorkspaceClient", lambda: _Workspace())

    exit_code = verify_ai_gateway_exact_proof.main(
        [
            "verify-pending",
            "--require-verified",
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
    monkeypatch.setattr(
        verify_ai_gateway_exact_proof,
        "get_sql_client",
        lambda: _ProofSql(counts_by_request_id={other_id: 1}),
    )
    monkeypatch.setattr(verify_ai_gateway_exact_proof, "WorkspaceClient", lambda: _Workspace())

    exit_code = verify_ai_gateway_exact_proof.main(
        [
            "verify-pending",
            "--require-verified",
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


def test_wait_for_exact_row_ignores_row_under_a_different_request_id() -> None:
    """A logged inference row under some OTHER client_request_id must not
    verify this pending proof -- the exact-id match is what makes the proof
    trustworthy, so a wrong-id row leaves the proof pending."""
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
    assert lakebase.rows[0]["status"] == "pending"


def test_wait_for_exact_row_timeout_leaves_pending() -> None:
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
    assert lakebase.rows[0]["status"] == "pending"


def test_strict_send_wait_requires_the_sent_proof(monkeypatch, capsys) -> None:
    lakebase = _ProofLakebase(
        [_verified_row(git_sha=_TEST_GIT_SHA, verified_at=datetime.now(UTC) - timedelta(minutes=5))]
    )
    monkeypatch.setattr(verify_ai_gateway_exact_proof, "get_lakebase_client", lambda: lakebase)
    monkeypatch.setattr(
        verify_ai_gateway_exact_proof,
        "get_sql_client",
        lambda: _ProofSql(counts_by_request_id={}),
    )
    monkeypatch.setattr(verify_ai_gateway_exact_proof, "WorkspaceClient", lambda: _Workspace())

    exit_code = verify_ai_gateway_exact_proof.main(
        [
            "send",
            "--wait",
            "--require-verified",
            "--git-sha",
            _TEST_GIT_SHA,
            "--endpoint",
            _ENDPOINT,
            "--inference-table",
            _INFERENCE_TABLE,
            "--timeout-s",
            "0",
            "--interval-s",
            "1",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert lakebase.rows[0]["status"] == "verified"
    assert lakebase.rows[1]["status"] == "pending"
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


def test_no_payload_error_redacts_endpoint_name() -> None:
    lakebase = _ProofLakebase()

    with pytest.raises(RuntimeError) as exc:
        verify_ai_gateway_exact_proof.send_probe(
            lakebase=lakebase,
            workspace=_NoPayloadWorkspace(),
            endpoint=_ENDPOINT,
            inference_table=_INFERENCE_TABLE,
            git_sha=_TEST_GIT_SHA,
        )

    message = str(exc.value)
    assert "Configured AI Gateway endpoint returned no response payload" in message
    assert _ENDPOINT not in message
    assert _INFERENCE_TABLE not in message
    assert lakebase.rows == []


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
