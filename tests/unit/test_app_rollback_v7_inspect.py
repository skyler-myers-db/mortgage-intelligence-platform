"""``app_deployment_rollback inspect`` on a v7 record (M2-PR7): each tier's own proof.

A core record is verified with the App identity and exactly its core bindings
and never asks the Gateway; an agentic record takes v6 inspect's own Gateway
resource proof, with no candidate owner (the signed payload's, else the
authenticated legacy owner). The sandbox is tests/fixtures/adopt_live_world.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.fixtures import rollback_records as rr
from tests.fixtures.adopt_live_world import World, make_repo
from tools.databricks import app_rollback_record_contract as contract


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    rr.signing_env(monkeypatch)
    monkeypatch.setattr(contract, "assert_owned_app_rollback_scope", lambda *_a, **_kw: None)


@pytest.fixture
def world(tmp_path: Path) -> World:
    repo, main, side = make_repo(tmp_path)
    return World(repo=repo, main=main, side=side)


def test_inspect_verifies_the_adopted_record_without_the_gateway(
    world: World, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tools.databricks import app_deployment_rollback as rollback
    from tools.databricks import app_deployment_rollback_cli as cli

    world.apply()
    monkeypatch.setattr(cli, "WorkspaceClient", lambda: world)
    monkeypatch.setenv("MIP_TEST_TOKEN", "token")
    monkeypatch.setattr(
        rollback,
        "_stored_resource_proof",
        lambda *_a, **_kw: pytest.fail("core never asks the Gateway"),
    )
    out = tmp_path / "blue.env"

    argv = [
        "inspect",
        "--app-name",
        rr.APP_NAME,
        "--scope",
        rr.SCOPE,
        "--base-url",
        "https://x.example.invalid",
    ]
    assert cli.main([*argv, "--token-env", "MIP_TEST_TOKEN", "--out-env", str(out)]) == 0
    values = dict(line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines())
    assert (
        values["MIP_APP_ROLLBACK_RECORD_VERSION"] == "7"
        and values["MIP_APP_ROLLBACK_TIER"] == "core"
    )
    assert values["MIP_APP_ROLLBACK_PROVENANCE"] == "adopted"
    assert world.apps.mutations == []


def _inspect_argv(out: Path) -> list[str]:
    return [
        "inspect",
        "--app-name",
        rr.APP_NAME,
        "--scope",
        rr.SCOPE,
        "--base-url",
        "https://x.example.invalid",
        "--token-env",
        "MIP_TEST_TOKEN",
        "--out-env",
        str(out),
    ]


@pytest.mark.parametrize("owner", ("payload-owner", None))
def test_a_v7_agentic_inspect_requires_v6_inspects_own_gateway_proof(
    world: World, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, owner: str | None
) -> None:
    """No candidate owner: the payload's, else authenticated legacy-style, exactly as v6 inspect."""

    from tools.databricks import app_deployment_rollback as rollback
    from tools.databricks import app_deployment_rollback_cli as cli

    env = {"MIP_REVIEWED_FUNCTION_OWNER": owner} if owner else None
    contract.save_versioned_record(world, scope=rr.SCOPE, record=rr.v7_agentic_record(env))
    monkeypatch.setattr(cli, "WorkspaceClient", lambda: world)
    monkeypatch.setenv("MIP_TEST_TOKEN", "token")
    calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        rollback,
        "authenticated_reviewed_function_owner",
        lambda *_a, **_kw: calls.append(("authenticate", None)) or "deployer-owner",
    )

    live = {"gateway_endpoint": rr.GATEWAY_RESOURCES["gateway_endpoint"]}

    def exact(_workspace: Any, **kwargs: Any) -> str:
        calls.append(("exact", (kwargs["reviewed_function_owner"], kwargs["expected"])))
        if kwargs["expected"]["gateway_endpoint"] != live["gateway_endpoint"]:
            raise RuntimeError("does not match its live exact Gateway resource proof")
        return "proven"

    monkeypatch.setattr(rollback, "resolve_exact_resource_proof", exact)
    out = tmp_path / "blue.env"

    assert cli.main(_inspect_argv(out)) == 0
    record = world.record()
    assert record is not None
    expected_calls = [("exact", (owner, record["gateway_resources"]))]
    if owner is None:
        expected_calls = [
            ("authenticate", None),
            ("exact", ("deployer-owner", record["gateway_resources"])),
        ]
    assert calls == expected_calls, "the Gateway proof ran, with v6 inspect's owner"
    values = dict(line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines())
    assert values["MIP_APP_ROLLBACK_TIER"] == "agentic"

    live["gateway_endpoint"] = "a-tampered-gateway"
    with pytest.raises(RuntimeError, match="live exact Gateway resource proof"):
        cli.main(_inspect_argv(tmp_path / "tampered.env"))
    assert not (tmp_path / "tampered.env").exists() and world.apps.mutations == []
