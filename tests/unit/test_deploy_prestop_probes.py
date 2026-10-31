"""Read-only probes that refuse a deploy before step 0a stops the live App.

Two failures used to surface only after ``scripts/deploy.sh`` had stopped (and,
on the unsigned rebase path, quarantined) the App: the irreversible retirement
of a canonical Supervisor the agent-runtime SP did not create, and the global
Genie audits refusing a group grant that reaches an audited service principal.
These tests pin the probe's refusals, its read-only behavior, and its position
in the deploy orchestration: before the signed lease and before any App stop.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.errors import NotFound, PermissionDenied

from tests.fixtures.deploy_script import deploy_entrypoint_text
from tools.databricks import deploy_prestop_probes as probes
from tools.databricks import prestop_genie_grants as genie_grants
from tools.databricks import reconcile_historical_agent_endpoints as reconcile
from tools.databricks import supervisor_retirement_ack as ack
from tools.databricks.agentic_supervisor_endpoint import (
    managed_query_supervisor_replacement_name,
)
from tools.databricks.supervisor_agent_contract import (
    RUNTIME_REPLACEMENT_SUFFIX,
    supervisor_replacement_name,
)

REPO = Path(__file__).resolve().parents[2]
AUTOMATION_STEP = REPO / "scripts" / "lib" / "deploy_step_automation_identities.sh"
DEPLOYMENT_DOC = REPO / "docs" / "deployment.md"
DEPLOY_DEV = REPO / ".github" / "workflows" / "deploy-dev.yml"

RUNTIME_ID = "runtime-client"
VERIFIER_ID = "verifier-client"
HUMAN_ID = "a1a1a1a1-0000-4000-8000-000000000001"
CANONICAL = "Mortgage Growth Agent"


@pytest.fixture(autouse=True)
def _suite_probes_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    # The record, owner, serving and Genie-budget probes have their own tests
    # (tests/unit/test_prestop_*.py); here main() runs only these clauses.
    monkeypatch.setattr(probes, "suite_refusals", lambda *_args, **_kwargs: ([], []))


def _human_canonical(**overrides: str) -> dict[str, str]:
    return {
        "supervisor_agent_id": HUMAN_ID,
        "display_name": CANONICAL,
        "creator": "operator@summit.example",
        "endpoint_name": "mas-a1a1a1a1-endpoint",
        "create_time": "2026-01-02T03:04:05.678Z",
        **overrides,
    }


def _supervisor_verdict(
    agents: list[dict[str, str]], acknowledgement: str = ""
) -> tuple[list[str], list[str]]:
    return probes.supervisor_retirement_refusals(
        agents,
        canonical_name=CANONICAL,
        runtime_application_id=RUNTIME_ID,
        acknowledgement=acknowledgement,
    )


# --- Supervisor retirement acknowledgement -----------------------------------


def test_unacknowledged_human_supervisor_retirement_is_refused_with_both_remedies() -> None:
    refusals, notes = _supervisor_verdict([_human_canonical()])

    assert notes == []
    assert len(refusals) == 1
    refusal = refusals[0]
    assert HUMAN_ID in refusal
    assert "'operator@summit.example'" in refusal
    assert "'mas-a1a1a1a1-endpoint'" in refusal
    assert "cannot be undone" in refusal
    assert f"export MIP_RETIRE_HUMAN_SUPERVISOR={HUMAN_ID}" in refusal
    assert f"rename it off the canonical name {CANONICAL!r}" in refusal


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", "accept"])
def test_a_boolean_acknowledgement_is_refused(value: str) -> None:
    refusals, _ = _supervisor_verdict([_human_canonical()], acknowledgement=value)

    assert len(refusals) == 1
    assert "must be the Supervisor id itself, not a boolean" in refusals[0]


def test_an_acknowledgement_naming_another_supervisor_is_refused() -> None:
    refusals, _ = _supervisor_verdict(
        [_human_canonical()], acknowledgement="b2b2b2b2-0000-4000-8000-000000000002"
    )

    assert len(refusals) == 1
    assert "names 'b2b2b2b2-0000-4000-8000-000000000002', not this Supervisor" in refusals[0]


def test_the_exact_supervisor_id_acknowledges_the_documented_first_migration() -> None:
    refusals, notes = _supervisor_verdict([_human_canonical()], acknowledgement=HUMAN_ID)

    assert refusals == []
    assert any("acknowledges the permanent deletion" in note for note in notes)


OTHER_SP_CREATOR = "c3c3c3c3-0000-4000-8000-000000000003"


def test_a_canonical_supervisor_created_by_another_service_principal_needs_the_id() -> None:
    # "Not the runtime SP" includes every other service principal, not only a
    # person's e-mail: the planner replaces it and the cutover deletes it.
    other_sp = _human_canonical(creator=OTHER_SP_CREATOR)

    refusals, notes = _supervisor_verdict([other_sp])
    assert len(refusals) == 1 and f"created by {OTHER_SP_CREATOR!r}" in refusals[0]
    assert not any("runtime-owned" in note for note in notes)
    assert _supervisor_verdict([other_sp], acknowledgement=HUMAN_ID)[0] == []
    squatter = _human_canonical(display_name=RESERVED_NAMES[0], creator=OTHER_SP_CREATOR)
    assert len(
        probes.reserved_replacement_refusals(
            [squatter], canonical_name=CANONICAL, runtime_application_id=RUNTIME_ID
        )
    ) == 1


def test_a_runtime_owned_canonical_supervisor_needs_no_acknowledgement() -> None:
    refusals, notes = _supervisor_verdict([_human_canonical(creator=RUNTIME_ID)])

    assert refusals == []
    assert any("runtime-owned" in note for note in notes)


def test_non_canonical_supervisors_are_never_retirement_candidates() -> None:
    other = _human_canonical(
        supervisor_agent_id="b2b2b2b2", display_name="supervisor-agent-2026-01-02"
    )

    refusals, notes = _supervisor_verdict([other], acknowledgement=HUMAN_ID)

    assert refusals == []
    assert any("is set but unused" in note for note in notes)


def test_duplicate_canonical_supervisors_are_refused_for_governance_review() -> None:
    refusals, _ = _supervisor_verdict(
        [_human_canonical(), _human_canonical(supervisor_agent_id="second")],
        acknowledgement=HUMAN_ID,
    )

    assert len(refusals) == 1
    assert "multiple Supervisor agents use the canonical name" in refusals[0]


@pytest.mark.parametrize("field", list(probes.PINNED_SUPERVISOR_FIELDS))
@pytest.mark.parametrize("acknowledgement", ["", HUMAN_ID])
def test_a_non_runtime_canonical_supervisor_with_an_incomplete_identity_is_refused(
    field: str, acknowledgement: str
) -> None:
    # pin_journal refuses an incomplete tuple after step 0a whatever the
    # acknowledgement says, and a blank creator is never the runtime SP.
    refusals, notes = _supervisor_verdict(
        [_human_canonical(**{field: ""})], acknowledgement=acknowledgement
    )

    assert len(refusals) == 1
    assert f"incomplete immutable identity (missing {field})" in refusals[0]
    assert "manual governance review is required" in refusals[0]
    assert not any("runtime-owned" in note for note in notes)


def test_an_acknowledgement_differing_only_in_case_is_refused() -> None:
    refusals, _ = _supervisor_verdict([_human_canonical()], acknowledgement=HUMAN_ID.upper())

    assert len(refusals) == 1
    assert f"names {HUMAN_ID.upper()!r}, not this Supervisor" in refusals[0]


@pytest.mark.parametrize("padded", [f" {CANONICAL}", f"{CANONICAL} ", f"{CANONICAL}\n"])
@pytest.mark.parametrize(
    ("creator", "acknowledgement"), [(RUNTIME_ID, ""), ("operator@summit.example", HUMAN_ID)]
)
def test_a_whitespace_padded_canonical_name_is_refused(
    padded: str, creator: str, acknowledgement: str
) -> None:
    # The planner matches the raw name and the journal strips it, so a padded
    # MIP_AGENT_SUPERVISOR_NAME must not pass on the stripped name's verdict,
    # whether that Supervisor is runtime-owned or acknowledged.
    refusals, notes = probes.supervisor_retirement_refusals(
        [_human_canonical(creator=creator)],
        canonical_name=padded,
        runtime_application_id=RUNTIME_ID,
        acknowledgement=acknowledgement,
    )

    assert notes == []
    assert len(refusals) == 1
    assert f"the canonical Supervisor name {padded!r} (MIP_AGENT_SUPERVISOR_NAME)" in refusals[0]
    assert "has leading or trailing whitespace" in refusals[0]
    assert "Remove the whitespace, then re-run." in refusals[0]


# --- Reserved replacement names ------------------------------------------------

GOVERNED_SPACE = "01f13d4968af1b249dc388fd5b18b195"
RESERVED_NAMES = (
    f"{CANONICAL}{RUNTIME_REPLACEMENT_SUFFIX}",
    supervisor_replacement_name(CANONICAL, genie_space_id=GOVERNED_SPACE, catalog="mip"),
    managed_query_supervisor_replacement_name(
        CANONICAL, genie_space_id=GOVERNED_SPACE, catalog="mip"
    ),
    supervisor_replacement_name(CANONICAL, genie_space_id="older-space", catalog="mip"),
)
UNRESERVED_NAMES = (
    CANONICAL,
    f"{CANONICAL} [mip-agent-runtime-ABCDEF123456]",
    f"{CANONICAL} [mip-agent-runtime-abc123]",
    f"{CANONICAL} [mip-agent-runtime-abcdef123456] (kept)",
    f"{CANONICAL} [mip-agent-runtime-abcdef123456]-mq1 copy",
    f"{CANONICAL}{RUNTIME_REPLACEMENT_SUFFIX} (kept)",
    f"Other Agent{RUNTIME_REPLACEMENT_SUFFIX}",
    f"{CANONICAL} (kept by growth team)",
)


@pytest.mark.parametrize("name", RESERVED_NAMES)
def test_a_non_runtime_supervisor_on_a_reserved_replacement_name_is_refused(name: str) -> None:
    squatter = _human_canonical(supervisor_agent_id="squatter-id", display_name=name)

    refusals = probes.reserved_replacement_refusals(
        [squatter], canonical_name=CANONICAL, runtime_application_id=RUNTIME_ID
    )

    assert len(refusals) == 1
    assert "'squatter-id'" in refusals[0] and repr(name) in refusals[0]
    assert "Rename it to a name outside the reserved replacement names" in refusals[0]


@pytest.mark.parametrize("name", RESERVED_NAMES)
def test_a_runtime_supervisor_on_a_reserved_replacement_name_is_the_green_path(
    name: str,
) -> None:
    green = _human_canonical(display_name=name, creator=RUNTIME_ID)

    assert (
        probes.reserved_replacement_refusals(
            [green], canonical_name=CANONICAL, runtime_application_id=RUNTIME_ID
        )
        == []
    )


@pytest.mark.parametrize("name", RESERVED_NAMES + UNRESERVED_NAMES)
def test_reserved_names_match_the_historical_supervisor_family(name: str) -> None:
    expected = reconcile._supervisor_family(name, CANONICAL) and name != CANONICAL

    assert probes.is_reserved_replacement_name(name, CANONICAL) is expected
    assert expected is (name in RESERVED_NAMES)


def test_a_reserved_name_refusal_needs_no_canonical_supervisor() -> None:
    workspace = _ReadOnlyWorkspace(acls={}, app=_app())
    squatter = _human_canonical(display_name=RESERVED_NAMES[0])

    refusals, notes = _run(workspace, [squatter], acknowledgement=HUMAN_ID)

    assert len(refusals) == 1 and "reserved runtime replacement name" in refusals[0]
    assert any("no Supervisor holds the canonical name" in note for note in notes)


def test_the_acknowledgement_is_read_from_the_environment_only_when_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ack.ACKNOWLEDGEMENT_ENV, raising=False)
    assert ack.supervisor_retirement_acknowledgement() == ""
    monkeypatch.setenv(ack.ACKNOWLEDGEMENT_ENV, f"  {HUMAN_ID}\n")
    assert ack.supervisor_retirement_acknowledgement() == HUMAN_ID
    assert ack.supervisor_retirement_acknowledgement({}) == ""


def test_retirement_classification_rejects_a_blank_runtime_identity() -> None:
    with pytest.raises(ValueError, match="agent-runtime application ID is required"):
        ack.is_runtime_created("operator@summit.example", runtime_application_id=" ")


# --- Genie group grants -------------------------------------------------------


def _acl(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"access_control_list": list(entries)}


# A level is (permission, False) for a direct grant, (permission, True) for an
# inherited one with no named parent, or (permission, "/directories/<id>") for
# one inherited from that object, as the permissions API reports it.
Level = tuple[str, bool | str]
SHARED_DIRECTORY = "/directories/100000000000005"
OTHER_DIRECTORY = "/directories/100000000000006"


def _permissions(levels: tuple[Level, ...]) -> list[dict[str, Any]]:
    permissions: list[dict[str, Any]] = []
    for level, inherited in levels:
        permission: dict[str, Any] = {"permission_level": level, "inherited": bool(inherited)}
        if isinstance(inherited, str):
            permission["inherited_from_object"] = [inherited]
        permissions.append(permission)
    return permissions


def _group(name: str, *levels: Level) -> dict[str, Any]:
    return {"group_name": name, "all_permissions": _permissions(levels)}


class _ReadOnlyWorkspace:
    """Genie, Apps and SCIM reads; any non-GET call fails the test."""

    def __init__(
        self,
        *,
        acls: dict[str, dict[str, Any]],
        app: object | None = None,
        service_principals: dict[str, str] | None = None,
        duplicate_principals: tuple[tuple[str, str], ...] = (),
        admin: str = "deployer@example.invalid",
        shared_folder: object = SHARED_DIRECTORY.rsplit("/", 1)[1],
        titles: dict[str, str] | None = None,
    ) -> None:
        self._shared_folder = shared_folder
        self.calls: list[tuple[str, str]] = []
        self._acls = acls
        self._app = app
        principals = {RUNTIME_ID: "runtime-scim", VERIFIER_ID: "verifier-scim"}
        self._service_principals = [
            *(principals if service_principals is None else service_principals).items(),
            *duplicate_principals,
        ]
        titles = titles or {space_id: f"Space {space_id}" for space_id in acls}
        pages = [
            {
                "spaces": [{"space_id": space_id, "title": titles[space_id]}],
                "next_page_token": f"page-{index + 1}" if index + 1 < len(acls) else None,
            }
            for index, space_id in enumerate(acls)
        ] or [{"spaces": [], "next_page_token": None}]
        self.genie = SimpleNamespace(
            list_spaces=lambda page_token=None: pages[
                0 if page_token is None else int(page_token.split("-")[1])
            ]
        )
        self.api_client = SimpleNamespace(do=self._do)
        self.apps = SimpleNamespace(get=self._get_app)
        self.service_principals = SimpleNamespace(list=self._list_service_principals)
        self.workspace = SimpleNamespace(get_status=self._get_status)
        self.current_user = SimpleNamespace(
            me=lambda: SimpleNamespace(user_name=admin, groups=[{"display": "admins"}])
        )

    def _do(self, method: str, path: str, **_kwargs: object) -> object:
        self.calls.append((method, path))
        assert method == "GET", f"pre-stop probe attempted a {method} on {path}"
        space_id = path.rsplit("/", 1)[1]
        acl = self._acls[space_id]
        if isinstance(acl, Exception):
            raise acl
        return acl

    def _get_status(self, path: str) -> object:
        self.calls.append(("GET", f"workspace/get-status {path}"))
        if isinstance(self._shared_folder, Exception):
            raise self._shared_folder
        return SimpleNamespace(object_id=int(str(self._shared_folder)), path=path)

    def _get_app(self, name: str) -> object:
        self.calls.append(("GET", f"apps/{name}"))
        if self._app is None:
            raise NotFound(f"App {name} does not exist")
        if isinstance(self._app, Exception):
            raise self._app
        return self._app

    def _list_service_principals(self, *, filter: str) -> list[object]:  # noqa: A002
        self.calls.append(("GET", f"servicePrincipals?{filter}"))
        return [
            SimpleNamespace(application_id=application_id, id=scim_id)
            for application_id, scim_id in self._service_principals
            if f'"{application_id}"' in filter
        ]


def _app() -> object:
    return SimpleNamespace(
        service_principal_client_id="d4d4d4d4-0000-4000-8000-000000000004",
        service_principal_id=100000000000004,
    )


def _walk(memberships: dict[str, dict[str, str]], calls: list[str] | None = None) -> Any:
    def resolve(_workspace: object, *, sp_id: str) -> dict[str, str]:
        if calls is not None:
            calls.append(sp_id)
        return memberships.get(sp_id, {})

    return resolve


# Workspace SCIM puts only the agent-runtime SP in this group; the App and
# verifier SPs are not members. Every refusal for a group other than 'users'
# depends on the probe resolving each principal's own SCIM id.
RUNTIME_ONLY_GROUP = {"runtime-scim": {"g-1": "mip-runtime-readers"}}
RESOLVED_SCIM_IDS = ["100000000000004", "runtime-scim", "verifier-scim"]


def _runtime_readers_workspace() -> _ReadOnlyWorkspace:
    return _ReadOnlyWorkspace(
        acls={"space-view": _acl(_group("mip-runtime-readers", ("CAN_VIEW", False)))}, app=_app()
    )


def _assert_only_the_runtime_readers_refusal(refusals: list[str]) -> None:
    assert len(refusals) == 1
    assert "group 'mip-runtime-readers' holds CAN_VIEW" in refusals[0]
    assert f"; reaches agent-runtime SP {RUNTIME_ID}. " in refusals[0]


def test_every_principal_belongs_to_the_system_users_group() -> None:
    groups = probes.principal_effective_groups(
        object(), scim_id="100000000000004", resolve_groups=_walk({})
    )

    assert groups == frozenset({"users"})


def test_a_users_grant_reaches_every_audited_principal_inherited_or_direct() -> None:
    workspace = _ReadOnlyWorkspace(
        acls={
            "space-inherited": _acl(
                _group("users", ("CAN_MANAGE", True)), _group("admins", ("CAN_MANAGE", True))
            ),
            "space-direct": _acl(_group("users", ("CAN_RUN", False))),
        }
    )
    effective = {
        label: probes.principal_effective_groups(workspace, scim_id=label, resolve_groups=_walk({}))
        for label in ("App SP a", "agent-runtime SP b", "verifier SP c")
    }

    grants = probes.genie_group_grants(workspace, effective_groups=effective)

    assert [(g.space_id, g.group, g.levels, g.inherited) for g in grants] == [
        ("space-direct", "users", ("CAN_RUN",), False),
        ("space-inherited", "users", ("CAN_MANAGE",), True),
    ]
    assert all(
        g.principals == ("App SP a", "agent-runtime SP b", "verifier SP c") for g in grants
    )
    assert "CAN_MANAGE (inherited from an unnamed parent object)" in grants[1].describe()
    assert "CAN_RUN (direct on the space)" in grants[0].describe()
    assert {method for method, _ in workspace.calls} == {"GET"}


def test_a_group_grant_reaches_only_its_members_and_direct_grants_are_out_of_scope() -> None:
    workspace = _ReadOnlyWorkspace(
        acls={
            "governed": _acl(
                {"service_principal_name": RUNTIME_ID, "all_permissions": [
                    {"permission_level": "CAN_RUN", "inherited": False}
                ]},
                _group("mip-verifier-readers", ("CAN_RUN", False)),
                _group("unrelated-team", ("CAN_MANAGE", False)),
                _group("empty-entry"),
            )
        }
    )
    effective = {
        "agent-runtime SP r": frozenset({"users"}),
        "verifier SP v": frozenset({"users", "mip-verifier-readers", "empty-entry"}),
    }

    grants = probes.genie_group_grants(workspace, effective_groups=effective)

    assert [(g.group, g.principals) for g in grants] == [
        ("mip-verifier-readers", ("verifier SP v",))
    ]


def test_an_unreadable_genie_acl_fails_closed() -> None:
    workspace = _ReadOnlyWorkspace(acls={"space": PermissionError("403")})

    with pytest.raises(RuntimeError, match="cannot inspect Genie ACL for 'space'"):
        probes.genie_group_grants(workspace, effective_groups={"verifier SP v": frozenset()})


def _principals(workspace: _ReadOnlyWorkspace) -> probes.AuditedPrincipals:
    return probes.audited_principals(
        workspace,
        app_name="mip-app",
        runtime_application_id=RUNTIME_ID,
        verifier_application_id=VERIFIER_ID,
    )


def test_an_absent_app_is_skipped_but_its_future_users_membership_still_counts() -> None:
    principals = _principals(_ReadOnlyWorkspace(acls={}))

    assert principals.resolved == {
        f"agent-runtime SP {RUNTIME_ID}": "runtime-scim",
        f"verifier SP {VERIFIER_ID}": "verifier-scim",
    }
    assert principals.unresolved == {}
    assert principals.notes and "does not exist yet" in principals.notes[0]


def test_an_existing_app_with_an_incomplete_identity_is_unresolved() -> None:
    workspace = _ReadOnlyWorkspace(
        acls={}, app=SimpleNamespace(service_principal_client_id="app", service_principal_id="")
    )

    principals = _principals(workspace)

    assert list(principals.unresolved) == ["App 'mip-app' SP"]
    assert "incomplete identity" in principals.unresolved["App 'mip-app' SP"]
    assert not any(label.startswith("App") for label in principals.resolved)


def test_an_app_read_error_other_than_not_found_propagates() -> None:
    # Only a missing App is a first install; a refused or failed read must not
    # be mistaken for one and silently drop the App SP from the audit.
    workspace = _ReadOnlyWorkspace(acls={}, app=PermissionDenied("apps.get denied"))

    with pytest.raises(PermissionDenied, match="apps.get denied"):
        _principals(workspace)


@pytest.mark.parametrize(
    ("present", "missing", "missing_id"),
    [
        ({RUNTIME_ID: "r"}, "verifier", VERIFIER_ID),
        ({VERIFIER_ID: "v"}, "agent-runtime", RUNTIME_ID),
    ],
)
def test_a_missing_audited_service_principal_is_unresolved_and_named(
    present: dict[str, str], missing: str, missing_id: str
) -> None:
    principals = _principals(
        _ReadOnlyWorkspace(acls={}, app=_app(), service_principals=present)
    )

    reason = principals.unresolved[f"{missing} SP {missing_id}"]
    assert f"expected exactly one {missing} service principal" in reason
    assert "found 0; add it to this workspace" in reason
    assert f"{missing} SP {missing_id}" not in principals.resolved


def test_a_duplicated_audited_service_principal_is_unresolved() -> None:
    workspace = _ReadOnlyWorkspace(
        acls={}, app=_app(), duplicate_principals=((RUNTIME_ID, "runtime-scim-2"),)
    )

    principals = _principals(workspace)

    reason = principals.unresolved[f"agent-runtime SP {RUNTIME_ID}"]
    assert "found 2; remove the duplicates" in reason
    assert set(principals.resolved) == {
        "App SP d4d4d4d4-0000-4000-8000-000000000004",
        f"verifier SP {VERIFIER_ID}",
    }


def _run(
    workspace: _ReadOnlyWorkspace,
    agents: list[dict[str, str]],
    acknowledgement: str = "",
    resolve_groups: Any = None,
) -> tuple[list[str], list[str]]:
    return probes.run_probes(
        workspace,
        app_name="mip-app",
        runtime_application_id=RUNTIME_ID,
        verifier_application_id=VERIFIER_ID,
        canonical_name=CANONICAL,
        acknowledgement=acknowledgement,
        list_agents=lambda: agents,
        resolve_groups=_walk({}) if resolve_groups is None else resolve_groups,
    )


def test_both_probes_report_every_refusal_in_one_read_only_pass() -> None:
    workspace = _ReadOnlyWorkspace(
        acls={"space-one": _acl(_group("users", ("CAN_MANAGE", True)))}, app=_app()
    )

    refusals, _ = _run(workspace, [_human_canonical()])

    assert len(refusals) == 2
    assert "MIP_RETIRE_HUMAN_SUPERVISOR" in refusals[0]
    genie = refusals[1]
    assert "Genie space space-one" in genie
    assert "App SP d4d4d4d4-0000-4000-8000-000000000004" in genie
    assert "Never widen the audits' exemptions" in genie
    assert "not possible for the system 'users' group" in genie
    assert {method for method, _ in workspace.calls} == {"GET"}


def test_missing_principals_are_refused_alongside_every_other_refusal() -> None:
    # A shared dev workspace today: a human canonical Supervisor, 'users' Genie grants, and
    # neither the agent-runtime nor the verifier SP in the workspace. The
    # operator must see all of it in one run, not a traceback for the first gap.
    workspace = _ReadOnlyWorkspace(
        acls={"space-two": _acl(_group("users", ("CAN_RUN", False)))},
        app=_app(),
        service_principals={},
    )

    refusals, _ = _run(workspace, [_human_canonical()])

    assert len(refusals) == 4
    assert "MIP_RETIRE_HUMAN_SUPERVISOR" in refusals[0]
    for refusal, (label, client) in zip(
        refusals[1:3], (("agent-runtime", RUNTIME_ID), ("verifier", VERIFIER_ID)), strict=True
    ):
        assert refusal.startswith(f"cannot read the {label} SP {client}'s group memberships")
        assert "found 0; add it to this workspace, then re-run" in refusal
        assert "refuses rather than pass a principal it could not audit" in refusal
    users = refusals[3]
    assert "Genie space space-two" in users and "group 'users'" in users
    assert (
        "reaches App SP d4d4d4d4-0000-4000-8000-000000000004, "
        f"agent-runtime SP {RUNTIME_ID}, verifier SP {VERIFIER_ID}"
    ) in users
    assert {method for method, _ in workspace.calls} == {"GET"}


def test_a_service_principal_without_a_scim_id_is_refused_alongside_every_other_refusal() -> None:
    # A blank SCIM id must never reach the group walk: the admin SCIM resolver
    # exits on a blank id, which would end the run and drop every refusal the
    # probe had already collected.
    calls: list[str] = []
    workspace = _ReadOnlyWorkspace(
        acls={"space-two": _acl(_group("users", ("CAN_RUN", False)))},
        app=_app(),
        service_principals={RUNTIME_ID: "", VERIFIER_ID: "verifier-scim"},
    )

    refusals, _ = _run(workspace, [_human_canonical()], resolve_groups=_walk({}, calls))

    assert len(refusals) == 3
    assert "MIP_RETIRE_HUMAN_SUPERVISOR" in refusals[0]
    assert refusals[1].startswith(
        f"cannot read the agent-runtime SP {RUNTIME_ID}'s group memberships: the "
        f"agent-runtime service principal {RUNTIME_ID!r} has no SCIM id; manual identity "
        "review is required, then re-run."
    )
    assert f"agent-runtime SP {RUNTIME_ID}" in refusals[2] and "group 'users'" in refusals[2]
    assert calls == ["100000000000004", "verifier-scim"]


def test_a_scim_group_reaching_only_the_runtime_sp_is_refused_through_its_own_scim_id() -> None:
    calls: list[str] = []
    workspace = _runtime_readers_workspace()

    refusals, _ = _run(workspace, [], resolve_groups=_walk(RUNTIME_ONLY_GROUP, calls))

    assert calls == RESOLVED_SCIM_IDS
    _assert_only_the_runtime_readers_refusal(refusals)
    assert {method for method, _ in workspace.calls} == {"GET"}


def test_an_unreadable_genie_acl_fails_the_whole_probe_closed() -> None:
    # A Genie space whose ACL cannot be read must never count as a clean one.
    workspace = _ReadOnlyWorkspace(
        acls={"clean": _acl(), "space": PermissionError("403")}, app=_app()
    )

    with pytest.raises(RuntimeError, match="cannot inspect Genie ACL for 'space'"):
        _run(workspace, [])


def test_an_app_read_error_fails_the_whole_probe_closed() -> None:
    workspace = _ReadOnlyWorkspace(acls={}, app=PermissionDenied("apps.get denied"))

    with pytest.raises(PermissionDenied, match="apps.get denied"):
        _run(workspace, [])


def _principal(application_id: str, *levels: Level) -> dict[str, Any]:
    return {"service_principal_name": application_id, "all_permissions": _permissions(levels)}


def test_any_genie_grant_to_the_verifier_is_refused_without_the_governed_space() -> None:
    # --forbid-all-genie refuses every verifier grant, so no governed space id
    # is needed. Direct App and runtime grants are judged against the governed
    # space after it is provisioned and stay with the post-stop audits.
    workspace = _ReadOnlyWorkspace(
        acls={
            "governed": _acl(
                _principal(RUNTIME_ID, ("CAN_RUN", False)),
                _principal(_app().service_principal_client_id, ("CAN_RUN", False)),
            ),
            "space-view": _acl(_principal(VERIFIER_ID, ("CAN_VIEW", True))),
            "space-mixed": _acl(_principal(VERIFIER_ID, ("CAN_RUN", False), ("CAN_VIEW", True))),
            "no-levels": _acl(_principal(VERIFIER_ID)),
        },
        app=_app(),
    )

    refusals, _ = _run(workspace, [])

    assert len(refusals) == 1
    verifier = refusals[0]
    assert "--forbid-all-genie" in verifier
    view = f"Genie space space-view: verifier service principal {VERIFIER_ID!r}"
    mixed = f"Genie space space-mixed: verifier service principal {VERIFIER_ID!r}"
    assert f"{view} holds CAN_VIEW (inherited from an unnamed parent object)" in verifier
    # A direct CAN_RUN does not hide the inherited CAN_VIEW beside it.
    assert (
        f"{mixed} holds CAN_RUN (direct on the space), "
        "CAN_VIEW (inherited from an unnamed parent object)"
    ) in verifier
    assert "Genie space governed" not in verifier and "no-levels" not in verifier
    assert "The verifier needs no Genie access" in verifier
    assert {method for method, _ in workspace.calls} == {"GET"}


def _genie_refusal(acl: dict[str, Any], **workspace_options: Any) -> tuple[str, list[str]]:
    workspace = _ReadOnlyWorkspace(acls={"space-one": acl}, app=_app(), **workspace_options)
    refusals, _ = _run(workspace, [])
    assert len(refusals) == 1
    status_reads = [path for method, path in workspace.calls if "get-status" in path]
    assert {method for method, _ in workspace.calls} == {"GET"}
    return refusals[0], status_reads


def test_an_inherited_users_grant_names_its_folder_and_is_not_sent_to_the_space() -> None:
    # A shared workspace: several spaces inherit 'users' CAN_MANAGE from
    # /Workspace/Shared.
    # That grant cannot be revoked on the space, and the folder ACL is shared
    # by every workspace user, so the remedy must not send the operator there.
    refusal, status_reads = _genie_refusal(_acl(_group("users", ("CAN_MANAGE", SHARED_DIRECTORY))))

    assert (
        f"group 'users' holds CAN_MANAGE (inherited from {SHARED_DIRECTORY}, "
        "which is /Workspace/Shared)"
    ) in refusal
    assert genie_grants.INHERITED_GRANT_REMEDY in refusal
    assert "cannot be removed on the space" in refusal
    assert "affects every workspace user's shared content" in refusal
    assert genie_grants.DIRECT_GROUP_REMEDY not in refusal
    assert "on the space itself" not in refusal
    assert "Never widen the audits' exemptions" in refusal
    assert status_reads == ["workspace/get-status /Workspace/Shared"]


def test_an_inherited_grant_from_another_folder_is_named_by_its_object_id() -> None:
    refusal, _ = _genie_refusal(_acl(_group("users", ("CAN_RUN", OTHER_DIRECTORY))))

    assert f"CAN_RUN (inherited from {OTHER_DIRECTORY})" in refusal
    assert "which is" not in refusal
    assert genie_grants.INHERITED_GRANT_REMEDY in refusal
    assert genie_grants.DIRECT_GROUP_REMEDY not in refusal


def test_a_direct_users_grant_is_removed_on_the_space_without_a_folder_read() -> None:
    refusal, status_reads = _genie_refusal(_acl(_group("users", ("CAN_RUN", False))))

    assert "group 'users' holds CAN_RUN (direct on the space)" in refusal
    assert genie_grants.DIRECT_GROUP_REMEDY in refusal
    assert genie_grants.INHERITED_GRANT_REMEDY not in refusal
    assert status_reads == []


def test_a_mixed_grant_lists_both_sources_and_both_remedies() -> None:
    refusal, _ = _genie_refusal(
        _acl(_group("users", ("CAN_RUN", False), ("CAN_VIEW", SHARED_DIRECTORY)))
    )

    assert (
        "holds CAN_RUN (direct on the space), CAN_VIEW (inherited from "
        f"{SHARED_DIRECTORY}, which is /Workspace/Shared)"
    ) in refusal
    assert genie_grants.DIRECT_GROUP_REMEDY in refusal
    assert genie_grants.INHERITED_GRANT_REMEDY in refusal


def test_direct_and_inherited_group_grants_on_different_spaces_get_both_remedies() -> None:
    # The shared-workspace shape: some spaces grant 'users' directly, others
    # inherit it from /Workspace/Shared, and no single space holds both. The
    # remedy is chosen across every listed grant, so each kind must survive
    # into the one refusal; a per-kind all() would drop both.
    workspace = _ReadOnlyWorkspace(
        acls={
            **{
                f"direct-{index}": _acl(_group("users", ("CAN_RUN", False)))
                for index in range(3)
            },
            **{
                f"inherited-{index}": _acl(_group("users", ("CAN_MANAGE", SHARED_DIRECTORY)))
                for index in range(3)
            },
        },
        app=_app(),
    )

    refusals, _ = _run(workspace, [])

    assert len(refusals) == 1
    refusal = refusals[0]
    assert refusal.count("Genie space ") == 6
    assert genie_grants.DIRECT_GROUP_REMEDY in refusal
    assert genie_grants.INHERITED_GRANT_REMEDY in refusal
    assert {method for method, _ in workspace.calls} == {"GET"}


def test_direct_and_inherited_verifier_grants_on_different_spaces_get_both_remedies() -> None:
    workspace = _ReadOnlyWorkspace(
        acls={
            "direct": _acl(_principal(VERIFIER_ID, ("CAN_RUN", False))),
            "inherited": _acl(_principal(VERIFIER_ID, ("CAN_VIEW", SHARED_DIRECTORY))),
        },
        app=_app(),
    )

    refusals, _ = _run(workspace, [])

    assert len(refusals) == 1
    refusal = refusals[0]
    assert refusal.count("Genie space ") == 2
    assert genie_grants.DIRECT_VERIFIER_REMEDY in refusal
    assert genie_grants.INHERITED_VERIFIER_REMEDY in refusal


def test_an_inherited_verifier_grant_names_its_folder_and_is_not_sent_to_the_space() -> None:
    refusal, _ = _genie_refusal(_acl(_principal(VERIFIER_ID, ("CAN_VIEW", SHARED_DIRECTORY))))

    assert (
        f"verifier service principal {VERIFIER_ID!r} holds CAN_VIEW (inherited from "
        f"{SHARED_DIRECTORY}, which is /Workspace/Shared)"
    ) in refusal
    assert genie_grants.INHERITED_VERIFIER_REMEDY in refusal
    assert genie_grants.DIRECT_VERIFIER_REMEDY not in refusal
    assert "The verifier needs no Genie access" in refusal


def test_a_missing_shared_folder_only_drops_the_folder_name() -> None:
    refusal, _ = _genie_refusal(
        _acl(_group("users", ("CAN_MANAGE", SHARED_DIRECTORY))),
        shared_folder=NotFound("no /Workspace/Shared"),
    )

    assert f"CAN_MANAGE (inherited from {SHARED_DIRECTORY})" in refusal
    assert "which is" not in refusal


def test_a_shared_folder_read_error_fails_closed() -> None:
    workspace = _ReadOnlyWorkspace(
        acls={"space-one": _acl(_group("users", ("CAN_MANAGE", SHARED_DIRECTORY)))},
        app=_app(),
        shared_folder=PermissionDenied("get-status denied"),
    )

    with pytest.raises(PermissionDenied, match="get-status denied"):
        _run(workspace, [])


def test_the_verifier_grant_check_needs_the_verifier_identity() -> None:
    with pytest.raises(ValueError, match="verifier application ID is required"):
        probes.verifier_genie_grants([], verifier_application_id=" ")


def test_a_clean_workspace_with_an_acknowledged_supervisor_passes() -> None:
    workspace = _ReadOnlyWorkspace(
        acls={"governed": _acl(_group("admins", ("CAN_MANAGE", True)))}, app=_app()
    )

    refusals, notes = _run(workspace, [_human_canonical()], acknowledgement=HUMAN_ID)

    assert refusals == []
    assert any("acknowledges the permanent deletion" in note for note in notes)


def test_main_exits_2_and_reports_without_mutating(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import databricks.sdk

    from tools.databricks import m2m_access_policy, provision_agentic_resources

    workspace = _ReadOnlyWorkspace(
        acls={"space-two": _acl(_group("users", ("CAN_RUN", False)))}, app=_app()
    )
    monkeypatch.setattr(databricks.sdk, "WorkspaceClient", lambda: workspace)
    monkeypatch.setattr(provision_agentic_resources, "_supervisor_agents", _agents_human)
    monkeypatch.setattr(m2m_access_policy, "resolve_effective_groups", _walk({}))
    monkeypatch.delenv(ack.ACKNOWLEDGEMENT_ENV, raising=False)

    rc = probes.main(_main_args())

    err = capsys.readouterr().err
    assert rc == 2
    assert err.count("[mip-prestop] REFUSED:") == 2
    assert "nothing was changed and the App was not stopped by this run" in err


def test_main_passes_once_the_operator_acknowledges_and_grants_are_clean(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import databricks.sdk

    from tools.databricks import m2m_access_policy, provision_agentic_resources

    workspace = _ReadOnlyWorkspace(acls={"governed": _acl()}, app=_app())
    monkeypatch.setattr(databricks.sdk, "WorkspaceClient", lambda: workspace)
    monkeypatch.setattr(provision_agentic_resources, "_supervisor_agents", _agents_human)
    monkeypatch.setattr(m2m_access_policy, "resolve_effective_groups", _walk({}))
    monkeypatch.setenv(ack.ACKNOWLEDGEMENT_ENV, HUMAN_ID)

    assert probes.main(_main_args()) == 0
    assert "pre-stop probes passed (read-only)" in capsys.readouterr().out


def test_main_reads_the_approval_lists_from_the_environment_never_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import databricks.sdk

    from tools.databricks import m2m_access_policy, provision_agentic_resources

    seen: list[Any] = []

    def record(*_args: Any, config: Any, **_kwargs: Any) -> tuple[list[Any], list[str]]:
        seen.append(config)
        return [], []

    workspace = _ReadOnlyWorkspace(acls={"governed": _acl()}, app=_app())
    monkeypatch.setattr(databricks.sdk, "WorkspaceClient", lambda: workspace)
    monkeypatch.setattr(provision_agentic_resources, "_supervisor_agents", _agents_human)
    monkeypatch.setattr(m2m_access_policy, "resolve_effective_groups", _walk({}))
    monkeypatch.setattr(probes, "suite_refusals", record)
    monkeypatch.setenv(ack.ACKNOWLEDGEMENT_ENV, HUMAN_ID)
    monkeypatch.setenv("MIP_UC_APPROVED_OWNER_PRINCIPALS", "mip-data-owners,owner@example.invalid")
    monkeypatch.setenv("MIP_GENIE_MANAGER_PRINCIPALS", "genie-curators")

    assert probes.main(_main_args()) == 0
    assert [(c.approved_owner_principals, c.genie_manager_principals) for c in seen] == [
        ("mip-data-owners,owner@example.invalid", "genie-curators")
    ]
    with pytest.raises(SystemExit) as refused:
        probes.main([*_main_args(), "--approved-owner-principals", "owner@example.invalid"])
    assert refused.value.code == 2, "argv would echo the principals into the deploy transcript"


def test_main_resolves_each_principal_through_the_admin_scim_walk(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import databricks.sdk

    from tools.databricks import m2m_access_policy, provision_agentic_resources

    calls: list[str] = []
    workspace = _runtime_readers_workspace()
    monkeypatch.setattr(databricks.sdk, "WorkspaceClient", lambda: workspace)
    monkeypatch.setattr(provision_agentic_resources, "_supervisor_agents", lambda: [])
    monkeypatch.setattr(
        m2m_access_policy, "resolve_effective_groups", _walk(RUNTIME_ONLY_GROUP, calls)
    )
    monkeypatch.delenv(ack.ACKNOWLEDGEMENT_ENV, raising=False)

    rc = probes.main(_main_args())

    err = capsys.readouterr().err
    assert rc == 2
    assert calls == RESOLVED_SCIM_IDS
    refusals = [line for line in err.splitlines() if line.startswith("[mip-prestop] REFUSED: ")]
    _assert_only_the_runtime_readers_refusal(
        [line.removeprefix("[mip-prestop] REFUSED: ") for line in refusals]
    )


def test_refusals_name_genie_spaces_by_id_never_by_title(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # On a shared workspace the admin sees every team's spaces, and the refusal
    # goes to deploy stderr and CI logs: name a space by its id, never its title.
    import databricks.sdk

    from tools.databricks import m2m_access_policy, provision_agentic_resources

    workspace = _ReadOnlyWorkspace(
        acls={
            "space-one": _acl(_group("users", ("CAN_RUN", False))),
            "space-two": _acl(_principal(VERIFIER_ID, ("CAN_VIEW", SHARED_DIRECTORY))),
        },
        app=_app(),
        titles={"space-one": "Another Team Pipeline", "space-two": "Another Team Ledger"},
    )
    monkeypatch.setattr(databricks.sdk, "WorkspaceClient", lambda: workspace)
    monkeypatch.setattr(provision_agentic_resources, "_supervisor_agents", lambda: [])
    monkeypatch.setattr(m2m_access_policy, "resolve_effective_groups", _walk({}))
    monkeypatch.delenv(ack.ACKNOWLEDGEMENT_ENV, raising=False)

    rc = probes.main(_main_args())

    captured = capsys.readouterr()
    assert rc == 2
    assert "Genie space space-one: group 'users' holds CAN_RUN" in captured.err
    assert "Genie space space-two: verifier service principal" in captured.err
    assert "Another Team" not in captured.out + captured.err


def test_main_refuses_a_non_admin_inventory_principal(monkeypatch: pytest.MonkeyPatch) -> None:
    import databricks.sdk

    workspace = _ReadOnlyWorkspace(acls={}, admin="someone-else@example.invalid")
    monkeypatch.setattr(databricks.sdk, "WorkspaceClient", lambda: workspace)

    with pytest.raises(RuntimeError, match="unexpected principal"):
        probes.main(_main_args())


def _agents_human() -> list[dict[str, str]]:
    return [_human_canonical()]


def _main_args() -> list[str]:
    return [
        "--app-name",
        "mip-app",
        "--expected-inventory-principal",
        "deployer@example.invalid",
        "--runtime-application-id",
        RUNTIME_ID,
        "--verifier-application-id",
        VERIFIER_ID,
        "--supervisor-name",
        CANONICAL,
        "--rollback-scope",
        "mip-app-rollback",
        "--lakebase-instance",
        "mip-app-state",
        "--catalog",
        "mip",
        "--genie-space-name",
        "Mortgage Lead Intelligence",
        "--rebase-unverified-app",
        "0",
    ]


# --- Deploy orchestration -----------------------------------------------------

PROBE_CALL = (
    "  run_with_account_identity \\\n"
    '    "$PYTHON" -m tools.databricks.deploy_prestop_probes \\\n'
)


def test_the_probe_runs_read_only_before_the_lease_and_every_app_stop() -> None:
    script = deploy_entrypoint_text()
    assert script.count("tools.databricks.deploy_prestop_probes") == 1
    # Lifecycle libraries define (not run) stop/rollback helpers ahead of the
    # steps, so every ordering below is measured from the first step onward.
    steps = script.index('step "preflight — check .env.local, databricks CLI, venv"')

    m2m_gate = script.index("missing required per-run M2M credential(s)", steps)
    distinct = script.index("must be pairwise distinct", steps)
    probe = script.index(PROBE_CALL, steps)
    lease_root = script.index("tools.databricks.app_deployment_lease recovery-root", steps)
    rollback_scope = script.index("tools.databricks.app_rollback_secret_scope ensure", steps)
    foreign_uc = script.index("tools.databricks.audit_agent_runtime_foreign_uc_access", steps)
    first_stop = script.index("tools.databricks.stop_app_fail_closed", steps)
    quarantine = script.index("--mode quarantine", steps)
    signed_blue = script.index("tools.databricks.app_deployment_rollback ensure", steps)
    # The EXIT trap stops the App on any failure once this is armed; a probe
    # refusal must exit while it is still 0, or refusing would stop the App.
    first_arm = script.index("APP_FAIL_CLOSED_ARMED=1", steps)

    assert m2m_gate < distinct < probe < lease_root < rollback_scope < foreign_uc
    assert probe < first_stop and probe < quarantine and probe < signed_blue
    assert probe < first_arm
    assert "APP_FAIL_CLOSED_ARMED=1" not in script[:steps]


def test_the_probe_runs_after_deployer_auth_and_the_runtime_secret_check() -> None:
    # The probe reads as the workspace-admin deployer, so the deployment
    # workspace auth must be bound first. Step 0's runtime-secret plan stays the
    # first pre-stop refusal in the preflight tail; the probe follows it, and
    # nothing between the first step and the probe stops or quarantines the App.
    script = deploy_entrypoint_text()
    steps = script.index('step "preflight — check .env.local, databricks CLI, venv"')
    bind = script.index("\nbind_deployment_workspace_auth\n", steps)
    secret_check = script.index("tools.databricks.provision_runtime_secrets --check", steps)
    probe = script.index(PROBE_CALL, steps)

    assert steps < bind < secret_check < probe
    assert script.count("tools.databricks.provision_runtime_secrets --check") == 2
    assert script.rindex("tools.databricks.provision_runtime_secrets --check") < probe
    before_probe = script[steps:probe]
    for app_stop in (
        "stop_app_fail_closed",
        "--stop-app-for-mutation",
        "--mode quarantine",
        "apps stop",
        "APP_FAIL_CLOSED_ARMED=1",
    ):
        assert app_stop not in before_probe, app_stop
    # One bounded authority only: the owner policy resolves owners in account
    # SCIM (M0-PR5), so the probe gets the account credential, never the
    # proof-signing key, and never runs as an M2M identity.
    block = script[probe : script.index("\n  # The signed lease", probe)]
    call = block[: block.index("# >>> pii-salt pre-stop check >>>")]
    assert call.count("run_with_account_identity") == 1
    assert "run_with_proof_signing_authority" not in call
    assert "run_as_m2m_identity" not in call and "run_with_lakebase" not in call
    for argument in (
        '--app-name "$_GRANTS_APP_NAME"',
        '--expected-inventory-principal "$DEPLOY_INVENTORY_PRINCIPAL"',
        '--runtime-application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID"',
        '--verifier-application-id "$DATABRICKS_VERIFIER_CLIENT_ID"',
        '--supervisor-name "${MIP_AGENT_SUPERVISOR_NAME:-Mortgage Growth Agent}"',
        '--rollback-scope "$APP_ROLLBACK_SECRET_SCOPE"',
        '--lakebase-instance "$MIP_LAKEBASE_INSTANCE"',
        '--catalog "${MIP_DEFAULT_CATALOG:-mip}"',
        '--genie-space-name "$MIP_GENIE_SPACE_NAME"',
        '--rebase-unverified-app "$_PRESTOP_REBASE_UNVERIFIED_APP"',
    ):
        assert argument in call, argument
    # The approval lists name principals (IDENTIFYING in the client profile),
    # and run_with_account_identity echoes argv, so they reach the probe only
    # through the exported environment, the way the post-stop converger reads
    # MIP_UC_APPROVED_OWNER_PRINCIPALS.
    for name in ("MIP_UC_APPROVED_OWNER_PRINCIPALS", "MIP_GENIE_MANAGER_PRINCIPALS"):
        assert name not in call, name
        before = script[:probe]
        assert f"export {name}" in before or f"export {name}=" in before, name


def test_dry_run_names_the_probe_without_running_it() -> None:
    text = AUTOMATION_STEP.read_text(encoding="utf-8")
    dry_run = text[text.index('export MIP_APP_DEPLOYMENT_LEASE_ID="dry-run-deployment-lease"') :]

    assert 'step "pre-stop probes:' in dry_run
    assert "deploy_prestop_probes" not in dry_run


def test_ci_never_acknowledges_a_supervisor_deletion() -> None:
    assert "MIP_RETIRE_HUMAN_SUPERVISOR" not in DEPLOY_DEV.read_text(encoding="utf-8")
    # CI runs the same probe, so its pre-stop refusals are documented as expected.
    flat = " ".join(DEPLOYMENT_DOC.read_text(encoding="utf-8").split())
    assert (
        "The `deploy-dev` workflow runs the same probe and never sets "
        "`MIP_RETIRE_HUMAN_SUPERVISOR`, so expect a CI deploy to refuse here, before step 0a"
    ) in flat


def test_the_operator_contract_is_documented() -> None:
    doc = DEPLOYMENT_DOC.read_text(encoding="utf-8")

    assert "### Pre-stop probes" in doc
    assert "tools/databricks/deploy_prestop_probes.py" in doc
    assert "MIP_RETIRE_HUMAN_SUPERVISOR=<supervisor id>" in doc
    assert "before step 0a" in doc
    # An inherited grant is never sent back to the space, and the shared
    # folder's blast radius is spelled out before anyone edits its ACL.
    assert "An inherited grant cannot be removed on the space." in doc
    assert "affects every workspace user's\n    shared content" in doc
    assert "on the space or on the folder it inherits from" not in doc
    assert "names each space by\n  its id only, never by its title" in doc
    # The orphan endpoint of an already-deleted Supervisor needs the same
    # acknowledgement; the doc once promised the opposite.
    flat = " ".join(doc.split())
    assert "an endpoint the agent-runtime service principal did not create is never deleted unacknowledged" in flat
    assert "including one that only deletes the orphan endpoint" in flat
    assert "orphan endpoint after the Supervisor is gone needs no acknowledgement" not in flat
    assert "A `MIP_AGENT_SUPERVISOR_NAME` with leading or trailing whitespace is refused" in flat
    # The retirement step's re-check guards the Supervisor and its endpoint,
    # not the old-Gateway step, which runs first on every path (post-W5 review).
    assert "checks it once more before its first Supervisor or serving-endpoint mutation" in flat
    assert "On all three paths the pinned old-Gateway step runs before that check" in flat
    assert "deletes the Gateway only when the agent-runtime service principal created it" in flat
    assert "checks it once more before its first mutation" not in flat


def test_the_runbook_go_list_points_at_the_pre_stop_probe() -> None:
    # The scheduled-refresh GO list kept the Genie grant and the human-created
    # Supervisor as manual owner pre-checks that failed after the App stop;
    # with the probe they refuse before it, and the remedy follows the source.
    runbook = " ".join((REPO / "docs" / "runbook.md").read_text(encoding="utf-8").split())

    assert "The runtime and verifier audits refuse it at cutover, after the App is stopped." not in runbook
    assert "Step 0's read-only pre-stop probe lists every such grant" in runbook
    assert "inherited from `/Workspace/Shared`" in runbook
    assert (
        "Accept that by exporting `MIP_RETIRE_HUMAN_SUPERVISOR=<supervisor id>` in the "
        "operator shell for the run"
    ) in runbook
    assert "Without either, step 0's pre-stop probe refuses before the App stop" in runbook


def _heading_indexes(lines: list[str]) -> list[int]:
    fenced = False
    headings: list[int] = []
    for index, line in enumerate(lines):
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and re.match(r"#{1,6} ", line):
            headings.append(index)
    return headings


def test_the_probe_section_does_not_reparent_unrelated_deployment_content() -> None:
    # A heading dropped into the middle of the blue/green Supervisor paragraph
    # once glued its closing sentence to the Ed25519 attestation text and moved
    # every later deployment section under "Pre-stop probes". The string checks
    # above cannot see that, so pin the structure itself.
    text = DEPLOYMENT_DOC.read_text(encoding="utf-8")
    lines = text.splitlines()
    headings = _heading_indexes(lines)
    start = next(i for i in headings if lines[i].startswith("### Pre-stop probes"))
    end = next((i for i in headings if i > start), len(lines))
    section = "\n".join(lines[start + 1 : end])

    assert end < len(lines) and re.match(r"#{2,3} ", lines[end]), "probe section has children"
    assert lines[end - 1] == "", "probe section runs into the next heading"
    for foreign in ("Ed25519", "proxy model version"):
        assert foreign not in section
    blue_green = next(
        paragraph
        for paragraph in text.split("\n\n")
        if "a changed old identity stop the cutover" in paragraph
    )
    assert "MIP_RETIRE_HUMAN_SUPERVISOR=<supervisor id>" in blue_green
    assert "Each immutable proxy model version also carries" in blue_green
    assert "### Pre-stop probes" not in blue_green


def _harness(tmp_path: Path, *, probe_rc: int) -> tuple[subprocess.CompletedProcess[str], str]:
    calls = tmp_path / "calls.log"
    fake_python = tmp_path / "fake-python.sh"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(calls))}\n"
        'case "$*" in\n'
        "  *'agent_proxy_credential_bundle all-fields'*)\n"
        "    printf 'proxy-client\\tproxy-credential\\tproxy-secret\\n'; exit 0 ;;\n"
        f"  *deploy_prestop_probes*) exit {probe_rc} ;;\n"
        "esac\n"
        "cat >/dev/null\n"
        "printf 'admin-client,release-probe-client\\n'\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    harness = tmp_path / "harness.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
cd {shlex.quote(str(REPO))}
export TMPDIR={shlex.quote(str(tmp_path))}
DRY_RUN=0
PYTHON={shlex.quote(str(fake_python))}
RED=""; RST=""
step() {{ echo "[step] $*"; }}
run() {{ "$@"; }}
run_with_proof_signing_authority() {{ "$@"; }}
run_with_account_identity() {{ "$@"; }}
resolve_m2m_credential() {{ :; }}
deployment_control_value() {{ printf ''; }}
same_identity_casefold() {{ [[ "$1" == "$2" ]]; }}
start_proof_signing_heartbeat() {{ :; }}
# The step file renders, validates and runs the pre-lease plan gate (M0-PR4).
dotenv_value() {{ printf ''; }}
run_bundle_plan_gate() {{ printf 'run_bundle_plan_gate %s\n' "$*" >> {shlex.quote(str(calls))}; }}
TARGET=client
# The client profile's install class and demo-feed policy, which
# resolve_client_profile sets at the top of the preflight step file (M1-PR2,
# M1-PR3); the render's demo-feed switch reads the policy.
CLIENT_INSTALL_CLASS=demo
CLIENT_DEMO_FEEDS=1
DEPLOY_PHASE=""
DEPLOY_INVENTORY_PRINCIPAL=deployer@example.invalid
# Resolved by the preflight step file; the pii-salt check reads both.
MIP_PII_SALT_SCOPE=mip
PII_SALT_REQUIRE_EXISTING=0
APP_ROLLBACK_SECRET_SCOPE=mip-app-rollback
MIP_LAKEBASE_INSTANCE=mip-app-state
MIP_GENIE_SPACE_NAME="Mortgage Lead Intelligence"
DATABRICKS_CLIENT_ID=normal-client DATABRICKS_CLIENT_SECRET=s1
DATABRICKS_OPERATOR2_CLIENT_ID=operator2-client
DATABRICKS_ADMIN_CLIENT_ID=admin-client DATABRICKS_ADMIN_CLIENT_SECRET=s2
DATABRICKS_RELEASE_PROBE_CLIENT_ID=release-probe-client
DATABRICKS_RELEASE_PROBE_CLIENT_SECRET=s3
DATABRICKS_VERIFIER_CLIENT_ID=verifier-client DATABRICKS_VERIFIER_CLIENT_SECRET=s4
DATABRICKS_AGENT_RUNTIME_CLIENT_ID=runtime-client
DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET=s5
DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE=bundle
DATABRICKS_ACCOUNT_HOST=https://accounts.cloud.databricks.com
DATABRICKS_ACCOUNT_ID=account
DATABRICKS_ACCOUNT_CLIENT_ID=account-client DATABRICKS_ACCOUNT_CLIENT_SECRET=s6
source {shlex.quote(str(AUTOMATION_STEP))}
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(harness)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )
    return result, calls.read_text(encoding="utf-8") if calls.exists() else ""


def test_a_probe_refusal_stops_the_deploy_before_the_lease(tmp_path: Path) -> None:
    result, calls = _harness(tmp_path, probe_rc=2)

    assert result.returncode == 2, result.stderr
    assert "[step] pre-stop probes:" in result.stdout
    assert "tools.databricks.deploy_prestop_probes --app-name mip-app" in calls
    assert "app_deployment_lease" not in calls
    assert "run_bundle_plan_gate" not in calls
    assert "stop_app_fail_closed" not in calls


def test_a_passing_probe_hands_over_to_the_signed_lease(tmp_path: Path) -> None:
    _result, calls = _harness(tmp_path, probe_rc=0)

    probe = calls.index("tools.databricks.deploy_prestop_probes")
    salt_check = calls.index("tools.databricks.pii_salt_scope check --scope mip --require-existing 0")
    gate = calls.index("run_bundle_plan_gate pre-lease")
    lease = calls.index("tools.databricks.app_deployment_lease recovery-root")
    assert probe < salt_check < gate < lease
