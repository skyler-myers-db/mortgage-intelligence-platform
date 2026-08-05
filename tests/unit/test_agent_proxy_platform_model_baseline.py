from types import SimpleNamespace
from unittest.mock import MagicMock

from tools.databricks import verify_agent_proxy_uc_grants as verifier
from tools.databricks.agent_runtime_uc_baseline import _SYSTEM_AI_INHERITED


def test_new_system_owned_kimi_model_uses_exact_platform_inheritance(monkeypatch) -> None:
    full_name = "system.ai.databricks-kimi-k3"
    model = SimpleNamespace(
        full_name=full_name,
        catalog_name="system",
        schema_name="ai",
        name="databricks-kimi-k3",
        owner="System user",
    )
    workspace = MagicMock()
    workspace.registered_models.list.return_value = [model]
    asserted: list[dict[str, object]] = []
    monkeypatch.setattr(
        verifier,
        "_assert_privileges",
        lambda *_args, **kwargs: asserted.append(kwargs),
    )

    verifier._audit_registered_models(
        workspace,
        catalog="mip_pr105_staging",
        principal="proxy-application-id",
        owner_aliases={"proxy-application-id"},
    )

    assert asserted == [
        {
            "securable_type": "function",
            "full_name": full_name,
            "principal": "proxy-application-id",
            "expected": {"EXECUTE"},
            "expected_source_map": {"EXECUTE": set(_SYSTEM_AI_INHERITED)},
        }
    ]
