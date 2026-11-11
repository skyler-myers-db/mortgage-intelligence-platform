"""The profile's ``MIP_SALESFORCE_BINDINGS`` decides the App's three optional bindings (spike S2b).

``databricks.yml`` declares fourteen App bindings, three of them the
Salesforce connector's secrets. The release applies those three only when the
profile switches them on: off (the default) keeps the eleven client #1's live
App runs on, on yields all fourteen. A partial connector refuses either way,
and so does an unreviewed switch value. The binding payload is built here
from the real bundle declaration, with placeholder ids.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from tools.databricks import app_resource_bindings as bindings
from tools.databricks.app_optional_bindings import (
    SALESFORCE_BINDINGS,
    salesforce_bindings_from_env,
    salesforce_bindings_on,
    select_bindings,
)

ROOT = Path(__file__).resolve().parents[2]
APP_NAME = "mip-app-PLACEHOLDER"
# The eleven bindings spike S2 read on client #1's live App (names only).
LIVE_ELEVEN = frozenset(
    {
        "sql_warehouse",
        "genie_space",
        "database",
        "lifecycle_sync_job",
        "fred_rates_job",
        "silver_refresh_job",
        "gold_refresh_job",
        "cotality_id_mask_secret",
        "genie_action_current_secret",
        "genie_action_previous_secret",
        "growth_agent_scheduler_job",
    }
)
_RESOURCE_REF = re.compile(r"\$\{resources\.([a-z_]+)\.([A-Za-z0-9_-]+)\.([A-Za-z0-9_]+)\}")
_VAR_REF = re.compile(r"\$\{var\.([a-z_]+)\}")


def bundle_summary() -> dict[str, Any]:
    """The real bundle's App declaration as a post-apply summary, every id a placeholder."""

    bundle = yaml.safe_load((ROOT / "databricks.yml").read_text(encoding="utf-8"))
    app = copy.deepcopy(bundle["resources"]["apps"]["mip_app"])
    resources: dict[str, Any] = {}

    def concrete(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: concrete(item) for key, item in value.items()}
        if isinstance(value, list):
            return [concrete(item) for item in value]
        if not isinstance(value, str):
            return value
        match = _RESOURCE_REF.fullmatch(value)
        if match:
            kind, key, field = match.groups()
            resources.setdefault(kind, {}).setdefault(key, {})[field] = f"{key}-placeholder"
            return value
        return _VAR_REF.sub(lambda var: f"{var.group(1)}-placeholder", value)

    app["resources"] = concrete(app["resources"])
    app["name"] = APP_NAME
    resources["apps"] = {"mip_app": app}
    return {"resources": resources}


def _payload(*, salesforce: bool) -> dict[str, Any]:
    return bindings.build_resource_binding_payload(
        bundle_summary(), app_name=APP_NAME, salesforce_bindings=salesforce
    )


def _names(payload: dict[str, Any]) -> set[str]:
    return {str(item["name"]) for item in payload["resources"]}


def test_the_bundle_declares_fourteen_bindings_three_of_them_salesforce() -> None:
    declared = {item["name"] for item in bundle_summary()["resources"]["apps"]["mip_app"]["resources"]}
    assert len(declared) == 14 and declared >= SALESFORCE_BINDINGS
    assert declared - SALESFORCE_BINDINGS == LIVE_ELEVEN


def test_off_keeps_the_eleven_live_bindings_and_on_yields_fourteen() -> None:
    off, on = _payload(salesforce=False), _payload(salesforce=True)

    assert _names(off) == LIVE_ELEVEN and len(off["resources"]) == 11
    assert _names(on) == LIVE_ELEVEN | SALESFORCE_BINDINGS and len(on["resources"]) == 14
    # The bundle stays the one definition: the switch only selects.
    declared = bundle_summary()["resources"]["apps"]["mip_app"]["resources"]
    assert [item["name"] for item in on["resources"]] == [item["name"] for item in declared]
    assert off["description"] == on["description"] and off["description"].strip()


def test_the_build_cli_takes_the_switch_and_has_no_default(tmp_path: Path) -> None:
    summary, out = tmp_path / "summary.json", tmp_path / "payload.json"
    summary.write_text(json.dumps(bundle_summary()), encoding="utf-8")
    base = ["build", "--bundle-summary", str(summary), "--app-name", APP_NAME, "--out", str(out)]

    with pytest.raises(SystemExit):
        bindings.main(base)  # a forgotten switch never builds the default silently
    assert bindings.main([*base, "--salesforce-bindings", "off"]) == 0
    assert _names(json.loads(out.read_text(encoding="utf-8"))) == LIVE_ELEVEN
    assert bindings.main([*base, "--salesforce-bindings", "on"]) == 0
    assert len(json.loads(out.read_text(encoding="utf-8"))["resources"]) == 14


def test_a_partial_connector_refuses_and_on_needs_a_declared_connector() -> None:
    eleven = [{"name": name} for name in sorted(LIVE_ELEVEN)]
    partial = [*eleven, {"name": "salesforce_password"}]

    for salesforce in (False, True):
        with pytest.raises(ValueError, match="only part"):
            select_bindings(partial, salesforce=salesforce)
    with pytest.raises(ValueError, match="declares no Salesforce"):
        select_bindings(eleven, salesforce=True)
    assert select_bindings(eleven, salesforce=False) == eleven


@pytest.mark.parametrize(("value", "on"), ((None, False), ("", False), ("off", False), (" on ", True)))
def test_the_switch_reads_unset_as_off(value: str | None, on: bool) -> None:
    assert salesforce_bindings_on(value) is on
    environ = {} if value is None else {"MIP_SALESFORCE_BINDINGS": value}
    assert salesforce_bindings_from_env(environ) is on


@pytest.mark.parametrize("value", ("ON", "true", "1", "yes", "of"))
def test_an_unreviewed_switch_value_refuses(value: str) -> None:
    with pytest.raises(ValueError, match="must be off or on"):
        salesforce_bindings_on(value)


def test_the_profile_field_is_public_optional_and_shaped_off_or_on() -> None:
    from tests.fixtures.client_profiles import demo_profile
    from tools.clients.environment import RawProfile, resolve
    from tools.clients.errors import ProfileRefusal
    from tools.clients.schema import FIELD_CLASS, FieldClass

    assert FIELD_CLASS["MIP_SALESFORCE_BINDINGS"] is FieldClass.PUBLIC
    assert resolve(RawProfile(values=demo_profile())).salesforce_bindings is None
    assert resolve(RawProfile(values=demo_profile(MIP_SALESFORCE_BINDINGS="on"))).salesforce_bindings == "on"
    with pytest.raises(ProfileRefusal, match="MIP_SALESFORCE_BINDINGS"):
        resolve(RawProfile(values=demo_profile(MIP_SALESFORCE_BINDINGS="yes")))


@pytest.mark.parametrize(("switch", "count"), ((None, 11), ("off", 11), ("on", 14)))
def test_adopt_reads_the_profiles_bindings_through_the_same_switch(
    monkeypatch: pytest.MonkeyPatch, switch: str | None, count: int
) -> None:
    """Cutover step 10: adopt-live compares the live App with the profile's reviewed
    bindings. On origin/main those were all fourteen the bundle declares, so client
    #1's eleven live bindings refused (``bindings_differ``); the switch decides now."""

    import subprocess
    from types import SimpleNamespace

    from tools.databricks import adopt_live_facts

    summary = bundle_summary()
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(returncode=0, stdout=json.dumps(summary), stderr=""),
    )
    if switch is None:
        monkeypatch.delenv("MIP_SALESFORCE_BINDINGS", raising=False)
    else:
        monkeypatch.setenv("MIP_SALESFORCE_BINDINGS", switch)

    reviewed = adopt_live_facts.reviewed_bindings()

    assert len(reviewed) == count
    assert ({str(item["name"]) for item in reviewed} & SALESFORCE_BINDINGS) == (
        SALESFORCE_BINDINGS if count == 14 else set()
    )
    monkeypatch.setenv("MIP_SALESFORCE_BINDINGS", "yes")
    with pytest.raises(adopt_live_facts.AdoptLiveRefused, match="must be off or on"):
        adopt_live_facts.reviewed_bindings()
