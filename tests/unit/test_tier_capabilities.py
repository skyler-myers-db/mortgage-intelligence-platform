"""The capability preflight refuses a tier whose workspace features are missing (M2-PR3).

``tools/clients/capabilities.py`` runs read-only, inside the live checks and
before the lease, against a fake SDK here: one list call per feature, at most
one page. The core tier needs Apps, Lakebase, Genie and SQL warehouses; the
agentic tier also Model Serving, Supervisor agents and MLflow. A missing
feature or an unreadable one refuses, naming the feature and the tier, never
a value. The core tier over an App that already exists refuses too: its
upgrade path still needs the agentic identities.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk import errors
from databricks.sdk.errors import NotFound, PermissionDenied

from tests.fixtures.client_profiles import (
    IDENTITY_IDS,
    core_tier_profile,
    env_file_text,
    paychex_shaped_profile,
)
from tests.fixtures.live_workspace import LiveAccount, LiveWorkspace
from tools.clients import capabilities, live_checks
from tools.clients.environment import RawProfile, resolve

FEATURES = {
    "Databricks Apps": "apps.list",
    "Lakebase database instances": "database.list_database_instances",
    "Genie spaces": "genie.list_spaces",
    "SQL warehouses": "warehouses.list",
    "Model Serving (AI Gateway endpoints)": "serving_endpoints.list",
    "Agent Bricks Supervisor agents": "GET /api/2.1/supervisor-agents",
    "MLflow experiments": "experiments.list_experiments",
}
CORE = ("Databricks Apps", "Lakebase database instances", "Genie spaces", "SQL warehouses")


class FakeSdk:
    """The capability reads only; ``fail`` maps a call to the error it raises."""

    def __init__(self, *, fail: dict[str, BaseException] | None = None, app_exists: bool = False) -> None:
        self.calls: list[str] = []
        self._fail = dict(fail or {})
        self._app_exists = app_exists
        self.apps = SimpleNamespace(list=self._lister("apps.list"), get=self._app_get)
        self.database = SimpleNamespace(list_database_instances=self._lister("database.list_database_instances"))
        self.genie = SimpleNamespace(list_spaces=self._lister("genie.list_spaces"))
        self.warehouses = SimpleNamespace(list=self._lister("warehouses.list"))
        self.serving_endpoints = SimpleNamespace(list=self._lister("serving_endpoints.list"))
        self.experiments = SimpleNamespace(list_experiments=self._lister("experiments.list_experiments"))
        self.api_client = SimpleNamespace(do=self._do)

    def _call(self, name: str) -> None:
        self.calls.append(name)
        if name in self._fail:
            raise self._fail[name]

    def _lister(self, name: str) -> Any:
        def listing(**_kwargs: Any) -> Iterator[object]:
            # Raised on the call itself, as the SDK's first page request does.
            self._call(name)
            return iter([object(), object()])

        return listing

    def _do(self, method: str, path: str, **_kwargs: Any) -> dict[str, Any]:
        assert method == "GET", "a capability probe is read-only"
        self._call(f"{method} {path}")
        return {"supervisor_agents": []}

    def _app_get(self, name: str) -> object:
        self.calls.append(f"apps.get {name}")
        if not self._app_exists:
            raise NotFound("no such app")
        return SimpleNamespace(name=name)


def _profile(tier: str) -> Any:
    return resolve(RawProfile(values=paychex_shaped_profile() if tier == "on" else core_tier_profile()))


def test_the_agentic_tier_proves_every_feature_with_one_read_each() -> None:
    sdk = FakeSdk()
    assert capabilities.check(_profile("on"), sdk) == []
    assert sdk.calls == list(FEATURES.values()), "one read per feature, and no App read in this tier"


def test_the_core_tier_needs_only_the_core_features() -> None:
    sdk = FakeSdk()
    assert capabilities.check(_profile("off"), sdk) == []
    assert sdk.calls == [*(FEATURES[name] for name in CORE), "apps.get mip-app"]
    assert [capability.name for capability in capabilities.required("off")] == list(CORE)


@pytest.mark.parametrize("feature", sorted(FEATURES))
@pytest.mark.parametrize("error", [NotFound("ENDPOINT_NOT_FOUND"), errors.NotImplemented("not enabled")])
def test_a_missing_feature_refuses_the_tier_that_needs_it(feature: str, error: BaseException) -> None:
    sdk = FakeSdk(fail={FEATURES[feature]: error})
    lines = capabilities.check(_profile("on"), sdk)
    assert lines == [
        f"MIP_AGENTIC_TIER: the agentic tier (MIP_AGENTIC_TIER=on) needs {feature}, which this "
        "workspace does not offer"
    ]
    # Every other feature was still read: the refusals are collected.
    assert len(sdk.calls) == len(FEATURES)
    core = capabilities.check(_profile("off"), FakeSdk(fail={FEATURES[feature]: error}))
    assert bool(core) == (feature in CORE), "the core tier refuses only what it needs"


def test_an_unreadable_feature_refuses_and_never_reads_as_present() -> None:
    sdk = FakeSdk(fail={"genie.list_spaces": PermissionDenied("user 4200000001 may not list")})
    lines = capabilities.check(_profile("on"), sdk)
    assert lines == [
        "MIP_AGENTIC_TIER: could not prove Genie spaces for the agentic tier (MIP_AGENTIC_TIER=on): PermissionDenied"
    ]
    assert "4200000001" not in "".join(lines), "a refusal never carries what the SDK said"


def test_the_core_tier_over_an_existing_app_refuses_before_the_lease() -> None:
    lines = capabilities.check(_profile("off"), FakeSdk(app_exists=True))
    assert lines == [
        "MIP_AGENTIC_TIER: the core tier cannot upgrade an existing App yet (its signed-blue "
        "restore and activation still need the agentic identities); deploy it with MIP_AGENTIC_TIER=on"
    ]
    assert capabilities.check(_profile("on"), FakeSdk(app_exists=True)) == [], "the agentic tier upgrades it"


def test_a_tier_the_schema_was_bypassed_for_refuses() -> None:
    forged = _profile("on").model_copy(update={"agentic_tier": "maybe"})
    sdk = FakeSdk()
    assert capabilities.check(forged, sdk) == ["MIP_AGENTIC_TIER: must be exactly off or on"]
    assert sdk.calls == []


def test_the_live_checks_run_the_capability_preflight(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    profile = paychex_shaped_profile()
    env_file = tmp_path / ".env.local"
    env_file.write_text(env_file_text(profile), encoding="utf-8")
    workspace = LiveWorkspace(profile, identity_ids=IDENTITY_IDS, missing_features=("GET supervisor-agents (list)",))
    account = LiveAccount(IDENTITY_IDS["account_principal"])

    code = live_checks.main([], environ={}, env_file=env_file, client=lambda: workspace, account=lambda: account)

    assert code == 2
    err = capsys.readouterr().err
    assert "needs Agent Bricks Supervisor agents, which this workspace does not offer" in err
    assert "GET supervisor-agents (list)" in workspace.calls
