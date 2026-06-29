from __future__ import annotations

from typing import Any

from tools.databricks import provision_agentic_resources


def test_grant_app_can_query_serving_endpoint_uses_endpoint_id_and_app_sp(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, Any] | None]] = []

    def fake_run(args: list[str], *, input_json: dict[str, Any] | None = None) -> dict[str, Any]:
        calls.append((args, input_json))
        if args == ["apps", "get", "mip-app"]:
            return {"service_principal_client_id": "app-sp-123"}
        if args == ["serving-endpoints", "list"]:
            return [
                {"name": "other", "id": "endpoint-other"},
                {"name": "mas-agent-endpoint", "id": "endpoint-123"},
            ]
        if args == ["serving-endpoints", "update-permissions", "endpoint-123"]:
            assert input_json == {
                "access_control_list": [
                    {
                        "service_principal_name": "app-sp-123",
                        "permission_level": "CAN_QUERY",
                    }
                ]
            }
            return {"ok": True}
        raise AssertionError(f"unexpected databricks call: {args}")

    monkeypatch.setattr(provision_agentic_resources, "_run", fake_run)

    provision_agentic_resources._grant_app_can_query_serving_endpoint(
        endpoint="mas-agent-endpoint",
        app_name="mip-app",
    )

    assert calls[-1][0] == ["serving-endpoints", "update-permissions", "endpoint-123"]


def test_grant_app_can_query_serving_endpoint_skips_builtin_without_endpoint_id(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, Any] | None]] = []

    def fake_run(args: list[str], *, input_json: dict[str, Any] | None = None) -> dict[str, Any]:
        calls.append((args, input_json))
        if args == ["apps", "get", "mip-app"]:
            return {"service_principal_client_id": "app-sp-123"}
        if args == ["serving-endpoints", "list"]:
            return [{"name": "databricks-claude-sonnet-4-5", "id": None}]
        raise AssertionError(f"unexpected databricks call: {args}")

    monkeypatch.setattr(provision_agentic_resources, "_run", fake_run)

    provision_agentic_resources._grant_app_can_query_serving_endpoint(
        endpoint="databricks-claude-sonnet-4-5",
        app_name="mip-app",
    )

    assert all(call[0][:2] != ["serving-endpoints", "update-permissions"] for call in calls)
