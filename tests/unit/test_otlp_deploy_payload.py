import json

import pytest

from tools.databricks.otlp_deploy_payload import build_payload, main


def test_otlp_deploy_payload_contains_full_base_env_and_secret_reference(capsys):
    rc = main(
        [
            "--source-code-path",
            "/Workspace/Users/example/.bundle/mip/dev/files",
            "--endpoint",
            "https://collector.example.com/v1/logs",
        ]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    env = {item["name"]: item for item in payload["env_vars"]}

    assert payload["mode"] == "SNAPSHOT"
    assert env["DATABRICKS_WAREHOUSE_ID"] == {
        "name": "DATABRICKS_WAREHOUSE_ID",
        "value_from": "sql_warehouse",
    }
    assert env["GENIE_SPACE_ID"]["value_from"] == "genie_space"
    assert env["PGHOST"]["value_from"] == "database"
    assert env["LAKEBASE_HOST"]["value_from"] == "database"
    assert env["MIP_LIFECYCLE_SYNC_JOB_ID"]["value_from"] == "lifecycle_sync_job"
    assert env["MIP_FRED_RATES_JOB_ID"]["value_from"] == "fred_rates_job"
    assert env["MIP_SILVER_REFRESH_JOB_ID"]["value_from"] == "silver_refresh_job"
    assert env["MIP_GOLD_REFRESH_JOB_ID"]["value_from"] == "gold_refresh_job"
    assert env["MIP_OTEL_ENDPOINT"]["value"] == "https://collector.example.com/v1/logs"
    assert env["MIP_OTEL_HEADERS"] == {
        "name": "MIP_OTEL_HEADERS",
        "value_from": "otel_headers",
    }


def test_otlp_deploy_payload_never_accepts_credentials_in_endpoint():
    with pytest.raises(SystemExit):
        main(
            [
                "--source-code-path",
                "/Workspace/source",
                "--endpoint",
                "https://user:secret@collector.example.com/v1/logs?token=bad",
            ]
        )


def test_otlp_deploy_payload_requires_https_endpoint():
    with pytest.raises(SystemExit):
        main(
            [
                "--source-code-path",
                "/Workspace/source",
                "--endpoint",
                "http://collector.example.com/v1/logs",
            ]
        )


def test_otlp_deploy_payload_builder_has_no_header_values():
    payload = build_payload(
        source_code_path="/Workspace/source",
        endpoint="https://collector.example.com/v1/logs",
        header_resource="customer_otel_headers",
    )

    text = json.dumps(payload)
    assert "Bearer" not in text
    assert "token" not in text.lower()
    assert {"name": "MIP_OTEL_HEADERS", "value_from": "customer_otel_headers"} in payload[
        "env_vars"
    ]
