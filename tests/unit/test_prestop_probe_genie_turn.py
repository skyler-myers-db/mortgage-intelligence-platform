"""``--phase plan`` writes nothing: its pre-stop probes open no Genie conversation (M1-PR6).

The probes' one call that is not a GET or a list is the Genie turn (it opens a
conversation). ``--genie-turn off``, which only the plan phase passes
(tests/unit/test_plan_phase_live_shape.py), reaches the suite as
``genie_turn=False``; the default still asks it. The fakes are
tests/unit/test_deploy_prestop_probes.py's.
"""

from __future__ import annotations

from typing import Any

import databricks.sdk
import pytest

from tests.unit.test_deploy_prestop_probes import (
    HUMAN_ID,
    _acl,
    _agents_human,
    _app,
    _main_args,
    _ReadOnlyWorkspace,
    _walk,
)
from tools.databricks import deploy_prestop_probes as probes
from tools.databricks import m2m_access_policy, provision_agentic_resources
from tools.databricks import supervisor_retirement_ack as ack


def test_main_passes_the_plan_phases_genie_turn_off_to_the_suite(monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert probes.main(_main_args()) == 0
    assert probes.main([*_main_args(), "--genie-turn", "off"]) == 0
    assert [config.genie_turn for config in seen] == [True, False], "the default asks the turn"
