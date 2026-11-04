"""Every deploy-side runtime-identity tool reads the tier it is handed (M2-PR3, attack O4).

* The lease's writer is the caller's (the tier's lease writer); no ambient
  agent-runtime id ever stands in for it.
* The pre-stop probes take no runtime or verifier in the core tier, skip the
  Supervisor clauses and audit the App alone (its future service principal
  on a first install); the agentic tier still requires both ids.
* The reviewed function verifier proves the App alone in the core tier and
  refuses an agentic consumer there; the agentic tier is unchanged.
* The migrate job's ``--ai-gateway-verifier-absent`` binds no verifier role and
  refuses a real verifier id; it excludes ``--require-ai-gateway-verifier``.
* The App payload of the core tier ships the orchestrator and the AI Gateway
  off; the agentic tier's payload is the one the payload had before, so the
  emergency recipe keeps its exact env-name set.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jobs import lakebase_migrate
from tests.fixtures.prestop_estate import FakeWorkspace, acl_entry
from tests.fixtures.release_sandbox import first_install_estate
from tests.unit.test_app_deployment_lease import _workspace
from tests.unit.test_settings_d0_env_parity import D0_NAMES, _d0_operator_config
from tools.databricks import app_deploy_payload
from tools.databricks import app_deployment_lease as lease
from tools.databricks import deploy_prestop_probes as probes
from tools.databricks import verify_reviewed_function_execute_grants as function_grants
from tools.databricks.prestop_refusals import GENIE_GROUP_GRANT, SUPERVISOR_RETIREMENT

# ----------------------------------------------------------------- the lease


@pytest.mark.parametrize("writer", ["", "   "])
def test_the_lease_never_falls_back_to_an_ambient_runtime_writer(
    monkeypatch: pytest.MonkeyPatch, writer: str
) -> None:
    monkeypatch.setenv("DATABRICKS_AGENT_RUNTIME_CLIENT_ID", "agent-runtime-application-id")
    with pytest.raises(ValueError, match="requires a distinct writer application ID"):
        lease.acquire(_workspace(), app_name="mip-app", source_git_sha="a" * 40, writer_application_id=writer)


def test_the_lease_writer_is_a_required_argument() -> None:
    with pytest.raises(TypeError, match="writer_application_id"):
        lease.acquire(_workspace(), app_name="mip-app", source_git_sha="a" * 40)  # type: ignore[call-arg]


# ------------------------------------------------------------ pre-stop probes

_PROBE_ARGS = [
    "--app-name", "mip-app",
    "--expected-inventory-principal", "deployer@example.invalid",
    "--supervisor-name", "Mortgage Growth Agent",
    "--rollback-scope", "mip-app-rollback",
    "--lakebase-instance", "mip-app-state",
    "--catalog", "mip",
    "--genie-space-name", "Mortgage Lead Intelligence",
    "--rebase-unverified-app", "0",
]  # fmt: skip


@pytest.mark.parametrize(
    "extra",
    [
        [],
        ["--runtime-application-id", "runtime-id"],
        ["--verifier-application-id", "verifier-id"],
        ["--agentic-tier", "on", "--runtime-application-id", "runtime-id"],
    ],
    ids=["neither", "runtime-only", "verifier-only", "explicit-on"],
)
def test_the_agentic_tier_probes_still_require_both_identities(
    extra: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        probes.main([*_PROBE_ARGS, *extra])
    assert exit_info.value.code == 2
    assert "the agentic tier requires --runtime-application-id and --verifier-application-id" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["--runtime-application-id", "--verifier-application-id"])
def test_the_core_tier_probes_refuse_an_agentic_identity(name: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        probes.main([*_PROBE_ARGS, "--agentic-tier", "off", name, "some-id"])
    assert exit_info.value.code == 2
    assert "the core tier deploys no agent runtime or verifier" in capsys.readouterr().err


def _core_verdict(estate: dict[str, Any]) -> probes.BaseVerdict:
    return probes.base_verdict(
        FakeWorkspace(estate),
        app_name="mip-app",
        runtime_application_id=None,
        verifier_application_id=None,
        canonical_name="Mortgage Growth Agent",
        acknowledgement="",
        list_agents=lambda: estate["agents"],
        resolve_groups=lambda *_a, **_k: {},
    )


def test_the_core_tier_skips_the_supervisor_clauses() -> None:
    estate = first_install_estate()
    # A human-created canonical Supervisor: the agentic tier would refuse to retire it.
    estate["agents"] = [
        {
            "supervisor_agent_id": "supervisor-human",
            "display_name": "Mortgage Growth Agent",
            "creator": "operator@example.invalid",
            "endpoint_name": "mas-human",
            "create_time": "2026-01-02T03:04:05.678Z",
        }
    ]
    verdict = _core_verdict(estate)
    assert not [refusal for refusal in verdict.refusals if refusal.code == SUPERVISOR_RETIREMENT]
    assert any("the core tier provisions no Supervisor" in note for note in verdict.notes)


def _unavailable_supervisor_api() -> list[dict[str, Any]]:
    raise RuntimeError("the Supervisor agents API is not enabled in this workspace")


def test_the_core_tier_never_lists_the_supervisor_agents() -> None:
    # The core capability preflight needs no Supervisor API, so the core probes
    # must not depend on it: a failing listing is never read in this tier.
    estate = first_install_estate()
    verdict = probes.base_verdict(
        FakeWorkspace(estate),
        app_name="mip-app",
        runtime_application_id=None,
        verifier_application_id=None,
        canonical_name="Mortgage Growth Agent",
        acknowledgement="",
        list_agents=_unavailable_supervisor_api,
        resolve_groups=lambda *_a, **_k: {},
    )
    # No listing, so the serving clause pins no canonical Supervisor endpoint:
    # a core App may hold access to none.
    assert verdict.refusals == [] and verdict.agents == []
    # The agentic tier still reads it, and an unreadable listing still raises there.
    with pytest.raises(RuntimeError, match="Supervisor agents API"):
        probes.base_verdict(
            FakeWorkspace(estate),
            app_name="mip-app",
            runtime_application_id="runtime-id",
            verifier_application_id="verifier-id",
            canonical_name="Mortgage Growth Agent",
            acknowledgement="",
            list_agents=_unavailable_supervisor_api,
            resolve_groups=lambda *_a, **_k: {},
        )


def test_a_core_first_install_audits_its_future_app_for_users_grants() -> None:
    clean = _core_verdict(first_install_estate())
    assert clean.refusals == []
    assert clean.effective == {"future App SP for 'mip-app'": frozenset({"users"})}

    estate = first_install_estate()
    estate["genie"]["space-other-team"] = [acl_entry("group_name", "users", "CAN_RUN")]
    verdict = _core_verdict(estate)
    assert [refusal.code for refusal in verdict.refusals] == [GENIE_GROUP_GRANT]


# ------------------------------------------------- reviewed function grants


def _effective(principals: set[str]) -> Any:
    def get_effective(_kind: str, _full_name: str, *, principal: str, **_kwargs: Any) -> Any:
        privileges = [SimpleNamespace(privilege="EXECUTE", inherited_from_type="", inherited_from_name="")]
        held = privileges if principal in principals else []
        return SimpleNamespace(
            privilege_assignments=[SimpleNamespace(principal=principal, privileges=held)],
            next_page_token=None,
        )

    return SimpleNamespace(grants=SimpleNamespace(get_effective=get_effective))


def test_the_core_tier_proves_the_app_alone(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(function_grants, "deployment_workspace_client", lambda: _effective({"app-client"}))
    assert function_grants.main(["--catalog", "mip", "--app-application-id", "app-client", "--agentic-tier", "off"]) == 0
    assert "verified three exact effective direct EXECUTE grants" in capsys.readouterr().out


def test_the_agentic_tier_still_proves_all_three_consumers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(function_grants, "deployment_workspace_client", lambda: _effective({"app", "runtime"}))
    with pytest.raises(RuntimeError, match="exact effective EXECUTE postflight failed .* proxy"):
        function_grants.main(
            [
                "--catalog", "mip",
                "--app-application-id", "app",
                "--agent-runtime-application-id", "runtime",
                "--agent-proxy-application-id", "proxy",
            ]
        )  # fmt: skip


@pytest.mark.parametrize(
    "extra",
    [["--agent-runtime-application-id", "runtime"], ["--agent-proxy-application-id", "proxy"]],
)
def test_the_core_tier_verifier_refuses_an_agentic_consumer(extra: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        function_grants.main(["--catalog", "mip", "--app-application-id", "app", "--agentic-tier", "off", *extra])
    assert exit_info.value.code == 2
    with pytest.raises(ValueError, match="exactly one function consumer"):
        function_grants.verify_reviewed_function_execute_grants(
            _effective({"app", "runtime"}), catalog="mip", principals=("app", "runtime"), agentic_tier="off"
        )


# ---------------------------------------------------------- the migrate job


def test_the_migrate_job_takes_exactly_one_verifier_flag(capsys: pytest.CaptureFixture[str]) -> None:
    parser = lakebase_migrate.build_parser()
    args = parser.parse_args(["--ai-gateway-verifier-client-id=00000000PLACEHOLDER", "--ai-gateway-verifier-absent"])
    assert args.ai_gateway_verifier_absent is True and args.require_ai_gateway_verifier is False
    with pytest.raises(SystemExit):
        parser.parse_args(["--require-ai-gateway-verifier", "--ai-gateway-verifier-absent"])
    assert "not allowed with argument" in capsys.readouterr().err


def _migrate_until_roles(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    def roles(_conn: dict[str, Any], **kwargs: Any) -> tuple[str, None]:
        seen.append(kwargs)
        raise RuntimeError("stop after the role preflight")

    monkeypatch.setattr(lakebase_migrate, "_resolve_connection", lambda *_a, **_k: {})
    monkeypatch.setattr(lakebase_migrate, "_preflight_database_roles", roles)
    monkeypatch.delenv("MIP_AI_GATEWAY_VERIFIER_CLIENT_ID", raising=False)
    return seen


@pytest.mark.parametrize("bound", ["00000000PLACEHOLDER", "", "<verifier-client-id>"])
def test_the_core_tier_migrate_binds_no_verifier(monkeypatch: pytest.MonkeyPatch, bound: str) -> None:
    seen = _migrate_until_roles(monkeypatch)
    # A remote job: neither APP_ENV nor the target would relax the agentic path.
    monkeypatch.setenv("APP_ENV", "sandbox")
    monkeypatch.setenv("DATABRICKS_BUNDLE_TARGET", "client")
    with pytest.raises(SystemExit):
        lakebase_migrate.main(ai_gateway_verifier_client_id=bound, ai_gateway_verifier_absent=True)
    assert seen == [
        {
            "app_name": None,
            "ai_gateway_verifier_client_id": None,
            "require_ai_gateway_verifier": False,
            "ai_gateway_verifier_absent": True,
        }
    ]


def test_the_core_tier_migrate_refuses_a_real_verifier_before_any_connection(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen = _migrate_until_roles(monkeypatch)
    monkeypatch.setattr(
        lakebase_migrate, "_resolve_connection", lambda *_a, **_k: pytest.fail("connected before refusing")
    )
    with pytest.raises(SystemExit) as exit_info:
        lakebase_migrate.main(ai_gateway_verifier_client_id="real-verifier-id", ai_gateway_verifier_absent=True)
    assert exit_info.value.code == 2 and seen == []
    assert "verifier identity preflight failed" in capsys.readouterr().err


def test_the_absence_resolver_hands_the_role_preflight_no_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def impl(_conn: dict[str, Any], **kwargs: Any) -> tuple[str, None]:
        captured["verifier"] = kwargs["_resolve_verifier_role_fn"]()
        return "app-role", None

    monkeypatch.setattr(lakebase_migrate, "_preflight_database_roles_impl", impl)
    # Even an ambient verifier id is never resolved under the absence flag.
    monkeypatch.setenv("MIP_AI_GATEWAY_VERIFIER_CLIENT_ID", "ambient-verifier-id")
    assert lakebase_migrate._preflight_database_roles({}, ai_gateway_verifier_absent=True) == ("app-role", None)
    assert captured == {"verifier": None}


# ---------------------------------------------------------------- the payload


def _payload(**kwargs: Any) -> dict[str, str]:
    payload = app_deploy_payload.build_payload(source_code_path="/Workspace/app-src/release", install_class="demo", **kwargs)
    return {item["name"]: item.get("value", item.get("value_from", "")) for item in payload["env_vars"]}  # type: ignore[union-attr]


def test_the_agentic_payload_is_the_payload_without_a_tier(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _d0_operator_config(monkeypatch, tmp_path)
    assert _payload(agentic_tier="on") == _payload()
    # The emergency recipe's payload keeps D0's exact env-name set.
    assert tuple(sorted(_payload())) == D0_NAMES


def test_the_core_payload_ships_the_agentic_surfaces_off(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _d0_operator_config(monkeypatch, tmp_path)
    core = _payload(agentic_tier="off")
    assert set(core) == set(D0_NAMES) | {"MIP_AGENT_ORCHESTRATOR", "MIP_AI_GATEWAY"}
    assert core["MIP_AGENT_ORCHESTRATOR"] == "0" and core["MIP_AI_GATEWAY"] == "0"
    monkeypatch.setenv("MIP_AI_GATEWAY", "off")
    assert _payload(agentic_tier="off")["MIP_AI_GATEWAY"] == "0"


@pytest.mark.parametrize("name", ["MIP_AGENT_ORCHESTRATOR", "MIP_AI_GATEWAY"])
@pytest.mark.parametrize("value", ["1", "true", "auto"])
def test_the_core_payload_refuses_an_agentic_switch_turned_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str, value: str
) -> None:
    _d0_operator_config(monkeypatch, tmp_path)
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=f"{name} is refused for the core tier"):
        _payload(agentic_tier="off")
    # The agentic tier forwards it as before.
    assert _payload(agentic_tier="on")[name] == value


def test_the_payload_cli_takes_the_tier(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _d0_operator_config(monkeypatch, tmp_path)
    argv = ["--source-code-path", "/Workspace/app-src/release", "--install-class", "demo"]
    assert app_deploy_payload.main([*argv, "--agentic-tier", "off"]) == 0
    assert '"name": "MIP_AI_GATEWAY",\n      "value": "0"' in capsys.readouterr().out
    with pytest.raises(SystemExit):
        app_deploy_payload.main([*argv, "--agentic-tier", "core"])
