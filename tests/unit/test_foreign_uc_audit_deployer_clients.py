"""The metastore-wide foreign-UC audit authenticates through the cached deployer binding.

``audit_agent_runtime_foreign_uc_access`` fans its catalog inspection out over
eight workers and runs several times per deploy, and both dual-authority
verifiers run it twice. Under a ``github-oidc`` deployer, a bare
``WorkspaceClient()`` resolves the SDK's native provider, which fetches a GitHub
ID token and performs a token exchange on every request. These tests pin that
each of those admin clients is built from ``deployment_workspace_client``, so N
audit requests cost one GitHub fetch and one exchange, while the PAT binding and
the audit CLI's 120 s read timeout are unchanged.
"""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.fixtures.github_oidc import (
    DEPLOYER_CLIENT_ID,
    WORKSPACE_HOST,
    GitHubOidcDeployer,
    install_github_oidc_deployer,
    restored_environ,
)
from tests.unit import test_verify_agent_proxy_uc_boundary_dual_authority as proxy_tests
from tests.unit import test_verify_agent_runtime_uc_boundary_dual_authority as runtime_tests
from tools.databricks import audit_agent_runtime_foreign_uc_access as auditor
from tools.databricks import verify_agent_proxy_uc_boundary_dual_authority as proxy_dual
from tools.databricks import verify_agent_runtime_uc_boundary_dual_authority as runtime_dual
from tools.databricks.github_oidc_auth import CachedGitHubOidcCredentials

_REQUESTS_PER_AUDIT = 32
_DEPLOYER_PROFILE_BUILD = {
    "profile": "DEFAULT",
    "host": WORKSPACE_HOST,
    "client_id": DEPLOYER_CLIENT_ID,
    "auth_type": "github-oidc",
}


@pytest.fixture
def deployer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[GitHubOidcDeployer]:
    # The dual-authority binds write os.environ directly; restore it afterwards.
    with restored_environ():
        yield install_github_oidc_deployer(monkeypatch, tmp_path)


def _fan_out(workspace: Any) -> None:
    """Authenticate the way inspect_catalog's eight workers do."""

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _i: workspace.config.authenticate(), range(_REQUESTS_PER_AUDIT)))


def _recording_client_factory(real: Any, built: list[dict[str, Any]], target: object) -> Any:
    """The first build is the real admin client; any later one is the target client."""

    def build(**kwargs: Any) -> Any:
        built.append(dict(kwargs))
        return real(**kwargs) if len(built) == 1 else target

    return build


def test_audit_cli_makes_one_exchange_for_every_worker_request(
    monkeypatch: pytest.MonkeyPatch,
    deployer: GitHubOidcDeployer,
) -> None:
    audited: list[Any] = []

    def audit(workspace: Any, **_kwargs: Any) -> None:
        audited.append(workspace)
        for _ in range(3):
            _fan_out(workspace)

    monkeypatch.setattr(auditor, "install_probe_deadlines", lambda **_kwargs: None)
    monkeypatch.setattr(auditor, "audit_foreign_uc_access", audit)

    assert auditor.main(["--application-id", "runtime", "--expected-inventory-principal", "p"]) == 0

    (workspace,) = audited
    assert workspace.config.auth_type == "github-oidc"
    assert workspace.config.client_id == DEPLOYER_CLIENT_ID
    assert workspace.config.http_timeout_seconds == 120
    assert len(deployer.http.gets) == len(deployer.http.posts) == 1


def test_audit_cli_keeps_the_pat_binding_and_its_read_timeout(
    monkeypatch: pytest.MonkeyPatch,
    deployer: GitHubOidcDeployer,
) -> None:
    for name in ("DATABRICKS_CONFIG_PROFILE", "MIP_DEPLOYER_DATABRICKS_PROFILE"):
        monkeypatch.delenv(name)
    monkeypatch.setenv("MIP_DEPLOYER_DATABRICKS_HOST", WORKSPACE_HOST)
    monkeypatch.setenv("MIP_DEPLOYER_DATABRICKS_TOKEN", "operator-pat")
    audited: list[Any] = []
    monkeypatch.setattr(auditor, "install_probe_deadlines", lambda **_kwargs: None)
    monkeypatch.setattr(
        auditor, "audit_foreign_uc_access", lambda workspace, **_kwargs: audited.append(workspace)
    )

    assert auditor.main(["--application-id", "runtime", "--expected-inventory-principal", "p"]) == 0

    (workspace,) = audited
    assert (workspace.config.auth_type, workspace.config.token) == ("pat", "operator-pat")
    assert workspace.config.http_timeout_seconds == 120
    assert deployer.http.gets == [] and deployer.http.posts == []


def test_proxy_dual_authority_admin_makes_one_exchange_across_both_audits(
    monkeypatch: pytest.MonkeyPatch,
    deployer: GitHubOidcDeployer,
) -> None:
    built: list[dict[str, Any]] = []
    proxy = SimpleNamespace(name="proxy")
    monkeypatch.setattr(
        proxy_dual,
        "WorkspaceClient",
        _recording_client_factory(proxy_dual.WorkspaceClient, built, proxy),
    )
    monkeypatch.setattr(
        proxy_dual,
        "_reviewed_workspace_capability_groups",
        lambda *_args, **_kwargs: proxy_tests._attestation(),
    )
    monkeypatch.setattr(proxy_dual, "account_client_from_env", lambda: object())
    audited: list[Any] = []

    def audit(workspace: Any, **_kwargs: Any) -> Any:
        audited.append(workspace)
        _fan_out(workspace)
        return proxy_tests._proof()

    monkeypatch.setattr(proxy_dual, "audit_foreign_uc_access", audit)
    verified: list[Any] = []
    monkeypatch.setattr(
        proxy_dual,
        "verify_effective_agent_proxy_uc_boundary",
        lambda workspace, **_kwargs: verified.append(workspace),
    )
    monkeypatch.setenv("DATABRICKS_AGENT_PROXY_CLIENT_ID", proxy_tests._APPLICATION_ID)
    monkeypatch.setenv("DATABRICKS_AGENT_PROXY_CLIENT_SECRET", "proxy-secret")

    assert proxy_dual.main(proxy_tests._args()) == 0

    assert len(audited) == 2 and audited[0] is audited[1]
    assert verified == [proxy]
    assert isinstance(built[0].pop("credentials_strategy"), CachedGitHubOidcCredentials)
    assert built == [_DEPLOYER_PROFILE_BUILD, {}]
    assert len(deployer.http.gets) == len(deployer.http.posts) == 1
    assert {post["client_id"] for post in deployer.http.posts} == {DEPLOYER_CLIENT_ID}


def test_runtime_dual_authority_admin_makes_one_exchange_across_both_audits(
    monkeypatch: pytest.MonkeyPatch,
    deployer: GitHubOidcDeployer,
) -> None:
    built: list[dict[str, Any]] = []
    runtime = SimpleNamespace(name="runtime")
    monkeypatch.setattr(
        runtime_dual,
        "WorkspaceClient",
        _recording_client_factory(runtime_dual.WorkspaceClient, built, runtime),
    )
    monkeypatch.setattr(runtime_dual, "MlflowClient", lambda **_kwargs: object())
    monkeypatch.setattr(
        runtime_dual,
        "delta_version_resolver",
        lambda _workspace, **_kwargs: (lambda _table_name: "1"),
    )
    monkeypatch.setattr(
        runtime_dual,
        "audit_gateway_model_lifecycle",
        lambda *_args, **_kwargs: runtime_tests._lifecycle_proof(),
    )
    monkeypatch.setattr(runtime_dual, "account_client_from_env", lambda: object())
    audited: list[Any] = []

    def audit(workspace: Any, **_kwargs: Any) -> Any:
        audited.append(workspace)
        _fan_out(workspace)
        return runtime_tests._proof()

    monkeypatch.setattr(runtime_dual, "audit_foreign_uc_access", audit)
    verified: list[Any] = []
    monkeypatch.setattr(
        runtime_dual,
        "verify_effective_uc_boundary",
        lambda workspace, **_kwargs: verified.append(workspace),
    )
    monkeypatch.setenv("DATABRICKS_AGENT_RUNTIME_CLIENT_ID", runtime_tests.APPLICATION_ID)
    monkeypatch.setenv("DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET", "runtime-secret")

    assert runtime_dual.main(runtime_tests._args()) == 0

    assert len(audited) == 2 and audited[0] is audited[1]
    assert verified == [runtime]
    assert isinstance(built[0].pop("credentials_strategy"), CachedGitHubOidcCredentials)
    assert built == [_DEPLOYER_PROFILE_BUILD, {}]
    assert len(deployer.http.gets) == len(deployer.http.posts) == 1
    assert {post["client_id"] for post in deployer.http.posts} == {DEPLOYER_CLIENT_ID}
