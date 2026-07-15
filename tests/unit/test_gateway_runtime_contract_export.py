from __future__ import annotations

import io
from types import SimpleNamespace

import pytest

from backend.agents.gateway_contract import (
    DEFAULT_GATEWAY_AGENT_MODEL,
    DEFAULT_GATEWAY_ENDPOINT,
    DEFAULT_GATEWAY_INFERENCE_TABLE,
    GATEWAY_PROXY_SOURCE_HASH_TAG,
    GATEWAY_UPSTREAM_TAG,
    gateway_proxy_source_hash,
    gateway_runtime_binding_hash,
)
from tools import verify_deployed_app_contract as deployed_contract
from tools.databricks import export_gateway_runtime_contract as export_contract

_SUPERVISOR_ID = "supervisor-123"
_UPSTREAM = "mas-supervisor-endpoint"


def _endpoint_details(
    *,
    pending: object | None = None,
    inference_prefix: str = "mip_agent_gateway_growth_agent",
    upstream: str = _UPSTREAM,
) -> object:
    source_hash = gateway_proxy_source_hash(upstream_endpoint=_UPSTREAM)
    return SimpleNamespace(
        pending_config=pending,
        state=SimpleNamespace(ready="READY"),
        task="agent/v1/responses",
        config=SimpleNamespace(
            served_entities=[
                SimpleNamespace(
                    entity_name=DEFAULT_GATEWAY_AGENT_MODEL,
                    entity_version="7",
                    environment_vars={"MIP_UPSTREAM_SUPERVISOR_ENDPOINT": upstream},
                )
            ]
        ),
        tags=[
            SimpleNamespace(key=GATEWAY_PROXY_SOURCE_HASH_TAG, value=source_hash),
            SimpleNamespace(key=GATEWAY_UPSTREAM_TAG, value=_UPSTREAM),
        ],
        ai_gateway=SimpleNamespace(
            inference_table_config=SimpleNamespace(
                enabled=True,
                catalog_name="mip",
                schema_name="audit",
                table_name_prefix=inference_prefix,
            )
        ),
    )


class _ApiClient:
    def __init__(self, supervisors: list[dict[str, str]]) -> None:
        self.supervisors = supervisors

    def do(self, method: str, path: str) -> object:
        assert (method, path) == ("GET", "/api/2.1/supervisor-agents")
        return {"supervisor_agents": self.supervisors}


def _workspace(
    *,
    supervisors: list[dict[str, str]] | None = None,
    details: object | None = None,
) -> object:
    rows = supervisors
    if rows is None:
        rows = [
            {
                "display_name": "Mortgage Growth Agent",
                "supervisor_agent_id": _SUPERVISOR_ID,
                "endpoint_name": _UPSTREAM,
            }
        ]

    class _ServingEndpoints:
        def get(self, endpoint: str) -> object:
            assert endpoint == DEFAULT_GATEWAY_ENDPOINT
            return details or _endpoint_details()

    return SimpleNamespace(
        api_client=_ApiClient(rows),
        serving_endpoints=_ServingEndpoints(),
    )


def _model_registry(*, source_hash: str | None = None, upstream: str = _UPSTREAM) -> object:
    reviewed_hash = source_hash or gateway_proxy_source_hash(upstream_endpoint=_UPSTREAM)
    return SimpleNamespace(
        get_model_version=lambda name, version: SimpleNamespace(
            name=name,
            version=version,
            tags={
                GATEWAY_PROXY_SOURCE_HASH_TAG: reviewed_hash,
                GATEWAY_UPSTREAM_TAG: upstream,
            },
        )
    )


def test_resolve_contract_exports_exact_source_bound_runtime() -> None:
    contract = export_contract.resolve_contract(
        _workspace(),
        supervisor_name="Mortgage Growth Agent",
        model_registry=_model_registry(),
    )

    expected_binding = gateway_runtime_binding_hash(
        endpoint=DEFAULT_GATEWAY_ENDPOINT,
        supervisor_id=_SUPERVISOR_ID,
        upstream_endpoint=_UPSTREAM,
        model_name=DEFAULT_GATEWAY_AGENT_MODEL,
        model_version=7,
        inference_table=DEFAULT_GATEWAY_INFERENCE_TABLE,
    )
    assert contract == {
        "MIP_AGENT_SERVING_ENDPOINT": DEFAULT_GATEWAY_ENDPOINT,
        "MIP_AGENT_SUPERVISOR_ENDPOINT": _UPSTREAM,
        "MIP_AGENT_SUPERVISOR_ID": _SUPERVISOR_ID,
        "MIP_AI_GATEWAY_ENDPOINT": DEFAULT_GATEWAY_ENDPOINT,
        "MIP_AI_GATEWAY_INFERENCE_TABLE": DEFAULT_GATEWAY_INFERENCE_TABLE,
        "MIP_AI_GATEWAY_AGENT_MODEL": DEFAULT_GATEWAY_AGENT_MODEL,
        "MIP_AI_GATEWAY_AGENT_MODEL_VERSION": "7",
        "MIP_EXPECTED_AGENT_GATEWAY_BINDING_SHA256": expected_binding,
    }


def test_resolve_contract_rejects_rogue_served_model_version_before_export() -> None:
    with pytest.raises(RuntimeError, match="Model version tags do not bind"):
        export_contract.resolve_contract(
            _workspace(),
            supervisor_name="Mortgage Growth Agent",
            model_registry=_model_registry(source_hash="b" * 64),
        )


@pytest.mark.parametrize("count", [0, 2], ids=["missing", "duplicate"])
def test_resolve_contract_requires_exactly_one_named_supervisor(count: int) -> None:
    supervisors = [
        {
            "display_name": "Mortgage Growth Agent",
            "supervisor_agent_id": f"supervisor-{index}",
            "endpoint_name": f"endpoint-{index}",
        }
        for index in range(count)
    ]

    with pytest.raises(RuntimeError, match=f"found {count}"):
        export_contract.resolve_contract(
            _workspace(supervisors=supervisors),
            supervisor_name="Mortgage Growth Agent",
        )


@pytest.mark.parametrize(
    ("details", "message"),
    [
        (_endpoint_details(pending=SimpleNamespace()), "pending config update"),
        (_endpoint_details(inference_prefix="wrong_prefix"), "inference-table configuration"),
        (_endpoint_details(upstream="wrong-upstream"), "upstream Supervisor binding"),
    ],
    ids=["pending", "wrong-inference-table", "wrong-upstream"],
)
def test_resolve_contract_rejects_gateway_binding_drift(
    details: object,
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        export_contract.resolve_contract(
            _workspace(details=details),
            supervisor_name="Mortgage Growth Agent",
        )


def test_export_main_appends_exact_contract_to_github_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    github_env = tmp_path / "github-env"
    monkeypatch.setattr(export_contract, "WorkspaceClient", lambda: _workspace())
    monkeypatch.setattr(export_contract, "MlflowClient", lambda **_kwargs: _model_registry())

    assert export_contract.main(["--github-env", str(github_env)]) == 0

    rows = github_env.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 8
    assert f"MIP_AGENT_SERVING_ENDPOINT={DEFAULT_GATEWAY_ENDPOINT}" in rows
    assert f"MIP_AGENT_SUPERVISOR_ID={_SUPERVISOR_ID}" in rows
    assert "MIP_AI_GATEWAY_AGENT_MODEL_VERSION=7" in rows
    assert not any("SECRET" in row or "TOKEN" in row for row in rows)


def _health_payload(*, git_sha: str = "abc123", binding: str = "binding-123") -> io.BytesIO:
    return io.BytesIO(
        ('{"git_sha":"' + git_sha + '","agent_gateway_binding_sha256":"' + binding + '"}').encode()
    )


def test_verify_deployed_contract_uses_authenticated_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _urlopen(request: object, *, timeout: int) -> io.BytesIO:
        captured.update(request=request, timeout=timeout)
        return _health_payload()

    monkeypatch.setattr(deployed_contract.urllib.request, "urlopen", _urlopen)

    deployed_contract.verify(
        base_url="https://mip-app.example/",
        bearer_token="short-lived-bearer",
        git_sha="abc123",
        gateway_binding_sha256="binding-123",
    )

    request = captured["request"]
    assert request.full_url == "https://mip-app.example/api/health"
    assert request.headers["Authorization"] == "Bearer short-lived-bearer"
    assert captured["timeout"] == 30


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (_health_payload(git_sha="wrong"), "git_sha"),
        (_health_payload(binding="wrong"), "Gateway binding"),
    ],
    ids=["wrong-sha", "wrong-binding"],
)
def test_verify_deployed_contract_rejects_health_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    payload: io.BytesIO,
    message: str,
) -> None:
    monkeypatch.setattr(
        deployed_contract.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: payload,
    )

    with pytest.raises(RuntimeError, match=message):
        deployed_contract.verify(
            base_url="https://mip-app.example",
            bearer_token="short-lived-bearer",
            git_sha="abc123",
            gateway_binding_sha256="binding-123",
        )


def test_verify_deployed_contract_cli_requires_token_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MIP_TEST_BEARER", raising=False)

    with pytest.raises(SystemExit) as exc:
        deployed_contract.main(
            [
                "--base-url",
                "https://mip-app.example",
                "--token-env",
                "MIP_TEST_BEARER",
                "--git-sha",
                "abc123",
                "--gateway-binding-sha256",
                "binding-123",
            ]
        )

    assert exc.value.code == 2
