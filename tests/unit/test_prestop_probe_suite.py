"""The record-dependent probes judge against what the signed record pins (M0-PR5)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tests.fixtures.prestop_estate import (
    APP_CLIENT,
    APP_NAME,
    APP_SCIM,
    BLUE_SUPERVISOR_ENDPOINT,
    CANONICAL,
    DEPLOYER,
    GOVERNED_SPACE,
    LEGACY_GATEWAY,
    FakeAccount,
    FakeWorkspace,
    acl_entry,
    clean_estate,
    rename_record_supervisor,
    signed_contract,
)
from tools.databricks import deploy_prestop_probes as runner
from tools.databricks import prestop_genie_budget, prestop_rollback_record
from tools.databricks import prestop_refusals as codes
from tools.databricks.prestop_genie_grants import genie_acls
from tools.databricks.prestop_probe_suite import (
    ProbeSuiteConfig,
    canonical_supervisor_endpoint,
    suite_refusals,
)
from tools.databricks.serving_endpoint_acl import audit_global_serving_endpoint_access

CONFIG = ProbeSuiteConfig(
    app_name=APP_NAME,
    rollback_scope="mip-app-rollback",
    lakebase_instance="mip-app-state",
    catalog="mip",
    genie_space_name="Mortgage Lead Intelligence",
    canonical_name=CANONICAL,
    rebase_unverified_app=False,
    deployer=DEPLOYER,
    approved_owner_principals="mip-data-owners",
    genie_manager_principals="",
    metastore_owner=DEPLOYER,
)


def _suite(
    monkeypatch: pytest.MonkeyPatch, estate: dict[str, Any], *, app: bool = True
) -> tuple[list[str], list[str], list[str]]:
    asked: list[str] = []
    record = estate["record"]
    monkeypatch.setattr(
        prestop_rollback_record, "ASSERT_SIGNED", lambda *_a, **_kw: signed_contract(record)
    )

    def ask(client: Any, _question: str) -> Any:
        asked.append(client.space_id)
        return SimpleNamespace(genie_status="COMPLETED", answer_text="12,345 borrowers")

    monkeypatch.setattr(prestop_genie_budget, "ASSERT", ask)
    workspace = FakeWorkspace(estate)
    refusals, notes = suite_refusals(
        workspace,
        config=CONFIG,
        app_client_id=APP_CLIENT if app else "",
        app_scim_id=APP_SCIM if app else "",
        app_groups=frozenset({"users"}),
        agents=estate["agents"],
        acls=genie_acls(workspace),
        journal_state=lambda: "absent",
        account_factory=lambda: FakeAccount(estate),
        token_provider=lambda: "token-PLACEHOLDER",
    )
    return [refusal.code for refusal in refusals], notes, asked


def test_a_clean_install_passes_every_suite_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    found, notes, asked = _suite(monkeypatch, clean_estate())

    assert found == []
    assert asked == [GOVERNED_SPACE], "the Genie turn asks the record-pinned space"
    assert len(notes) == 5


def test_the_record_pins_the_governed_space_the_owner_equivalent_probe_audits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    estate = clean_estate()
    estate["genie"]["space-unpinned"] = [acl_entry("user_name", "steward@example.invalid", "CAN_MANAGE")]
    assert _suite(monkeypatch, estate)[0] == [], "only the pinned space is the governed one"

    estate["record"]["genie_space_id"] = "space-unpinned"
    assert _suite(monkeypatch, estate)[0] == [codes.OWNER_EQUIVALENT]


def test_a_legacy_record_pins_mip_agent_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    estate = clean_estate()
    estate["serving"][LEGACY_GATEWAY] = {
        "id": "endpoint-legacy",
        "acl": [acl_entry("service_principal_name", APP_CLIENT, "CAN_QUERY")],
    }
    assert _suite(monkeypatch, estate)[0] == [codes.SERVING_ENDPOINT]

    estate["record"]["gateway_endpoint"] = LEGACY_GATEWAY
    estate["serving"].pop("mip-agent-gateway-blue")
    assert _suite(monkeypatch, estate)[0] == []


def test_query_on_the_record_supervisor_refuses_once_it_leaves_the_canonical_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_query = [acl_entry("service_principal_name", APP_CLIENT, "CAN_QUERY")]
    estate = clean_estate()
    estate["serving"][BLUE_SUPERVISOR_ENDPOINT]["acl"] = app_query
    assert _suite(monkeypatch, estate)[0] == [], "the canonical one is the one cutover replaces"

    estate = rename_record_supervisor(clean_estate())
    estate["serving"][BLUE_SUPERVISOR_ENDPOINT]["acl"] = app_query
    assert estate["record"]["supervisor_endpoint"] == BLUE_SUPERVISOR_ENDPOINT

    found, _notes, _asked = _suite(monkeypatch, estate)

    assert found == [codes.SERVING_ENDPOINT]
    # Parity: the post-stop App audit reviews the record's Gateway and the
    # canonical Supervisor (here without App access), and refuses the same.
    with pytest.raises(RuntimeError, match=f"unrelated serving endpoint '{BLUE_SUPERVISOR_ENDPOINT}'"):
        audit_global_serving_endpoint_access(
            FakeWorkspace(estate),
            app_name=APP_NAME,
            reviewed_endpoint_names=[estate["record"]["gateway_endpoint"]],
            service_principal=APP_CLIENT,
            expected_permission_level="CAN_QUERY",
            service_principal_id=APP_SCIM,
            effective_group_names={"users"},
            legacy_pinned_endpoint_names=[estate["record"]["gateway_endpoint"]],
        )


def test_without_an_app_only_the_owner_equivalent_probe_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    found, notes, asked = _suite(monkeypatch, clean_estate(), app=False)

    assert found == [] and asked == []
    assert any("no App exists yet" in note for note in notes)


def test_the_canonical_supervisor_endpoint_needs_exactly_one_canonical_supervisor() -> None:
    agents = clean_estate()["agents"]
    assert canonical_supervisor_endpoint(agents, CANONICAL) == BLUE_SUPERVISOR_ENDPOINT
    assert canonical_supervisor_endpoint([*agents, *agents], CANONICAL) == ""
    assert canonical_supervisor_endpoint([], CANONICAL) == ""


def test_a_read_that_exits_or_raises_is_a_probe_error_never_a_pass() -> None:
    def exits(*_args: Any, **_kwargs: Any) -> Any:
        raise SystemExit("Cannot resolve effective group memberships: a SCIM group has no id")

    estate = clean_estate()
    refusals, notes = runner.all_refusals(
        FakeWorkspace(estate),
        config=CONFIG,
        runtime_application_id="runtime-client-EXAMPLE",
        verifier_application_id="verifier-client-EXAMPLE",
        acknowledgement="",
        list_agents=lambda: estate["agents"],
        resolve_groups=exits,
    )

    assert [refusal.code for refusal in refusals] == [codes.PROBE_ERROR]
    assert "SystemExit: Cannot resolve effective group memberships" in refusals[0].message
    assert notes == []


def test_refusal_codes_are_closed() -> None:
    with pytest.raises(ValueError, match="unreviewed"):
        codes.PrestopRefusal("made_up", "message")
    with pytest.raises(ValueError, match="needs a message"):
        codes.PrestopRefusal(codes.GENIE_BUDGET, " ")


def _without_record(estate: dict[str, Any], titles: dict[str, str]) -> dict[str, Any]:
    # The one-time unsigned rebase: no record, so no Gateway the App may query yet.
    estate["genie_titles"] = titles
    estate["serving"]["mip-agent-gateway-blue"]["acl"] = []
    return estate


def _rebase_suite(
    monkeypatch: pytest.MonkeyPatch, estate: dict[str, Any]
) -> tuple[list[str], list[str], list[str]]:
    asked: list[str] = []
    monkeypatch.setattr(prestop_rollback_record, "ASSERT_ABSENT", lambda *_a, **_kw: None)

    def ask(client: Any, _question: str) -> Any:
        asked.append(client.space_id)
        return SimpleNamespace(genie_status="COMPLETED", answer_text="12,345 borrowers")

    monkeypatch.setattr(prestop_genie_budget, "ASSERT", ask)
    workspace = FakeWorkspace(estate)
    refusals, notes = suite_refusals(
        workspace,
        config=ProbeSuiteConfig(**{**CONFIG.__dict__, "rebase_unverified_app": True}),
        app_client_id=APP_CLIENT,
        app_scim_id=APP_SCIM,
        app_groups=frozenset({"users"}),
        agents=estate["agents"],
        acls=genie_acls(workspace),
        journal_state=lambda: "absent",
        account_factory=lambda: FakeAccount(estate),
        token_provider=lambda: "token-PLACEHOLDER",
    )
    return [refusal.message for refusal in refusals], notes, asked


def test_without_a_record_the_space_with_the_governed_title_is_audited_and_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    estate = _without_record(clean_estate(), {GOVERNED_SPACE: CONFIG.genie_space_name})
    estate["genie"][GOVERNED_SPACE].append(
        acl_entry("service_principal_name", "foreign-app-sp-EXAMPLE", "CAN_MANAGE")
    )

    refusals, notes, asked = _rebase_suite(monkeypatch, estate)

    assert asked == [GOVERNED_SPACE]
    assert f"the governed Genie space is {GOVERNED_SPACE} by its title" in " ".join(notes)
    assert len(refusals) == 1 and "'foreign-app-sp-EXAMPLE' holds CAN_MANAGE" in refusals[0]


def test_an_ambiguous_governed_title_refuses_as_the_provisioner_would(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    estate = clean_estate()
    estate["genie"]["space-copy"] = []
    _without_record(
        estate, {GOVERNED_SPACE: CONFIG.genie_space_name, "space-copy": CONFIG.genie_space_name}
    )

    refusals, _notes, asked = _rebase_suite(monkeypatch, estate)

    assert asked == []
    assert len(refusals) == 1 and "refusing ambiguous Genie binding: found 2 spaces" in refusals[0]


def test_without_a_record_or_a_titled_space_there_is_no_governed_space(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refusals, notes, asked = _rebase_suite(monkeypatch, _without_record(clean_estate(), {}))

    assert refusals == [] and asked == []
    assert "no Genie space has the governed title yet; the provisioner creates it" in notes
