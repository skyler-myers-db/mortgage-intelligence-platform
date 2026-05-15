from __future__ import annotations

import json
from datetime import UTC, datetime

from tools.databricks.otlp_customer_retention_gate import main, validate_evidence

NOW = datetime(2026, 5, 15, 12, 30, tzinfo=UTC)
CID = "c864cd1bbbf44779874fdb235ae7c6bf"


def _valid_evidence() -> dict[str, object]:
    return {
        "app": {
            "name": "mip-app",
            "active_deployment_id": "01f14ff2c4cd10b99ebad8f8785c307f",
            "otel_header_resource": "otel_headers",
            "otel_header_secret_ref": "databricks://secrets/mip/otel-headers",
        },
        "health": {"log_export": "otlp"},
        "probe": {
            "correlation_id": CID,
            "sent_at_utc": "2026-05-15T12:00:00Z",
        },
        "collector": {
            "customer_owned": True,
            "owner": "Summit Mortgage security operations",
            "endpoint": "https://logs.summit.example/otlp/v1/logs",
            "retention_days": 400,
            "retention_policy_ref": "SUMMIT-LOGS-RETENTION-2026",
            "acl_proof_ref": "SUMMIT-IAM-OTLP-ACL-2026",
            "query_proof_ref": "SUMMIT-SIEM-QUERY-20260515-001",
            "query_correlation_id": CID,
            "query_observed_at_utc": "2026-05-15T12:01:00Z",
        },
    }


def test_customer_retention_gate_passes_complete_customer_evidence() -> None:
    result = validate_evidence(_valid_evidence(), now=NOW, min_retention_days=365)

    assert result["status"] == "passed"
    assert result["errors"] == []
    assert result["summary"]["correlation_id"] == CID
    assert result["summary"]["retention_days"] == 400


def test_customer_retention_gate_blocks_stdout_only_health() -> None:
    evidence = _valid_evidence()
    evidence["health"] = {"log_export": "stdout-only"}

    result = validate_evidence(evidence, now=NOW)

    assert result["status"] == "blocked"
    assert "health.log_export must be otlp" in result["errors"]


def test_customer_retention_gate_blocks_temporary_collector_endpoint() -> None:
    evidence = _valid_evidence()
    collector = dict(evidence["collector"])  # type: ignore[arg-type]
    collector["endpoint"] = "https://webhook.site/abc123"
    evidence["collector"] = collector

    result = validate_evidence(evidence, now=NOW)

    assert result["status"] == "blocked"
    assert any("temporary proof collector" in error for error in result["errors"])


def test_customer_retention_gate_blocks_missing_retention_and_acl_evidence() -> None:
    evidence = _valid_evidence()
    collector = dict(evidence["collector"])  # type: ignore[arg-type]
    collector.pop("retention_policy_ref")
    collector.pop("acl_proof_ref")
    evidence["collector"] = collector

    result = validate_evidence(evidence, now=NOW)

    assert result["status"] == "blocked"
    assert "collector.retention_policy_ref is required" in result["errors"]
    assert "collector.acl_proof_ref is required" in result["errors"]


def test_customer_retention_gate_blocks_mismatched_collector_correlation_id() -> None:
    evidence = _valid_evidence()
    collector = dict(evidence["collector"])  # type: ignore[arg-type]
    collector["query_correlation_id"] = "11111111111111111111111111111111"
    evidence["collector"] = collector

    result = validate_evidence(evidence, now=NOW)

    assert result["status"] == "blocked"
    assert "collector.query_correlation_id must match probe.correlation_id" in result["errors"]


def test_customer_retention_gate_blocks_plaintext_header_secret() -> None:
    evidence = _valid_evidence()
    app = dict(evidence["app"])  # type: ignore[arg-type]
    app["MIP_OTEL_HEADERS"] = "Authorization=Bearer secret-token-value"
    evidence["app"] = app

    result = validate_evidence(evidence, now=NOW)

    assert result["status"] == "blocked"
    assert any("plaintext collector headers" in error for error in result["errors"])


def test_customer_retention_gate_blocks_stale_probe() -> None:
    evidence = _valid_evidence()
    probe = dict(evidence["probe"])  # type: ignore[arg-type]
    probe["sent_at_utc"] = "2026-05-15T06:00:00Z"
    evidence["probe"] = probe

    result = validate_evidence(evidence, now=NOW, max_age_minutes=120)

    assert result["status"] == "blocked"
    assert any("probe.sent_at_utc must be within" in error for error in result["errors"])


def test_customer_retention_gate_cli_returns_blocked_for_missing_evidence(
    tmp_path,
    capsys,
) -> None:
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps({"health": {"log_export": "stdout-only"}}))

    rc = main([str(evidence_path), "--now-utc", "2026-05-15T12:30:00Z"])

    assert rc == 2
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "blocked"
    assert "app.name is required" in output["errors"]
