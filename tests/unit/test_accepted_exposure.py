"""The accepted-exposure policy: exact tuples, the ceiling, matchers and the plan ack (M2-PR4)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tools.databricks.estate import accepted_exposure as policy_module
from tools.databricks.estate.accepted_exposure import (
    ACK_ENV,
    EXPOSURE_ENV,
    Exposure,
    ExposureRefusal,
    build_policy,
    load_policy,
    privilege_class,
)

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
WORKSPACE = "4200000001"
VERIFIER = "00000000-0000-4000-8000-000000000008"


def _clean() -> list[str]:
    return []


def genie_entry(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "kind": "workspace_permission",
        "securable": {"type": "GENIE_SPACE", "id": "space-other-0001", "name": "Other Team Space"},
        "grantee": "users",
        "privileges": ["CAN_VIEW"],
        "source": "inherited:/directories/1001",
        "binding": {"isolation_mode": "ISOLATED", "workspace_ids": [WORKSPACE]},
        "identities": ["app"],
        "privilege_class": "read",
        "reason": "Shared with every workspace user by its owner",
        "expires_at": "2026-12-01",
    }
    entry.update(overrides)
    return entry


def uc_entry(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "kind": "uc_grant",
        "securable": {"type": "CATALOG", "id": "foreign_alpha", "name": "foreign_alpha"},
        "grantee": "account users",
        "privileges": ["USE_CATALOG", "SELECT"],
        "source": "direct",
        "binding": {"isolation_mode": "OPEN", "workspace_ids": []},
        "identities": ["runtime"],
        "privilege_class": "read",
        "reason": "Another team's catalog, readable by every account user",
        "expires_at": "2027-01-31T00:00:00Z",
    }
    entry.update(overrides)
    return entry


def elevated_app(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "privileges": ["CAN_MANAGE"],
        "privilege_class": "elevated",
        "expires_at": (NOW + timedelta(days=30)).isoformat(),
    }
    return genie_entry(**{**fields, **overrides})


def _env(entries: list[dict[str, Any]], **extra: str) -> dict[str, str]:
    return {EXPOSURE_ENV: json.dumps(entries), **extra}


def _load(entries: list[dict[str, Any]], *, control: Any = _clean) -> Any:
    ack = _digest(entries, control=control) if entries else ""
    return load_policy(_env(entries), now=NOW, ack=ack, static_control=control)


def _digest(entries: list[dict[str, Any]], *, control: Any = _clean) -> str:
    from tools.clients.json_fields import ExposureEntry

    parsed = [ExposureEntry.model_validate(item) for item in entries]
    return build_policy(parsed, now=NOW, static_control=control).digest


def _refusal(entries: list[dict[str, Any]], *, control: Any = _clean, **kwargs: Any) -> str:
    with pytest.raises(ExposureRefusal) as caught:
        load_policy(_env(entries), now=NOW, ack="unused", static_control=control, **kwargs)
    return "\n".join(caught.value.lines)


def observed(entry: dict[str, Any]) -> Exposure:
    return Exposure(
        kind=entry["kind"],
        securable_type=entry["securable"]["type"],
        securable_id=entry["securable"]["id"],
        securable_name=entry["securable"]["name"],
        grantee=entry["grantee"],
        privileges=frozenset(entry["privileges"]),
        source=entry["source"],
        isolation_mode=entry["binding"]["isolation_mode"],
        workspace_ids=tuple(entry["binding"]["workspace_ids"]),
    )


# --- exact tuples ---------------------------------------------------------------

_MUTATIONS: dict[str, Any] = {
    "kind": lambda e: replace(e, kind="uc_grant"),
    "securable_type": lambda e: replace(e, securable_type="DASHBOARD"),
    "securable_id": lambda e: replace(e, securable_id="space-other-0002"),
    "securable_name": lambda e: replace(e, securable_name="Other Team Space (renamed)"),
    "grantee": lambda e: replace(e, grantee="account users"),
    "privileges_broader": lambda e: replace(e, privileges=e.privileges | {"CAN_RUN"}),
    "privileges_narrower": lambda e: replace(e, privileges=frozenset()),
    "privileges_other": lambda e: replace(e, privileges=frozenset({"CAN_RUN"})),
    "source_direct": lambda e: replace(e, source="direct"),
    "source_parent": lambda e: replace(e, source="inherited:/directories/1002"),
    "isolation_mode": lambda e: replace(e, isolation_mode="OPEN"),
    "workspace_ids": lambda e: replace(e, workspace_ids=("4200000002",)),
    "workspace_ids_extra": lambda e: replace(e, workspace_ids=(WORKSPACE, "4200000002")),
}


def test_the_exact_tuple_is_accepted_for_its_identity() -> None:
    matcher = _load([genie_entry()]).matcher("app")

    verdict = matcher.verdict(observed(genie_entry()))

    assert verdict.accepted and verdict.entry == 0
    assert matcher.active


@pytest.mark.parametrize("field", sorted(_MUTATIONS))
def test_every_single_field_change_refuses(field: str) -> None:
    matcher = _load([genie_entry()]).matcher("app")
    changed = _MUTATIONS[field](observed(genie_entry()))

    assert changed != observed(genie_entry())
    assert not matcher.accepts(changed)


_TEXT = st.sampled_from(["a", "b"])
_EXPOSURES = st.builds(
    Exposure,
    kind=st.sampled_from(["uc_grant", "workspace_permission"]),
    securable_type=st.sampled_from(["CATALOG", "GENIE_SPACE"]),
    securable_id=_TEXT,
    securable_name=_TEXT,
    grantee=st.sampled_from(["users", "account users"]),
    privileges=st.frozensets(st.sampled_from(["SELECT", "USE_CATALOG", "CAN_VIEW"]), max_size=3),
    source=st.sampled_from(["direct", "inherited:/directories/1", "inherited:CATALOG/a"]),
    isolation_mode=st.sampled_from(["OPEN", "ISOLATED"]),
    workspace_ids=st.sampled_from([(), ("4200000001",), ("4200000001", "4200000002")]),
)


@settings(max_examples=400, deadline=None)
@given(accepted=_EXPOSURES, seen=_EXPOSURES, identity=st.sampled_from(["app", "runtime", "proxy"]))
def test_any_non_exact_tuple_refuses(accepted: Exposure, seen: Exposure, identity: str) -> None:
    # The matcher compares the tuple only, so it is built straight from one entry.
    entry = policy_module.AcceptedEntry(
        index=0,
        exposure=accepted,
        identities=frozenset({identity}),
        privilege_class=privilege_class(accepted),
        reason="property",
        expires_text="2027-01-01",
        expires_at=NOW + timedelta(days=60),
    )
    matcher = policy_module.ExposureMatcher(identity=identity, entries=(entry,), now=NOW)

    assert matcher.accepts(seen) is (seen == accepted)


def test_a_changed_tuple_and_an_unlisted_one_carry_distinct_codes() -> None:
    matcher = _load([genie_entry()]).matcher("app")

    assert matcher.verdict(_MUTATIONS["privileges_broader"](observed(genie_entry()))).code == "changed"
    assert matcher.verdict(observed(uc_entry())).code == "unlisted"


# --- identities -----------------------------------------------------------------


def test_an_app_entry_still_refuses_for_runtime_and_proxy() -> None:
    policy = _load([genie_entry()])

    for identity in ("runtime", "proxy"):
        verdict = policy.matcher(identity).verdict(observed(genie_entry()))
        assert verdict.code == "other_identity"
        assert not policy.matcher(identity).active


@pytest.mark.parametrize("identity", ["verifier", "deployer", ""])
def test_the_verifier_never_consults_a_matcher(identity: str) -> None:
    with pytest.raises(ValueError, match="verifier's audits stay absolute"):
        _load([genie_entry()]).matcher(identity)


def test_an_entry_naming_the_verifier_refuses() -> None:
    lines = _refusal([genie_entry(grantee=VERIFIER)], verifier_application_ids=[VERIFIER])

    assert "entry 0: names the verifier" in lines


def test_the_verifier_ids_the_deploy_exports_are_refused_too() -> None:
    env = _env([genie_entry(grantee=VERIFIER.upper())], DATABRICKS_VERIFIER_CLIENT_ID=VERIFIER)

    with pytest.raises(ExposureRefusal, match="names the verifier"):
        load_policy(env, now=NOW, ack="", static_control=_clean)


@pytest.mark.parametrize(
    ("kwargs", "env"),
    [
        ({"governed_space_ids": ["space-other-0001"]}, {}),
        ({}, {"MIP_GENIE_SPACE_NAME": "other team space"}),
    ],
)
def test_an_entry_naming_the_governed_space_refuses(kwargs: dict[str, Any], env: dict[str, str]) -> None:
    with pytest.raises(ExposureRefusal, match="names the governed Genie space"):
        load_policy({**_env([genie_entry()]), **env}, now=NOW, ack="", static_control=_clean, **kwargs)


def test_a_matcher_refuses_a_governed_space_it_learns_later() -> None:
    matcher = _load([genie_entry()]).matcher("app")

    with pytest.raises(ExposureRefusal, match="governed Genie space"):
        matcher.assert_not_governed(space_ids=["space-other-0001"], space_names=[])
    matcher.assert_not_governed(space_ids=["space-governed"], space_names=["Mortgage Lead Intelligence"])


# --- ceiling --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("privileges", "kind_type", "expected"),
    [
        (["CAN_VIEW"], ("workspace_permission", "GENIE_SPACE"), "read"),
        (["CAN_VIEW", "CAN_RUN"], ("workspace_permission", "GENIE_SPACE"), "read"),
        (["CAN_EDIT"], ("workspace_permission", "GENIE_SPACE"), "elevated"),
        (["CAN_MANAGE"], ("workspace_permission", "GENIE_SPACE"), "elevated"),
        (["CAN_VIEW"], ("workspace_permission", "DASHBOARD"), "elevated"),
        (["BROWSE", "USE_CATALOG", "USE_SCHEMA", "SELECT"], ("uc_grant", "CATALOG"), "read"),
        (["SELECT", "MODIFY"], ("uc_grant", "CATALOG"), "elevated"),
        (["CAN_VIEW"], ("uc_grant", "CATALOG"), "elevated"),
        (["OWNERSHIP"], ("uc_ownership", "CATALOG"), "elevated"),
        ([], ("uc_grant", "CATALOG"), "elevated"),
    ],
)
def test_the_read_class_is_closed(privileges: list[str], kind_type: tuple[str, str], expected: str) -> None:
    exposure = replace(
        observed(uc_entry()),
        kind=kind_type[0],
        securable_type=kind_type[1],
        privileges=frozenset(privileges),
    )

    assert privilege_class(exposure) == expected


@pytest.mark.parametrize("identity", ["runtime", "proxy"])
def test_elevated_for_runtime_or_proxy_refuses(identity: str) -> None:
    lines = _refusal([elevated_app(identities=[identity])])

    assert f"elevated exposure is refused for {identity}" in lines


def test_elevated_for_app_and_runtime_together_refuses() -> None:
    assert "refused for runtime" in _refusal([elevated_app(identities=["app", "runtime"])])


def test_elevated_for_app_without_the_explicit_class_refuses() -> None:
    lines = _refusal([elevated_app(privilege_class="read")])

    assert "privilege_class is read but its privileges are elevated" in lines


def test_a_read_grant_declared_elevated_refuses() -> None:
    assert "privilege_class is elevated but its privileges are read" in _refusal(
        [genie_entry(privilege_class="elevated")]
    )


def test_elevated_for_app_with_a_31_day_expiry_refuses() -> None:
    lines = _refusal([elevated_app(expires_at=(NOW + timedelta(days=31)).isoformat())])

    assert "elevated exposure expires at most 30 days ahead" in lines


def test_elevated_for_app_within_30_days_is_accepted_for_app_only() -> None:
    policy = _load([elevated_app()])

    assert policy.matcher("app").accepts(observed(elevated_app()))
    assert not policy.matcher("runtime").accepts(observed(elevated_app()))


def test_elevated_for_app_needs_the_single_space_control() -> None:
    lines = _refusal([elevated_app()], control=lambda: ["backend/x.py:1: a Genie API path"])

    assert "needs the single-space Genie control" in lines and "backend/x.py:1" in lines


def test_the_static_control_is_consulted_only_for_elevated_entries() -> None:
    def broken() -> list[str]:
        raise AssertionError("a read-only policy must not depend on the control")

    assert _load([genie_entry(), uc_entry()], control=broken).entries


def test_the_default_static_control_holds_on_this_tree() -> None:
    entries = [elevated_app()]
    digest = _digest(entries)

    assert load_policy(_env(entries), now=NOW, ack=digest).matcher("app").active


# --- expiry ---------------------------------------------------------------------


@pytest.mark.parametrize("expires_at", ["2026-10-30", "2026-10-30T12:00:00Z", "2026-10-01"])
def test_an_expired_entry_parses_and_never_matches(expires_at: str) -> None:
    entries = [genie_entry(expires_at=expires_at)]
    matcher = _load(entries).matcher("app")

    verdict = matcher.verdict(observed(genie_entry(expires_at=expires_at)))

    assert verdict.code == "expired" and not verdict.accepted


def test_an_entry_expires_on_the_instant_its_time_passes() -> None:
    entries = [genie_entry(expires_at="2026-10-30T12:00:01Z")]
    policy = _load(entries)

    assert policy.matcher("app").accepts(observed(entries[0]))
    later = replace(policy, now=NOW + timedelta(seconds=1))
    assert later.matcher("app").verdict(observed(entries[0])).code == "expired"


def test_a_time_without_an_offset_refuses() -> None:
    assert "expires_at is not a date or an offset-qualified time" in _refusal(
        [genie_entry(expires_at="2026-12-01T00:00:00")]
    )


# --- closed shape ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("entry", "line"),
    [
        (genie_entry(binding={"isolation_mode": "OPEN", "workspace_ids": []}), "ISOLATED to exactly one"),
        (
            genie_entry(binding={"isolation_mode": "ISOLATED", "workspace_ids": [WORKSPACE, "4200000002"]}),
            "ISOLATED to exactly one",
        ),
        (uc_entry(binding={"isolation_mode": "OPEN", "workspace_ids": [WORKSPACE]}), "OPEN binding lists no"),
        (uc_entry(binding={"isolation_mode": "ISOLATED", "workspace_ids": []}), "ISOLATED binding lists"),
        (
            uc_entry(binding={"isolation_mode": "ISOLATED", "workspace_ids": [WORKSPACE, WORKSPACE]}),
            "repeats an id",
        ),
        (uc_entry(securable={"type": "GENIE_SPACE", "id": "s", "name": "s"}), "does not fit kind"),
        (genie_entry(securable={"type": "CATALOG", "id": "c", "name": "c"}), "does not fit kind"),
        (uc_entry(kind="uc_ownership"), "holds exactly OWNERSHIP"),
        (uc_entry(privileges=["OWNERSHIP"], privilege_class="elevated"), "only uc_ownership holds"),
    ],
)
def test_a_tuple_no_audit_can_observe_refuses(entry: dict[str, Any], line: str) -> None:
    assert line in _refusal([entry])


def test_a_repeated_tuple_refuses() -> None:
    lines = _refusal([genie_entry(), genie_entry(reason="the same grant again")])

    assert "entry 1: repeats entry 0's exact tuple" in lines


@pytest.mark.parametrize(
    ("raw", "line"),
    [
        ("{", "must be single-line JSON"),
        ('{"kind": "uc_grant"}', "must be a JSON list"),
        (json.dumps([genie_entry(reason="<why this is acceptable>")]), "holds a <placeholder>"),
        (json.dumps([genie_entry(privileges=["<privilege>"])]), "holds a <placeholder>"),
        (json.dumps([{**genie_entry(), "extra": "secret-looking-value"}]), "entry 0: invalid extra"),
    ],
)
def test_an_unusable_profile_value_refuses_without_echoing_it(raw: str, line: str) -> None:
    with pytest.raises(ExposureRefusal) as caught:
        load_policy({EXPOSURE_ENV: raw}, now=NOW, ack="", static_control=_clean)

    text = "\n".join(caught.value.lines)
    assert line in text
    assert "secret-looking-value" not in text


def test_a_refusal_names_entries_and_rules_never_values() -> None:
    text = _refusal([elevated_app(identities=["runtime"], grantee="team-secret-group")])

    for value in ("team-secret-group", "space-other-0001", "Other Team Space", WORKSPACE):
        assert value not in text


# --- plan digest and ack ----------------------------------------------------------


def test_an_empty_or_unset_policy_needs_no_ack_and_accepts_nothing() -> None:
    for env in ({}, {EXPOSURE_ENV: ""}, {EXPOSURE_ENV: "[]"}):
        policy = load_policy(env, now=NOW, static_control=_clean)
        assert policy.entries == ()
        assert not policy.matcher("app").accepts(observed(genie_entry()))


def test_a_non_empty_policy_refuses_without_its_ack() -> None:
    entries = [genie_entry()]
    digest = _digest(entries)

    with pytest.raises(ExposureRefusal, match=f"{ACK_ENV}={digest} for this run only"):
        load_policy(_env(entries), now=NOW, static_control=_clean)
    with pytest.raises(ExposureRefusal, match="not acknowledged"):
        load_policy(_env(entries, **{ACK_ENV: "0" * 64}), now=NOW, static_control=_clean)
    assert load_policy(_env(entries, **{ACK_ENV: digest}), now=NOW, static_control=_clean).entries


def test_the_digest_ignores_order_and_moves_with_every_field() -> None:
    entries = [genie_entry(), uc_entry()]
    digest = _digest(entries)

    assert _digest(list(reversed(entries))) == digest
    assert _digest([genie_entry(privileges=["CAN_VIEW"]), uc_entry(privileges=["SELECT", "USE_CATALOG"])]) == digest
    for changed in (
        genie_entry(reason="Another reason"),
        genie_entry(expires_at="2026-12-02"),
        genie_entry(identities=["app", "runtime"]),
        genie_entry(grantee="account users"),
    ):
        assert _digest([changed, uc_entry()]) != digest


def test_the_ack_is_recomputed_so_a_one_character_tamper_refuses() -> None:
    entries = [genie_entry()]
    digest = _digest(entries)
    tampered = [genie_entry(securable={"type": "GENIE_SPACE", "id": "space-other-0001", "name": "Other Team Spacf"})]

    with pytest.raises(ExposureRefusal, match="not acknowledged"):
        load_policy(_env(tampered, **{ACK_ENV: digest}), now=NOW, static_control=_clean)


def test_the_digest_does_not_depend_on_the_moment() -> None:
    entries = [genie_entry()]
    from tools.clients.json_fields import ExposureEntry

    parsed = [ExposureEntry.model_validate(item) for item in entries]
    later = build_policy(parsed, now=NOW + timedelta(days=20), static_control=_clean)

    assert later.digest == _digest(entries)
