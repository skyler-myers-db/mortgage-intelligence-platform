"""The foreign UC audit removes exactly the runtime's accepted exposure, nothing more (M2-PR4).

The audit refuses any effective privilege the audited identity holds on a
foreign catalog or below it. With the identity's matcher, a direct grant that
an entry names exactly is accepted, the same grantee's privileges inherited
from it below are accepted while they are a subset, and everything else still
refuses. The harness is the foreign UC audit's own (one foreign catalog
``other`` with a schema, a table, a function, a volume and a model).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from tests.unit import test_audit_agent_runtime_foreign_uc_access as harness
from tests.unit.test_audit_agent_runtime_foreign_uc_access import _assignment
from tools.clients.json_fields import ExposureEntry
from tools.databricks.estate.accepted_exposure import ExposureRefusal, build_policy, load_policy
from tools.databricks.estate.uc_exposure import UcExposureScope, securable_id

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
READERS = "account users"


def entry(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "kind": "uc_grant",
        "securable": {"type": "CATALOG", "id": "other", "name": "other"},
        "grantee": READERS,
        "privileges": ["USE_CATALOG", "USE_SCHEMA", "SELECT"],
        "source": "direct",
        "binding": {"isolation_mode": "OPEN", "workspace_ids": []},
        "identities": ["runtime"],
        "privilege_class": "read",
        "reason": "Another team's catalog, readable by every account user",
        "expires_at": "2026-12-31",
    }
    value.update(overrides)
    return value


def _matcher(entries: list[dict[str, Any]], identity: str = "runtime") -> Any:
    parsed = [ExposureEntry.model_validate(item) for item in entries]
    digest = build_policy(parsed, now=NOW, static_control=lambda: []).digest
    env = {"MIP_ACCEPTED_EXPOSURE_JSON": json.dumps(entries)}
    return load_policy(env, now=NOW, ack=digest, static_control=lambda: []).matcher(identity)


def _readable_catalog(*, schema_grant: bool = False) -> Any:
    """``account users`` holds USE_CATALOG, USE_SCHEMA and SELECT on ``other`` itself."""

    inherited = {"principal": READERS, "inherited_type": "CATALOG", "inherited_name": "other"}
    values: dict[tuple[str, str], list[object]] = {
        ("catalog", "other"): [_assignment("USE_CATALOG", "USE_SCHEMA", "SELECT", principal=READERS)],
        ("schema", "other.sandbox"): [_assignment("USE_SCHEMA", **inherited)],
        ("schema", "other.information_schema"): [
            _assignment("USE_SCHEMA", principal=READERS),
            _assignment("USE_SCHEMA", **inherited),
        ],
        ("table", "other.sandbox.secret"): [_assignment("SELECT", **inherited)],
        ("table", "other.information_schema.tables"): [
            _assignment("SELECT", principal=READERS),
            _assignment("SELECT", **inherited),
        ],
    }
    if schema_grant:
        values[("schema", "other.sandbox")].append(_assignment("USE_SCHEMA", "MODIFY", principal=READERS))
    return harness._workspace(values)


def test_without_an_entry_the_readable_catalog_refuses() -> None:
    with pytest.raises(RuntimeError, match="forbidden access on catalog other"):
        harness._audit(_readable_catalog())


def test_the_exact_catalog_entry_accepts_it_and_its_inherited_children() -> None:
    proof = harness._audit(_readable_catalog(), exposure=_matcher([entry()]))

    # Still grant-audited, so the self-view verifiers keep skipping it.
    assert proof.grant_audited_catalogs == frozenset({"other"})


@pytest.mark.parametrize(
    "changed",
    [
        {"privileges": ["USE_CATALOG", "SELECT"]},
        {"privileges": ["USE_CATALOG", "USE_SCHEMA", "SELECT", "BROWSE"]},
        {"grantee": "users"},
        {"source": "inherited:METASTORE/metastore-id"},
        {"securable": {"type": "CATALOG", "id": "other", "name": "other_renamed"}},
        {"binding": {"isolation_mode": "ISOLATED", "workspace_ids": ["4200000001"]}},
        {"identities": ["app"]},
        {"identities": ["proxy"]},
        {"expires_at": "2026-10-01"},
    ],
    ids=lambda changed: next(iter(changed)),
)
def test_any_other_tuple_still_refuses(changed: dict[str, Any]) -> None:
    with pytest.raises(RuntimeError, match="forbidden access on catalog other"):
        harness._audit(_readable_catalog(), exposure=_matcher([entry(**changed)]))


def test_an_unlisted_direct_grant_below_an_accepted_catalog_still_refuses() -> None:
    with pytest.raises(RuntimeError, match="effective UC boundary failed for schema other.sandbox"):
        harness._audit(_readable_catalog(schema_grant=True), exposure=_matcher([entry()]))


def test_modify_on_a_foreign_catalog_is_elevated_and_refused_for_runtime() -> None:
    with pytest.raises(ExposureRefusal, match="elevated exposure is refused for runtime"):
        _matcher([entry(privileges=["USE_CATALOG", "SELECT", "MODIFY"], privilege_class="elevated")])


def test_an_isolated_catalog_binds_its_workspace_ids() -> None:
    workspace = _readable_catalog()
    other = next(item for item in workspace.catalogs.list(include_browse=True, include_unbound=True) if item.name == "other")
    other.isolation_mode = "ISOLATED"
    bound = {"isolation_mode": "ISOLATED", "workspace_ids": [harness.WORKSPACE_ID]}

    assert harness._audit(workspace, exposure=_matcher([entry(binding=bound)])).grant_audited_catalogs
    with pytest.raises(RuntimeError, match="forbidden access on catalog other"):
        harness._audit(workspace, exposure=_matcher([entry()]))


def test_an_inherited_privilege_beyond_the_accepted_set_refuses() -> None:
    scope = UcExposureScope(matcher=_matcher([entry()]), binding=lambda _catalog: ("OPEN", ()))
    catalog = {"USE_CATALOG": {(READERS, "", "")}, "USE_SCHEMA": {(READERS, "", "")}, "SELECT": {(READERS, "", "")}}

    assert scope.residual(securable_type="catalog", full_name="other", item_id="other", sources=catalog) == {}
    child = {"SELECT": {(READERS, "CATALOG", "other")}, "MODIFY": {(READERS, "CATALOG", "other")}}
    assert scope.residual(securable_type="table", full_name="other.s.t", item_id="t-1", sources=child) == {
        "SELECT": {(READERS, "CATALOG", "other")},
        "MODIFY": {(READERS, "CATALOG", "other")},
    }
    narrower = {"SELECT": {(READERS, "CATALOG", "other")}}
    assert scope.residual(securable_type="table", full_name="other.s.t", item_id="t-1", sources=narrower) == {}


def test_inheritance_from_a_catalog_never_accepted_refuses() -> None:
    scope = UcExposureScope(matcher=_matcher([entry()]), binding=lambda _catalog: ("OPEN", ()))
    child = {"SELECT": {(READERS, "CATALOG", "other")}}

    assert scope.residual(securable_type="table", full_name="other.s.t", item_id="t-1", sources=child) == child


def test_an_expected_baseline_source_is_left_to_the_exact_check() -> None:
    scope = UcExposureScope(
        matcher=_matcher([entry(securable={"type": "SCHEMA", "id": "s-1", "name": "other.information_schema"},
                                privileges=["USE_SCHEMA"])]),
        binding=lambda _catalog: ("OPEN", ()),
    )
    baseline = {"USE_SCHEMA": {(READERS, "", "")}}

    kept = scope.residual(
        securable_type="schema", full_name="other.information_schema", item_id="s-1", sources=baseline, keep=baseline
    )
    assert kept == baseline


def test_securable_ids_come_from_the_api_and_a_catalog_is_named_by_itself() -> None:
    assert securable_id(SimpleNamespace(), securable_type="catalog", full_name="other") == "other"
    assert securable_id(SimpleNamespace(table_id="t-1"), securable_type="table", full_name="o.s.t") == "t-1"
    assert securable_id(SimpleNamespace(), securable_type="table", full_name="o.s.t") == ""
    assert securable_id(SimpleNamespace(), securable_type="function", full_name="o.s.model") == ""


def test_the_cli_takes_the_runtime_or_proxy_matcher_only(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []
    monkeypatch.setattr(harness.auditor, "install_probe_deadlines", lambda **_kw: None)
    monkeypatch.setattr(harness.auditor, "deployment_workspace_client", lambda **_kw: "workspace")
    monkeypatch.setattr(harness.auditor, "identity_matcher", lambda identity: f"matcher-{identity}")
    monkeypatch.setattr(
        harness.auditor, "audit_foreign_uc_access", lambda _workspace, **kwargs: seen.append(kwargs["exposure"])
    )
    base = ["--application-id", "runtime-EXAMPLE", "--expected-inventory-principal", "deployer@example.invalid"]

    assert harness.auditor.main([*base, "--exposure-identity", "runtime"]) == 0
    assert harness.auditor.main(base) == 0
    assert seen == ["matcher-runtime", None]
    with pytest.raises(SystemExit):
        harness.auditor.main([*base, "--exposure-identity", "app"])
