"""The core CLI hands each mode exactly the run's inputs (M2-PR7b review).

``app_core_rollback ensure`` and ``restore`` must pass this run's commit
(``--deployment-source-git-sha``) to the core tools as ``run_git_sha``: an
unverified blue frontend's ``restore_unverified_frontend`` ack covers that
commit, and tier b refuses an empty one (``run_commit_invalid``), so a dropped
wire would make every acked unverified-frontend restore refuse at the EXIT
trap. The shell side of the flag is pinned in
tests/unit/test_core_release_deploy_contract.py; this pins the Python side.
Every value is synthetic.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools.databricks import app_core_rollback as cli
from tools.databricks import core_blue, core_blue_restore

RUN_SHA = "c" * 40
FRONTEND_ACK = "a" * 64
TRANSITION_ACK = "b" * 64


def _argv(action: str, *extra: str) -> list[str]:
    return [
        action,
        "--app-name",
        "mip-app",
        "--scope",
        "mip-app-rollback",
        "--lakebase-instance",
        "instance",
        "--agentic-tier",
        "off",
        "--base-url",
        "https://app.example.invalid",
        "--deployment-lease-id",
        "00000000-0000-4000-8000-000000000000",
        "--deployment-source-git-sha",
        RUN_SHA,
        "--treatment-warehouse-id",
        "warehouse",
        "--restore-unverified-frontend-ack",
        FRONTEND_ACK,
        "--tier-transition-ack",
        TRANSITION_ACK,
        *extra,
    ]


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, Any]]:
    calls: dict[str, dict[str, Any]] = {}

    def recorder(name: str) -> Any:
        def tool(_workspace: Any, **kwargs: Any) -> dict[str, Any]:
            calls[name] = kwargs
            return {"provenance": "captured"}

        return tool

    monkeypatch.setenv("MIP_BEARER_TOKEN", "EXAMPLE-bearer-token")
    monkeypatch.setattr(cli, "_lease", lambda *_a, **_kw: lambda: None)
    monkeypatch.setattr(core_blue, "core_health", lambda *_a, **_kw: lambda *_h: None)
    monkeypatch.setattr(core_blue_restore, "ensure_core_blue", recorder("ensure"))
    monkeypatch.setattr(core_blue_restore, "restore_core_blue", recorder("restore"))
    return calls


@pytest.mark.parametrize(
    ("action", "extra"),
    [("ensure", ()), ("restore", ("--expected-rollback-deployment-id", "deployment-blue"))],
)
def test_ensure_and_restore_carry_the_runs_commit_and_its_acks(
    seen: dict[str, dict[str, Any]], action: str, extra: tuple[str, ...]
) -> None:
    assert cli.main(_argv(action, *extra), workspace_factory=lambda: SimpleNamespace()) == 0

    kwargs = seen[action]
    assert kwargs["run_git_sha"] == RUN_SHA, "the frontend ack covers this run's commit"
    assert kwargs["unverified_frontend_ack"] == FRONTEND_ACK
    assert kwargs["tier_transition_ack"] == TRANSITION_ACK
    assert kwargs["agentic_tier"] == "off" and kwargs["app_name"] == "mip-app"
    if action == "restore":
        assert kwargs["expected_rollback_deployment_id"] == "deployment-blue"


def test_an_ensure_out_env_holds_the_records_bindings(
    seen: dict[str, dict[str, Any]], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "binding_values", lambda record: {"MIP_EXAMPLE_BINDING": "placeholder"})
    out = tmp_path / "binding.env"

    assert cli.main(_argv("ensure", "--out-env", str(out)), workspace_factory=SimpleNamespace) == 0
    assert out.read_text(encoding="utf-8") == "MIP_EXAMPLE_BINDING=placeholder\n"
    assert seen["ensure"]["run_git_sha"] == RUN_SHA
