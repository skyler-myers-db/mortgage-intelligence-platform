"""The pre-stop Genie clause and the post-stop Genie audits judge an exposure alike (M2-PR4).

For each identity (the App and the agent-runtime) and each accepted-exposure
case, the pre-stop clause (``deploy_prestop_probes.base_verdict`` with its
``PrestopExposure``) refuses a group grant exactly when the post-stop audit
(``agent_runtime_access.audit_global_genie_access``) does, because both build
the tuple from the same ACL entry (``estate.genie_exposure``) and ask the same
identity's matcher. The verifier has no matcher: a grant reaching it refuses
whatever the profile accepts.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tests.fixtures.prestop_estate import (
    APP_CLIENT,
    APP_NAME,
    APP_SCIM,
    CANONICAL,
    GOVERNED_SPACE,
    RUNTIME_CLIENT,
    VERIFIER_CLIENT,
    FakeWorkspace,
    acl_entry,
    clean_estate,
)
from tools.clients.json_fields import ExposureEntry
from tools.databricks import agent_proxy_capability_denial as denial
from tools.databricks import deploy_prestop_probes as runner
from tools.databricks.agent_runtime_access import (
    audit_global_genie_access,
    audit_global_no_genie_access,
)
from tools.databricks.estate.accepted_exposure import ExposureRefusal, build_policy, load_policy
from tools.databricks.estate.genie_exposure import entry_exposures, entry_levels
from tools.databricks.m2m_access_policy import resolve_effective_groups
from tools.databricks.prestop_accepted_exposure import PrestopExposure, load_prestop_exposure
from tools.databricks.prestop_refusals import ACCEPTED_EXPOSURE, GENIE_GROUP_GRANT

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
OTHER = "space-other-team"
OTHER_TITLE = "Other Team Space"
GOVERNED_TITLE = "Mortgage Lead Intelligence"
WORKSPACE = "4200000001"
PARENT = "/directories/77"
SCIM = {"app": APP_SCIM, "runtime": "runtime-scim"}
CLIENT = {"app": APP_CLIENT, "runtime": RUNTIME_CLIENT}
LABEL = {"app": f"App SP {APP_CLIENT}", "runtime": f"agent-runtime SP {RUNTIME_CLIENT}"}


def _estate(identity: str, *, level: str = "CAN_VIEW", group: str | None = None) -> dict[str, Any]:
    estate = clean_estate()
    readers = group or f"readers-{identity}"
    estate["principals"][readers] = {"kind": "group", "id": f"group-{readers}", "members": [SCIM[identity]]}
    estate["genie"][OTHER] = [acl_entry("group_name", readers, level, PARENT)]
    # The governed space grants the audited identity exactly CAN_RUN, as step 6 converges it.
    estate["genie"][GOVERNED_SPACE] = [acl_entry("service_principal_name", CLIENT[identity], "CAN_RUN")]
    estate["genie_titles"] = {OTHER: OTHER_TITLE, GOVERNED_SPACE: GOVERNED_TITLE}
    return estate


def entry(identity: str, **overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "kind": "workspace_permission",
        "securable": {"type": "GENIE_SPACE", "id": OTHER, "name": OTHER_TITLE},
        "grantee": f"readers-{identity}",
        "privileges": ["CAN_VIEW"],
        "source": f"inherited:{PARENT}",
        "binding": {"isolation_mode": "ISOLATED", "workspace_ids": [WORKSPACE]},
        "identities": [identity],
        "privilege_class": "read",
        "reason": "Another team shares its space with this group",
        "expires_at": "2026-12-01",
    }
    value.update(overrides)
    return value


def _policy(entries: list[dict[str, Any]]) -> Any:
    env = {"MIP_ACCEPTED_EXPOSURE_JSON": json.dumps(entries), "MIP_GENIE_SPACE_NAME": GOVERNED_TITLE}
    parsed = [ExposureEntry.model_validate(item) for item in entries]
    digest = build_policy(parsed, now=NOW, static_control=lambda: []).digest
    return load_policy(env, now=NOW, ack=digest, static_control=lambda: [])


def _pre_refuses(workspace: FakeWorkspace, policy: Any, identity: str) -> bool:
    verdict = runner.base_verdict(
        workspace,
        app_name=APP_NAME,
        runtime_application_id=RUNTIME_CLIENT,
        verifier_application_id=VERIFIER_CLIENT,
        canonical_name=CANONICAL,
        acknowledgement="",
        list_agents=lambda: workspace.estate["agents"],
        resolve_groups=resolve_effective_groups,
        exposure=PrestopExposure(policy=policy, governed_title=GOVERNED_TITLE),
    )
    grants = [refusal for refusal in verdict.refusals if refusal.code == GENIE_GROUP_GRANT]
    assert all(refusal.code != ACCEPTED_EXPOSURE for refusal in verdict.refusals)
    return any(LABEL[identity] in refusal.message and OTHER in refusal.message for refusal in grants)


def _post_refuses(workspace: FakeWorkspace, policy: Any, identity: str) -> bool:
    groups = set(resolve_effective_groups(workspace, sp_id=SCIM[identity]).values()) | {"users"}
    try:
        audit_global_genie_access(
            workspace,
            reviewed_genie_space_id=GOVERNED_SPACE,
            application_id=CLIENT[identity],
            effective_group_names=groups,
            matcher=policy.matcher(identity),
        )
    except ExposureRefusal:
        raise
    except RuntimeError as exc:
        assert OTHER in str(exc), exc
        return True
    return False


CASES: dict[str, Any] = {
    "no_entry": (lambda identity: [], True),
    "exact": (lambda identity: [entry(identity)], False),
    "other_identity": (
        lambda identity: [entry(identity, identities=["runtime" if identity == "app" else "app"])],
        True,
    ),
    "broader_level": (lambda identity: [entry(identity, privileges=["CAN_VIEW", "CAN_RUN"])], True),
    "other_source": (lambda identity: [entry(identity, source="direct")], True),
    "other_parent": (lambda identity: [entry(identity, source="inherited:/directories/78")], True),
    "renamed_space": (
        lambda identity: [
            entry(identity, securable={"type": "GENIE_SPACE", "id": OTHER, "name": "Other Team Space v2"})
        ],
        True,
    ),
    "other_workspace": (
        lambda identity: [
            entry(identity, binding={"isolation_mode": "ISOLATED", "workspace_ids": ["4200000002"]})
        ],
        True,
    ),
    "expired": (lambda identity: [entry(identity, expires_at="2026-10-01")], True),
}


@pytest.mark.parametrize("identity", ["app", "runtime"])
@pytest.mark.parametrize("case", sorted(CASES))
def test_pre_and_post_refuse_the_same_exposure(identity: str, case: str) -> None:
    entries, refuses = CASES[case]
    policy = _policy(entries(identity))

    pre = _pre_refuses(FakeWorkspace(_estate(identity)), policy, identity)
    post = _post_refuses(FakeWorkspace(_estate(identity)), policy, identity)

    assert pre is post is refuses


@pytest.mark.parametrize("identity", ["app", "runtime"])
def test_an_elevated_grant_refuses_both_sides_for_runtime_and_is_accepted_for_app(identity: str) -> None:
    expires = (NOW + timedelta(days=30)).isoformat()
    entries = [entry(identity, privileges=["CAN_MANAGE"], privilege_class="elevated", expires_at=expires)]
    estate = _estate(identity, level="CAN_MANAGE")

    if identity == "runtime":
        with pytest.raises(ExposureRefusal, match="elevated exposure is refused for runtime"):
            _policy(entries)
        return
    policy = _policy(entries)
    assert not _pre_refuses(FakeWorkspace(estate), policy, identity)
    assert not _post_refuses(FakeWorkspace(estate), policy, identity)


def test_a_grant_reaching_the_verifier_still_refuses_before_the_stop() -> None:
    estate = _estate("app", group="shared-readers")
    estate["principals"]["shared-readers"]["members"].append("verifier-scim")
    policy = _policy([entry("app", grantee="shared-readers")])

    verdict = runner.base_verdict(
        FakeWorkspace(estate),
        app_name=APP_NAME,
        runtime_application_id=RUNTIME_CLIENT,
        verifier_application_id=VERIFIER_CLIENT,
        canonical_name=CANONICAL,
        acknowledgement="",
        list_agents=lambda: estate["agents"],
        resolve_groups=resolve_effective_groups,
        exposure=PrestopExposure(policy=policy, governed_title=GOVERNED_TITLE),
    )

    [grant] = [refusal for refusal in verdict.refusals if refusal.code == GENIE_GROUP_GRANT]
    assert f"verifier SP {VERIFIER_CLIENT}" in grant.message
    assert LABEL["app"] not in grant.message, "the App's exposure is accepted; the verifier's is not"
    assert any("accepted exposure: Genie space space-other-team" in note for note in verdict.notes)


def test_the_verifiers_forbid_all_audit_never_consults_a_matcher() -> None:
    estate = _estate("app", group="shared-readers")
    estate["principals"]["shared-readers"]["members"].append("verifier-scim")

    with pytest.raises(RuntimeError, match="through group"):
        audit_global_no_genie_access(
            FakeWorkspace(estate),
            application_id=VERIFIER_CLIENT,
            effective_group_names={"users", "shared-readers"},
        )


def test_no_entry_for_the_identity_reads_nothing_extra() -> None:
    workspace = FakeWorkspace(_estate("app"))
    calls_without = list(_reads(workspace, _policy([])))
    workspace = FakeWorkspace(_estate("app"))
    calls_other = list(_reads(workspace, _policy([_proxy_uc_entry()])))

    assert calls_without == calls_other


def _proxy_uc_entry() -> dict[str, Any]:
    """An entry no Genie consumer reads: the agent-proxy's UC grant on a foreign catalog."""

    return entry(
        "app",
        kind="uc_grant",
        securable={"type": "CATALOG", "id": "foreign_alpha", "name": "foreign_alpha"},
        privileges=["USE_CATALOG"],
        source="direct",
        binding={"isolation_mode": "OPEN", "workspace_ids": []},
        identities=["proxy"],
    )


def _reads(workspace: FakeWorkspace, policy: Any) -> list[str]:
    _pre_refuses(workspace, policy, "app")
    return workspace.estate["calls"]


def test_an_entry_on_the_governed_space_refuses_both_sides() -> None:
    # Named by its id under a stale title, so the loader's name check cannot see it.
    governed = entry("app", securable={"type": "GENIE_SPACE", "id": GOVERNED_SPACE, "name": "Renamed"})
    policy = _policy([entry("app"), governed])
    estate = _estate("app")

    verdict = runner.base_verdict(
        FakeWorkspace(estate),
        app_name=APP_NAME,
        runtime_application_id=RUNTIME_CLIENT,
        verifier_application_id=VERIFIER_CLIENT,
        canonical_name=CANONICAL,
        acknowledgement="",
        list_agents=lambda: estate["agents"],
        resolve_groups=resolve_effective_groups,
        exposure=PrestopExposure(policy=policy, governed_title=GOVERNED_TITLE),
    )
    codes = [refusal.code for refusal in verdict.refusals]
    assert ACCEPTED_EXPOSURE in codes and GENIE_GROUP_GRANT in codes, "nothing is relaxed"
    with pytest.raises(ExposureRefusal, match="governed Genie space"):
        _post_refuses(FakeWorkspace(_estate("app")), policy, "app")


def test_one_entry_per_source_and_the_exact_levels_it_grants() -> None:
    mixed = {
        "group_name": "users",
        "all_permissions": [
            {"permission_level": "CAN_VIEW", "inherited": False},
            {"permission_level": "CAN_MANAGE", "inherited": True, "inherited_from_object": [PARENT]},
            {"permission_level": "CAN_RUN", "inherited": True, "inherited_from_object": [PARENT]},
            {"permission_level": "CAN_EDIT", "inherited": True},
        ],
    }

    exposures = entry_exposures(mixed, space_id=OTHER, title=OTHER_TITLE, workspace_id=WORKSPACE)

    by_source = {item.source: sorted(item.privileges) for item in exposures}
    assert by_source == {
        "direct": ["CAN_VIEW"],
        f"inherited:{PARENT}": ["CAN_MANAGE", "CAN_RUN"],
        "inherited:": ["CAN_EDIT"],
    }
    assert {item.grantee for item in exposures} == {"users"}
    assert {item.workspace_ids for item in exposures} == {(WORKSPACE,)}
    assert [level.source for level in entry_levels(mixed)] == [
        "direct",
        f"inherited:{PARENT}",
        f"inherited:{PARENT}",
        "inherited:",
    ]


def test_a_genie_entry_for_the_proxy_refuses_at_load() -> None:
    """No Genie audit of the agent-proxy consults a matcher, so its entry is dead and refuses."""

    with pytest.raises(ExposureRefusal, match="no Genie audit consults a matcher for proxy"):
        _policy([entry("app", grantee="proxy-readers", identities=["proxy"])])
    with pytest.raises(ExposureRefusal, match="no Genie audit consults a matcher for proxy"):
        _policy([entry("app", grantee="proxy-readers", identities=["app", "proxy"])])


def test_the_deny_all_audit_takes_no_matcher() -> None:
    """The verifier's and the agent-proxy's zero-Genie proofs stay absolute, the governed space too."""

    assert "matcher" not in inspect.signature(audit_global_no_genie_access).parameters
    assert "matcher" not in inspect.signature(denial.revoke_all_genie_acls).parameters


@pytest.mark.parametrize("space", [OTHER, GOVERNED_SPACE])
def test_the_proxy_deny_all_refuses_its_group_grant_end_to_end(
    monkeypatch: pytest.MonkeyPatch, space: str
) -> None:
    """``revoke_all_genie_acls`` end to end (review of #306): a read grant through the proxy's
    group refuses on an unrelated space and on the governed one, whatever the profile lists."""

    estate = _estate("app")
    estate["principals"]["proxy-readers"] = {"kind": "group", "id": "group-proxy", "members": ["proxy-scim"]}
    estate["genie"][space] = [acl_entry("group_name", "proxy-readers", "CAN_VIEW", PARENT)]
    listed = entry(
        "app",
        securable={"type": "GENIE_SPACE", "id": space, "name": estate["genie_titles"][space]},
        grantee="proxy-readers",
        identities=["proxy"],
    )
    monkeypatch.setenv("MIP_ACCEPTED_EXPOSURE_JSON", json.dumps([listed]))
    monkeypatch.setenv("MIP_ACK_ACCEPTED_EXPOSURE_PLAN", "0" * 64)
    monkeypatch.setattr(denial, "inspect_managed_agent_proxy_group", lambda *_a, **_kw: None)

    with pytest.raises(RuntimeError, match="Genie deny-all did not converge") as caught:
        denial.revoke_all_genie_acls(
            FakeWorkspace(estate),
            application_id="proxy-client-EXAMPLE",
            service_principal_id="proxy-scim",
            effective_group_names={"users", "proxy-readers"},
            assert_single_writer=lambda: None,
            assert_legacy_cleanup_quiesced=lambda: None,
        )

    message = str(caught.value)
    assert f"Genie duplicate postflight {space}" in message and "boundary drifted" in message
    assert "Genie postflight" in message and "through group" in message
    assert "accepted exposure" not in message, "the deny-all never loads the policy"


@pytest.mark.parametrize(
    ("expires_in", "refuses"),
    [(timedelta(hours=1), True), (timedelta(hours=24), True), (timedelta(hours=25), False)],
)
def test_an_entry_expiring_within_the_run_margin_refuses_before_the_stop(
    monkeypatch: pytest.MonkeyPatch, expires_in: timedelta, refuses: bool
) -> None:
    """Review of #306: the post-stop audits load later and would judge it expired, App stopped."""

    entries = [entry("app", expires_at=(NOW + expires_in).isoformat())]
    parsed = [ExposureEntry.model_validate(item) for item in entries]
    monkeypatch.setenv("MIP_ACCEPTED_EXPOSURE_JSON", json.dumps(entries))
    monkeypatch.setenv(
        "MIP_ACK_ACCEPTED_EXPOSURE_PLAN", build_policy(parsed, now=NOW, static_control=lambda: []).digest
    )
    for name in ("MIP_GENIE_SPACE_NAME", "DATABRICKS_VERIFIER_CLIENT_ID", "MIP_AI_GATEWAY_VERIFIER_CLIENT_ID"):
        monkeypatch.delenv(name, raising=False)

    exposure, refusals = load_prestop_exposure(
        verifier_application_id=VERIFIER_CLIENT, governed_title=GOVERNED_TITLE, now=NOW
    )

    if refuses:
        assert exposure is None
        assert [refusal.code for refusal in refusals] == [ACCEPTED_EXPOSURE]
        assert "entry 0: expires within the 24 h deploy-run margin" in refusals[0].message
        # The same entry, judged by a post-stop audit an hour later, is already expired.
        later = _policy(entries).matcher("app")
        assert replace(later, now=NOW + expires_in).verdict(entry_exposures_for(entries[0])[0]).code == "expired"
    else:
        assert exposure is not None and refusals == []


def entry_exposures_for(item: dict[str, Any]) -> tuple[Any, ...]:
    acl = acl_entry("group_name", item["grantee"], item["privileges"][0], PARENT)
    return entry_exposures(acl, space_id=OTHER, title=OTHER_TITLE, workspace_id=WORKSPACE)


def test_elevated_for_the_proxy_refuses_at_load() -> None:
    expires = (NOW + timedelta(days=10)).isoformat()

    with pytest.raises(ExposureRefusal, match="elevated exposure is refused for proxy"):
        _policy([entry("app", identities=["proxy"], privileges=["CAN_EDIT"], privilege_class="elevated", expires_at=expires)])
