"""The App's metastore privileges are proven, never read as "none" (spike S0(b′)).

Measured on 2026-11-11 as a non-admin service principal: another principal's
``get_effective`` on the metastore is HTTP 403, and an unfiltered one answers
only the caller's own view. Once the deployer reads the metastore through its
summary (S0(b)), the treatment converger, its read-only ``--check`` (which
adopt-live's treatment fact runs) and the pre-stop owner probe would reach
that read as a deployer that is not a metastore admin. These tests pin the
guard (judge R1): each of the four refuses, value-free and before the read,
unless the deployer is a metastore admin (a live read) or the metastore owner's
exact attestation is given; a fake that answers the fail-open shape (an empty
listing) never gets asked. Positive controls: an admin deployer, and a valid
attestation, pass on every surface.
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.errors import InternalError, PermissionDenied

from tests.fixtures.prestop_estate import (
    APP_CLIENT,
    DEPLOYER,
    FakeAccount,
    FakeWorkspace,
    clean_estate,
)
from tests.unit import test_converge_campaign_treatment_access as harness
from tools.clients import dispatch_acks
from tools.clients.errors import ProfileRefusal
from tools.databricks import adopt_live_facts
from tools.databricks import metastore_app_grants as grants
from tools.databricks import prestop_owner_policy as probe
from tools.databricks.campaign_treatment_access_check import check_campaign_treatment_access
from tools.databricks.converge_campaign_treatment_access import (
    converge_campaign_treatment_access as converge,
)
from tools.databricks.estate import inventory as estate_inventory
from tools.databricks.prestop_refusals import METASTORE_AUTHORITY, OWNER_POLICY

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
GROUP = "Customer-Metastore-Admins"
METASTORE = "metastore-1"
APP = "app-client"
# Identifiers of the fake estates: none may appear in a refusal or a printed line.
IDENTIFIERS = (METASTORE, APP, GROUP, APP_CLIENT, DEPLOYER, "metastore-admins")


@pytest.fixture(autouse=True)
def _no_ack(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(grants.ACK_ENV, raising=False)


def _ack(
    *,
    metastore_id: str = METASTORE,
    owner: str = GROUP,
    app: str = APP,
    privileges: frozenset[str] = frozenset({"USE_MARKETPLACE_ASSETS"}),
    day: date | None = None,
) -> str:
    return grants.attestation_digest(
        metastore_id=metastore_id,
        metastore_owner=owner,
        app_application_id=app,
        privileges=privileges,
        attested_on=day or datetime.now(UTC).date(),
    )


def _value_free(text: str) -> None:
    for value in IDENTIFIERS:
        assert value not in text, (value, text)


# ------------------------------------------------------------------ the attestation


def test_the_digest_binds_every_field_and_its_purpose() -> None:
    base = dict(
        metastore_id=METASTORE,
        metastore_owner=GROUP,
        app_application_id=APP,
        privileges={"USE_MARKETPLACE_ASSETS"},
        attested_on=NOW.date(),
    )
    digest = grants.attestation_digest(**base)
    assert grants.attestation_body(**base)["purpose"] == "metastore_app_grants"
    for field, other in (
        ("metastore_id", "metastore-2"),
        ("metastore_owner", "someone-else"),
        ("app_application_id", "another-app"),
        ("privileges", set()),
        ("attested_on", NOW.date() - timedelta(days=1)),
    ):
        assert grants.attestation_digest(**{**base, field: other}) != digest, field
    # Domain-separated: never the bare canonical body's sha256.
    import hashlib

    from tools.databricks.bundle_plan_shape import canonical_json

    assert digest != hashlib.sha256(canonical_json(grants.attestation_body(**base)).encode()).hexdigest()


def test_the_age_rule_is_the_estate_inventorys_judged_at_the_runs_end() -> None:
    # The pre-stop probe's rule: the run's end, now plus the run's margin.
    dates = grants.admissible_dates(NOW, margin=grants.RUN_MARGIN)
    assert dates[0] == NOW.date() and len(dates) == 29
    assert dates[-1] == NOW.date() - timedelta(days=28)
    # Exactly at the last start the rule admits, and one second after.
    day = date(2026, 10, 1)
    last = grants.last_run_start(day)
    assert last == datetime(2026, 10, 30, tzinfo=UTC)
    assert day in grants.admissible_dates(last, margin=grants.RUN_MARGIN)
    assert day not in grants.admissible_dates(last + timedelta(seconds=1), margin=grants.RUN_MARGIN)
    # Every check after the lease judges its own moment (the estate inventory's
    # post-stop load, margin 0): the same day stays admitted to the run's end.
    assert grants.RUN_MARGIN == estate_inventory.RUN_MARGIN
    assert len(grants.admissible_dates(NOW)) == 30
    assert day in grants.admissible_dates(last + grants.RUN_MARGIN)
    assert day not in grants.admissible_dates(last + grants.RUN_MARGIN + timedelta(seconds=1))


@pytest.mark.parametrize("privileges", [frozenset(), frozenset({"USE_MARKETPLACE_ASSETS"})])
def test_an_exact_attestation_of_a_reviewed_set_is_accepted(privileges: frozenset[str]) -> None:
    ack = _ack(privileges=privileges, day=NOW.date() - timedelta(days=3))

    assert (
        grants.attested_privileges(
            ack=ack, metastore_id=METASTORE, metastore_owner=GROUP, app_application_id=APP, now=NOW
        )
        == privileges
    )


@pytest.mark.parametrize(
    "ack",
    [
        _ack(metastore_id="metastore-2", day=NOW.date()),
        _ack(owner="someone-else", day=NOW.date()),
        _ack(app="another-app", day=NOW.date()),
        _ack(privileges=frozenset({"USE_MARKETPLACE_ASSETS", "CREATE_CATALOG"}), day=NOW.date()),
        _ack(day=NOW.date() - timedelta(days=29)),
        _ack(day=NOW.date() + timedelta(days=1)),
        dispatch_acks.profile_ack("acme", "a" * 64),
    ],
    ids=["another-metastore", "another-owner", "another-app", "forbidden-set", "too-old", "future", "other-purpose"],
)
def test_any_other_attestation_refuses(ack: str) -> None:
    # Judged as the pre-stop probe judges it, at the run's end.
    with pytest.raises(grants.MetastoreAuthorityUnproven, match="attests no reviewed privilege set") as refused:
        grants.attested_privileges(
            ack=ack,
            metastore_id=METASTORE,
            metastore_owner=GROUP,
            app_application_id=APP,
            now=NOW,
            margin=grants.RUN_MARGIN,
        )
    _value_free(str(refused.value))


@pytest.mark.parametrize(
    "ack",
    [
        _ack(metastore_id="metastore-2", day=NOW.date()),
        _ack(owner="someone-else", day=NOW.date()),
        _ack(app="another-app", day=NOW.date()),
        _ack(privileges=frozenset({"USE_MARKETPLACE_ASSETS", "CREATE_CATALOG"}), day=NOW.date()),
        _ack(day=NOW.date() - timedelta(days=30)),
        _ack(day=NOW.date() + timedelta(days=1)),
        dispatch_acks.profile_ack("acme", "a" * 64),
    ],
    ids=["another-metastore", "another-owner", "another-app", "forbidden-set", "too-old", "future", "other-purpose"],
)
def test_any_other_attestation_refuses_after_the_lease_too(ack: str) -> None:
    # The converger's own moment (margin 0) admits more dates, never another body.
    with pytest.raises(grants.MetastoreAuthorityUnproven, match="attests no reviewed privilege set") as refused:
        grants.attested_privileges(
            ack=ack, metastore_id=METASTORE, metastore_owner=GROUP, app_application_id=APP, now=NOW
        )
    _value_free(str(refused.value))


@pytest.mark.parametrize("ack", ["short", "A" * 64, "g" * 64, f"metastore_app_grants={'a' * 64}"])
def test_a_malformed_ack_refuses(ack: str) -> None:
    with pytest.raises(grants.MetastoreAuthorityUnproven, match="64-lowercase-hex sha256"):
        grants.attested_privileges(
            ack=ack, metastore_id=METASTORE, metastore_owner=GROUP, app_application_id=APP, now=NOW
        )


# ------------------------------------------------------------------ the proof


class _Reads:
    """A recording ``effective`` reader: what the live read would answer."""

    def __init__(self, answer: set[str] | BaseException) -> None:
        self.answer = answer
        self.calls: list[dict[str, Any]] = []

    def __call__(self, _workspace: Any, **kwargs: Any) -> set[str]:
        self.calls.append(kwargs)
        if isinstance(self.answer, BaseException):
            raise self.answer
        return set(self.answer)


def _metastore(owner: object = GROUP) -> object:
    return SimpleNamespace(metastore_id=METASTORE, owner=owner)


def _me(*groups: str, user_name: str = "deployer") -> object:
    me = SimpleNamespace(user_name=user_name, application_id="", groups=[SimpleNamespace(display=g) for g in groups])
    return SimpleNamespace(current_user=SimpleNamespace(me=lambda: me))


def _prove(workspace: object, reads: _Reads, **env: str) -> grants.AppMetastorePrivileges:
    return grants.app_metastore_privileges(
        workspace, metastore=_metastore(), principal=APP, effective=reads, environ=env, now=NOW
    )


def test_an_admin_deployer_reads_the_set_live_with_its_principal() -> None:
    reads = _Reads({"USE_MARKETPLACE_ASSETS"})

    proof = _prove(_me(GROUP), reads)

    assert proof == grants.AppMetastorePrivileges(frozenset({"USE_MARKETPLACE_ASSETS"}), grants.LIVE_ROUTE)
    assert reads.calls == [{"securable_type": "metastore", "full_name": METASTORE, "principal": APP}]


def test_a_denied_live_read_refuses_and_is_never_an_empty_set() -> None:
    with pytest.raises(grants.MetastoreAuthorityUnproven, match="never an empty set") as refused:
        _prove(_me(GROUP), _Reads(PermissionDenied("PERMISSION_DENIED")))
    _value_free(str(refused.value))


def test_a_non_admin_deployer_without_the_attestation_refuses_before_any_read() -> None:
    reads = _Reads(set())  # the fail-open shape: it is never asked

    with pytest.raises(grants.MetastoreAuthorityUnproven, match="not a metastore admin") as refused:
        _prove(_me(), reads)

    assert reads.calls == []
    assert "spike S0(b′)" in str(refused.value) and grants.ACK_ENV in str(refused.value)
    _value_free(str(refused.value))


def test_a_non_admin_deployer_with_the_owners_attestation_passes_without_a_read() -> None:
    reads = _Reads(set())

    proof = _prove(_me(), reads, **{grants.ACK_ENV: _ack(day=NOW.date())})

    assert proof == grants.AppMetastorePrivileges(frozenset({"USE_MARKETPLACE_ASSETS"}), grants.ATTESTED_ROUTE)
    assert reads.calls == []


def test_a_given_ack_is_never_ignored() -> None:
    # A wrong ack refuses even for an admin deployer...
    with pytest.raises(grants.MetastoreAuthorityUnproven, match="attests no reviewed privilege set"):
        _prove(_me(GROUP), _Reads({"USE_MARKETPLACE_ASSETS"}), **{grants.ACK_ENV: _ack(app="another-app")})
    # ...and a valid one must equal the live set.
    with pytest.raises(grants.MetastoreAuthorityUnproven, match="differ from the owner's attestation"):
        _prove(_me(GROUP), _Reads(set()), **{grants.ACK_ENV: _ack(day=NOW.date())})
    assert _prove(_me(GROUP), _Reads({"USE_MARKETPLACE_ASSETS"}), **{grants.ACK_ENV: _ack(day=NOW.date())})


@pytest.mark.parametrize("owner", [None, "", " padded"])
def test_an_unreadable_owner_refuses_on_both_routes(owner: object) -> None:
    for workspace, env in ((_me(GROUP), {}), (_me(), {grants.ACK_ENV: _ack(day=NOW.date())})):
        with pytest.raises(grants.MetastoreAuthorityUnproven, match="no canonical owner"):
            grants.app_metastore_privileges(
                workspace, metastore=_metastore(owner), principal=APP, effective=_Reads(set()), environ=env, now=NOW
            )


# ------------------------------------------------- R1: every surface refuses, admin passes


def _converger_workspace(*, admin: bool) -> tuple[Any, Any, Any]:
    workspace, execution, recorded = harness._workspace(
        metastore_owner=GROUP, owner_group=GROUP, current_groups=(GROUP,) if admin else ()
    )
    original = recorded.get_effective

    def fail_open(securable_type: str, full_name: str, **kwargs: Any) -> object:
        if securable_type == "metastore" and not admin:
            # What a non-admin's read must never be taken for: an empty listing.
            recorded.calls.append((securable_type, full_name, kwargs.get("page_token")))
            return SimpleNamespace(privilege_assignments=[], next_page_token="")
        return original(securable_type, full_name, **kwargs)

    workspace.grants.get_effective = fail_open
    return workspace, execution, recorded


def _converge(workspace: Any) -> bool:
    return converge(
        warehouse_id="warehouse-1",
        catalog="mip",
        principal=APP,
        mode="quiesce",
        workspace=workspace,
        metastore_owner=GROUP,
        account_factory=lambda: harness._mirrored_account(workspace),
        assert_single_writer=lambda: None,
        group_membership_probe=lambda *_args: False,
    )


def _metastore_reads(recorded: Any) -> list[tuple[str, str, str | None]]:
    return [call for call in recorded.calls if call[0] == "metastore"]


def test_the_converger_refuses_a_non_admin_deployer_before_any_grant() -> None:
    workspace, execution, recorded = _converger_workspace(admin=False)

    with pytest.raises(grants.MetastoreAuthorityUnproven, match="not a metastore admin") as refused:
        _converge(workspace)

    assert execution.statements == [] and _metastore_reads(recorded) == []
    _value_free(str(refused.value))


def test_the_converger_accepts_the_attestation_or_an_admin_deployer(monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, execution, recorded = _converger_workspace(admin=True)
    assert _converge(workspace) and execution.table_actions == ["SELECT"]
    assert _metastore_reads(recorded) == [("metastore", METASTORE, None)]

    monkeypatch.setenv(grants.ACK_ENV, _ack())
    workspace, execution, recorded = _converger_workspace(admin=False)
    assert _converge(workspace) and execution.table_actions == ["SELECT"]
    assert _metastore_reads(recorded) == []


def test_the_read_only_check_and_adopt_live_refuse_a_non_admin_deployer(monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, _execution, recorded = _converger_workspace(admin=False)

    with pytest.raises(grants.MetastoreAuthorityUnproven, match="not a metastore admin"):
        check_campaign_treatment_access(catalog="mip", principal=APP, mode="quiesce", workspace=workspace)
    readers = SimpleNamespace(treatment=adopt_live_facts.treatment_reader(workspace, "mip"))
    with pytest.raises(adopt_live_facts.AdoptLiveRefused) as refused:
        adopt_live_facts._treatment_mode(readers, APP)  # type: ignore[arg-type]

    assert refused.value.code == "treatment_unverified"
    assert "cannot prove the App's metastore privileges" in str(refused.value)
    assert "spike S0(b′)" in str(refused.value)
    _value_free(str(refused.value))
    assert _metastore_reads(recorded) == []


@pytest.mark.parametrize("route", ["admin", "attestation"])
def test_the_read_only_check_and_adopt_live_pass_a_proven_set(route: str, monkeypatch: pytest.MonkeyPatch) -> None:
    if route == "attestation":
        monkeypatch.setenv(grants.ACK_ENV, _ack())
    workspace, execution, _recorded = _converger_workspace(admin=route == "admin")
    execution.direct.update(catalog={"USE_CATALOG"}, schema={"USE_SCHEMA"}, table={"SELECT"})

    assert check_campaign_treatment_access(catalog="mip", principal=APP, mode="quiesce", workspace=workspace) == []
    readers = SimpleNamespace(treatment=adopt_live_facts.treatment_reader(workspace, "mip"))
    assert adopt_live_facts._treatment_mode(readers, APP) == "quiesce"  # type: ignore[arg-type]


def _prestop(estate: dict[str, Any], *, metastore_owner: str) -> tuple[list[Any], list[str], FakeWorkspace]:
    workspace = FakeWorkspace(estate)
    refusals, notes = probe.owner_policy_refusals(
        workspace,
        app_client_id=APP_CLIENT,
        catalog="mip",
        approved_owner_principals=("mip-data-owners",),
        app_groups=frozenset({"users"}),
        metastore_owner=metastore_owner,
        account_factory=lambda: FakeAccount(estate),
    )
    return refusals, notes, workspace


def _group_owned_estate(*, deployer_in_group: bool) -> dict[str, Any]:
    estate = clean_estate()
    estate["metastore"]["owner"] = "metastore-admins"
    estate["principals"]["metastore-admins"] = {
        "kind": "group",
        "id": "group-metastore-admins",
        "members": ["deployer-id"] if deployer_in_group else [],
    }
    estate["effective_fail_open"] = True
    return estate


def test_the_prestop_probe_refuses_a_non_admin_deployer_before_the_stop() -> None:
    refusals, _notes, workspace = _prestop(_group_owned_estate(deployer_in_group=False), metastore_owner="metastore-admins")

    assert [refusal.code for refusal in refusals] == [METASTORE_AUTHORITY]
    assert "not a metastore admin" in refusals[0].message and "spike S0(b′)" in refusals[0].message
    assert "while the App still serves" in refusals[0].message
    _value_free(refusals[0].message)
    assert not any(call.startswith("GET effective-grants/metastore") for call in workspace.estate["calls"])


def test_the_prestop_probe_passes_an_admin_deployer_and_an_attestation(monkeypatch: pytest.MonkeyPatch) -> None:
    refusals, notes, workspace = _prestop(_group_owned_estate(deployer_in_group=True), metastore_owner="metastore-admins")
    assert refusals == []
    assert notes[-1] == f"the App holds no metastore privilege outside the reviewed set ({grants.LIVE_ROUTE})"
    assert "GET effective-grants/metastore/metastore-1" in workspace.estate["calls"]

    monkeypatch.setenv(grants.ACK_ENV, _ack(owner="metastore-admins", app=APP_CLIENT))
    refusals, notes, workspace = _prestop(_group_owned_estate(deployer_in_group=False), metastore_owner="metastore-admins")
    assert refusals == []
    assert notes[-1] == f"the App holds no metastore privilege outside the reviewed set ({grants.ATTESTED_ROUTE})"
    assert not any(call.startswith("GET effective-grants/metastore") for call in workspace.estate["calls"])


def test_the_prestop_probe_refuses_forbidden_privileges_and_a_denied_read() -> None:
    estate = clean_estate()
    estate["effective"] = {
        "metastore/metastore-1": [{"principal": APP_CLIENT, "privileges": ["CREATE_CATALOG"]}]
    }
    refusals, _notes, _workspace = _prestop(estate, metastore_owner=DEPLOYER)
    assert [refusal.code for refusal in refusals] == [METASTORE_AUTHORITY]
    assert "forbidden metastore privileges ['CREATE_CATALOG']" in refusals[0].message

    # An admin by its /Me groups whose read is still denied (a stale projection): refused.
    estate = _group_owned_estate(deployer_in_group=True)
    workspace = FakeWorkspace(estate)
    workspace.grants.get_effective = lambda *_a, **_kw: (_ for _ in ()).throw(PermissionDenied("denied"))
    refusals, _notes = probe.owner_policy_refusals(
        workspace,
        app_client_id=APP_CLIENT,
        catalog="mip",
        approved_owner_principals=("mip-data-owners",),
        app_groups=frozenset({"users"}),
        metastore_owner="metastore-admins",
        account_factory=lambda: FakeAccount(estate),
    )
    assert [refusal.code for refusal in refusals] == [METASTORE_AUTHORITY]
    assert "never an empty set" in refusals[0].message


@pytest.mark.parametrize(
    "unreadable",
    ["sdk-error", "repeated-page-token", "empty-privilege"],
)
def test_a_metastore_grant_list_the_probe_cannot_read_refuses_before_the_stop(unreadable: str) -> None:
    # The pre-stop probe's catch-all: an admin deployer's read that fails other
    # than PERMISSION_DENIED refuses, by class only, while the App still serves;
    # the converger would raise the same error after the lease.
    estate = _group_owned_estate(deployer_in_group=True)
    workspace = FakeWorkspace(estate)
    reads: list[str | None] = []

    def failing(_securable_type: str, _full_name: str, **kwargs: Any) -> object:
        reads.append(kwargs.get("page_token"))
        if unreadable == "sdk-error":
            raise InternalError(f"boom: the effective grants of {APP_CLIENT} on {METASTORE} by {DEPLOYER}")
        entry = SimpleNamespace(privilege="" if unreadable == "empty-privilege" else "USE_MARKETPLACE_ASSETS")
        assignment = SimpleNamespace(principal="account users", privileges=[entry])
        return SimpleNamespace(privilege_assignments=[assignment], next_page_token="page-2")

    workspace.grants.get_effective = failing
    refusals, notes = probe.owner_policy_refusals(
        workspace,
        app_client_id=APP_CLIENT,
        catalog="mip",
        approved_owner_principals=("mip-data-owners",),
        app_groups=frozenset({"users"}),
        metastore_owner="metastore-admins",
        account_factory=lambda: FakeAccount(estate),
    )

    assert [refusal.code for refusal in refusals] == [METASTORE_AUTHORITY], notes
    message = refusals[0].message
    expected = "InternalError" if unreadable == "sdk-error" else "RuntimeError"
    assert f"the App's metastore privileges could not be read before step 0a ({expected})" in message
    assert "rather than read an unreadable grant list as empty" in message
    for text in ("boom", "pagination", "empty privilege"):
        assert text not in message, (text, message)
    _value_free(message)
    assert reads, "the live read was asked"
    # The control: the same admin deployer with a readable list passes.
    refusals, _notes, _workspace = _prestop(_group_owned_estate(deployer_in_group=True), metastore_owner="metastore-admins")
    assert refusals == []


def _freeze(monkeypatch: pytest.MonkeyPatch, moment: datetime) -> None:
    """The proof's clock reads ``moment``: what each surface judges when it runs."""

    class _Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            return moment.astimezone(tz or UTC)

    monkeypatch.setattr(grants, "datetime", _Clock)


def test_an_ack_the_prestop_probe_admits_outlives_the_run_after_the_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    # Only the pre-stop probe adds the run's margin (the estate inventory's
    # pre-lease rule). An ack it admits must still be admitted by the same
    # run's treatment converger and --check after the lease (step 0a's quiesce,
    # step 4b, the EXIT trap's restore), or the refusal moves past the App stop.
    day = date(2026, 11, 1)
    last = grants.last_run_start(day)
    assert last == datetime(2026, 11, 30, tzinfo=UTC)

    monkeypatch.setenv(grants.ACK_ENV, _ack(owner="metastore-admins", app=APP_CLIENT, day=day))
    _freeze(monkeypatch, last - timedelta(minutes=1))
    refusals, notes, _workspace = _prestop(_group_owned_estate(deployer_in_group=False), metastore_owner="metastore-admins")
    assert refusals == []
    assert notes[-1] == f"the App holds no metastore privilege outside the reviewed set ({grants.ATTESTED_ROUTE})"
    # One second past the printed last start, the run refuses before its lease.
    _freeze(monkeypatch, last + timedelta(seconds=1))
    refusals, _notes, _workspace = _prestop(_group_owned_estate(deployer_in_group=False), metastore_owner="metastore-admins")
    assert [refusal.code for refusal in refusals] == [METASTORE_AUTHORITY]
    assert "attests no reviewed privilege set" in refusals[0].message
    _value_free(refusals[0].message)

    monkeypatch.setenv(grants.ACK_ENV, _ack(day=day))
    for moment in (last + timedelta(minutes=30), last + timedelta(hours=23, minutes=59), last + grants.RUN_MARGIN):
        _freeze(monkeypatch, moment)
        workspace, execution, recorded = _converger_workspace(admin=False)
        assert _converge(workspace) and execution.table_actions == ["SELECT"], moment
        assert _metastore_reads(recorded) == []
        workspace, execution, _recorded = _converger_workspace(admin=False)
        execution.direct.update(catalog={"USE_CATALOG"}, schema={"USE_SCHEMA"}, table={"SELECT"})
        assert check_campaign_treatment_access(catalog="mip", principal=APP, mode="quiesce", workspace=workspace) == []
    # Past the run's margin the 30 days are over at use as well.
    _freeze(monkeypatch, last + grants.RUN_MARGIN + timedelta(seconds=1))
    workspace, execution, _recorded = _converger_workspace(admin=False)
    with pytest.raises(grants.MetastoreAuthorityUnproven, match="attests no reviewed privilege set"):
        _converge(workspace)
    assert execution.statements == []


def test_one_owner_less_summary_refuses_the_same_way_pre_stop_and_post_stop() -> None:
    # Parity (the same reader): pre-stop names S0(b) and stops; the converger refuses.
    estate = clean_estate()
    estate["metastore"]["owner"] = None
    refusals, _notes, workspace = _prestop(estate, metastore_owner=DEPLOYER)
    assert [refusal.code for refusal in refusals] == [OWNER_POLICY]
    assert "the metastore owner '<missing>'" in refusals[0].message
    assert "spike S0(b)" in refusals[0].message
    assert "set MIP_UC_METASTORE_OWNER to the metastore's actual owner" not in refusals[0].message
    # The catalog, schema and table are judged all the same: the deployer owns them.
    assert "GET metastore_summary" in workspace.estate["calls"]

    converger, execution, _ = harness._workspace()
    converger.metastores.summary = lambda: SimpleNamespace(metastore_id=METASTORE)
    with pytest.raises(RuntimeError, match="returned no owner to the deploying identity"):
        converge(
            warehouse_id="warehouse-1", catalog="mip", principal=APP, mode="quiesce",
            workspace=converger, metastore_owner="deployer", assert_single_writer=lambda: None,
            account_factory=lambda: harness._mirrored_account(converger),
        )  # fmt: skip
    assert execution.statements == []


def test_a_summary_of_another_metastore_cannot_be_evaluated_pre_stop() -> None:
    estate = clean_estate()
    estate["metastore"]["summary_id"] = "metastore-2"

    refusals, _notes, workspace = _prestop(estate, metastore_owner=DEPLOYER)

    assert [refusal.code for refusal in refusals] == [OWNER_POLICY]
    assert "cannot evaluate the approved-owner policy before step 0a" in refusals[0].message
    assert "names another metastore than the workspace's current assignment" in refusals[0].message
    assert not any(call.startswith("GET effective-grants") for call in workspace.estate["calls"])


def test_an_owner_less_summary_still_judges_every_other_object() -> None:
    estate = clean_estate()
    estate["metastore"]["owner"] = None
    estate["catalogs"]["mip"]["owner"] = "outsider@example.invalid"
    estate["principals"]["outsider@example.invalid"] = {"kind": "user", "id": "outsider-id"}

    refusals, _notes, _workspace = _prestop(estate, metastore_owner=DEPLOYER)

    messages = [refusal.message for refusal in refusals]
    assert len(messages) == 2
    assert "the metastore owner '<missing>'" in messages[0]
    assert "the governed catalog owner 'outsider@example.invalid'" in messages[1]


def test_a_noncanonical_summary_owner_is_reported_as_a_data_fault() -> None:
    estate = clean_estate()
    estate["metastore"]["owner"] = f" {DEPLOYER}"

    refusals, _notes, _workspace = _prestop(estate, metastore_owner=DEPLOYER)

    assert [refusal.code for refusal in refusals] == [OWNER_POLICY]
    assert "a fault in the metastore's own data, not a withheld read" in refusals[0].message
    assert "the owner-read follow-up" not in refusals[0].message


def test_a_refusing_owner_is_masked_before_it_is_printed(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    estate = clean_estate()
    estate["catalogs"]["mip"]["owner"] = "outsider@example.invalid"
    estate["principals"]["outsider@example.invalid"] = {"kind": "user", "id": "outsider-id"}
    estate["metastore"]["owner"] = "users"

    refusals, _notes, _workspace = _prestop(estate, metastore_owner=DEPLOYER)

    err = capsys.readouterr().err
    assert len(refusals) == 2
    assert "::add-mask::outsider@example.invalid" in err.splitlines()
    # Platform vocabulary is no identifier, and masking it would blank the log.
    assert "::add-mask::users" not in err.splitlines()

    monkeypatch.setenv("GITHUB_ACTIONS", "false")
    _prestop(estate, metastore_owner=DEPLOYER)
    assert "::add-mask::" not in capsys.readouterr().err


# ------------------------------------------------------------------ the owner's command


class _OwnerWorkspace:
    """The owner's read-only workspace: current, summary, /Me, the App, effective grants."""

    def __init__(self, *, admin: bool = True, held: tuple[str, ...] = ("USE_MARKETPLACE_ASSETS",)) -> None:
        self.calls: list[str] = []
        self.metastores = SimpleNamespace(current=self._current, summary=self._summary)
        self.current_user = SimpleNamespace(me=self._me)
        self.apps = SimpleNamespace(get=self._app)
        self.grants = SimpleNamespace(get_effective=self._effective)
        self._admin = admin
        self._held = held

    def _current(self) -> object:
        self.calls.append("GET current-metastore-assignment")
        return SimpleNamespace(metastore_id=METASTORE)

    def _summary(self) -> object:
        self.calls.append("GET metastore_summary")
        return SimpleNamespace(metastore_id=METASTORE, owner=GROUP)

    def _me(self) -> object:
        self.calls.append("GET scim/Me")
        return SimpleNamespace(user_name="owner", groups=[SimpleNamespace(display=GROUP)] if self._admin else [])

    def _app(self, name: str) -> object:
        self.calls.append("GET apps")
        assert name == "mip-app"
        return SimpleNamespace(service_principal_client_id=APP)

    def _effective(self, securable_type: str, full_name: str, **kwargs: Any) -> object:
        self.calls.append(f"GET effective-grants/{securable_type}")
        assert (full_name, kwargs["principal"]) == (METASTORE, APP)
        entries = [SimpleNamespace(privilege=name) for name in self._held]
        return SimpleNamespace(
            privilege_assignments=[SimpleNamespace(principal="account users", privileges=entries)],
            next_page_token="",
        )


def _owner_run(workspace: _OwnerWorkspace) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = grants.main(["--app-name", "mip-app"], workspace_factory=lambda: workspace, now=lambda: NOW)
    return code, out.getvalue(), err.getvalue()


def test_the_owner_prints_the_digest_a_non_admin_deployer_then_accepts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    workspace = _OwnerWorkspace()

    code, out, err = _owner_run(workspace)

    assert code == 0, err
    digest = _ack(day=NOW.date())
    assert f"[mip-metastore-grants] metastore_app_grants={digest}" in out.splitlines()
    assert "(groups included): USE_MARKETPLACE_ASSETS" in out
    assert "a run that starts before 2026-12-10T00:00Z accepts it" in out
    _value_free(out + err)
    assert all(call.startswith("GET ") for call in workspace.calls), "read-only"
    # End to end: the printed digest is the ack a non-admin deployer's run accepts.
    proof = grants.app_metastore_privileges(
        _me(), metastore=_metastore(), principal=APP, effective=_Reads(set()), environ={grants.ACK_ENV: digest}, now=NOW
    )
    assert proof.route == grants.ATTESTED_ROUTE


def test_the_owner_command_masks_the_app_id_under_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    code, _out, err = _owner_run(_OwnerWorkspace())

    assert code == 0 and f"::add-mask::{APP}" in err.splitlines()


@pytest.mark.parametrize(
    ("workspace", "message"),
    [
        (_OwnerWorkspace(admin=False), "run this as the metastore's owner"),
        (_OwnerWorkspace(held=("USE_MARKETPLACE_ASSETS", "CREATE_CATALOG")), "outside the reviewed set: CREATE_CATALOG"),
    ],
    ids=["not-the-owner", "forbidden-privilege"],
)
def test_the_owner_command_refuses_without_a_digest(workspace: _OwnerWorkspace, message: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

    code, out, err = _owner_run(workspace)

    assert code == 2 and message in err
    assert "metastore_app_grants=" not in out + err
    _value_free(out + err)


def test_an_sdk_error_prints_its_class_never_its_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    workspace = _OwnerWorkspace()

    def denied(_name: str) -> object:
        raise PermissionDenied(f"user {GROUP} may not read app {APP}")

    workspace.apps.get = denied
    code, out, err = _owner_run(workspace)

    assert code == 2 and "PermissionDenied" in err
    _value_free(out + err)


# ------------------------------------------------------------------ the dispatch carrier


@pytest.mark.parametrize("phase", ["plan", "adopt", "unadopt", "release"])
def test_the_ack_is_admitted_in_the_phases_that_read_it(phase: str) -> None:
    assert dispatch_acks.operation_exports(f"metastore_app_grants={'a' * 64}", phase) == {grants.ACK_ENV: "a" * 64}


def test_the_data_phase_never_reads_it() -> None:
    with pytest.raises(ProfileRefusal) as refused:
        dispatch_acks.operation_exports(f"metastore_app_grants={'a' * 64}", "data")
    assert any("never read by --phase data" in line for line in refused.value.lines)
    assert dispatch_acks.OPERATION_ACKS["metastore_app_grants"].env == grants.ACK_ENV
