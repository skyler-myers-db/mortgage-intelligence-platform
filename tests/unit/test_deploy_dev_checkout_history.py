"""deploy-dev.yml's adopt phase checks out main's full history; the rest stay at depth 1 (B5).

adopt-live (``--phase adopt``: modes adopt and supersede) resolves the live
App's health sha in the deploy job's checkout, proves it an ancestor of
``refs/remotes/origin/main`` and reads ``app.yaml`` and the runtime closure at
it, so that phase needs main's history. The depth is a phase-keyed
``fromJSON`` map, the idiom of the job's timeout: 2147483647, git's
INFINITE_DEPTH (the deepen ``git fetch --unshallow`` sends), for adopt, so
actions/checkout keeps its one authenticated fetch of
``+<run commit>:refs/remotes/origin/main``, now unshallow, and fetches no other
branch or tag; 1 for the rest. Never 0: actions/checkout then fetches every
branch and tag, prints each name in the log, and a tag named like a short sha
shadows that commit (tests/unit/test_adopt_live_checkout_history.py runs both
fetch paths against the real adopt-live check).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from tests.fixtures.workflow_expressions import render

REPO = Path(__file__).resolve().parents[2]
DEPLOY_DEV = REPO / ".github" / "workflows" / "deploy-dev.yml"
ADOPT_LIFECYCLE = REPO / "scripts" / "lib" / "deploy_adopt_phase_lifecycle.sh"
INFINITE_DEPTH = 2**31 - 1  # git's INFINITE_DEPTH (0x7fffffff)
FULL_HISTORY_PHASES = frozenset({"adopt"})
_PHASE_DEPTH = re.compile(r"\$\{\{ fromJSON\('(\{[^']+\})'\)\[inputs\.phase\] \}\}")


def _workflow() -> dict[Any, Any]:
    return yaml.safe_load(DEPLOY_DEV.read_text(encoding="utf-8"))


def _phases() -> list[str]:
    triggers = _workflow()[True]  # YAML 1.1 reads the bare key `on` as true
    return list(triggers["workflow_dispatch"]["inputs"]["phase"]["options"])


def _checkouts(job: str) -> list[dict[str, Any]]:
    steps = _workflow()["jobs"][job]["steps"]
    return [step for step in steps if str(step.get("uses", "")).startswith("actions/checkout@")]


def _deploy_depth_expression() -> str:
    (checkout,) = _checkouts("deploy")
    return str(checkout["with"]["fetch-depth"])


def test_the_deploy_jobs_one_checkout_adds_only_the_phase_keyed_depth() -> None:
    (checkout,) = _checkouts("deploy")
    expression = _deploy_depth_expression()

    assert checkout["uses"] == "actions/checkout@v6"
    assert checkout["with"] == {"persist-credentials": False, "fetch-depth": expression}
    assert _PHASE_DEPTH.fullmatch(expression), f"one reviewed phase-keyed map: {expression}"


def test_the_depth_map_states_a_depth_for_every_phase_and_full_history_for_adopt_only() -> None:
    match = _PHASE_DEPTH.fullmatch(_deploy_depth_expression())
    assert match
    depths = json.loads(match.group(1))

    assert list(depths) == _phases(), "a new phase must state its checkout depth"
    assert {phase for phase, depth in depths.items() if depth != 1} == FULL_HISTORY_PHASES
    assert depths["adopt"] == INFINITE_DEPTH, "main's full history, never 0 (every branch and tag)"
    assert 0 not in depths.values()


def test_each_phase_renders_the_depth_actions_checkout_reads() -> None:
    """What the runner hands actions/checkout's ``fetch-depth`` input, phase by phase."""

    expression = _deploy_depth_expression()
    rendered = {phase: render(expression, {"inputs": {"phase": phase}}) for phase in _phases()}

    assert rendered == {
        phase: (str(INFINITE_DEPTH) if phase in FULL_HISTORY_PHASES else "1") for phase in _phases()
    }
    # An unknown phase renders '' and actions/checkout defaults it to depth 1: shallow fails closed.
    assert render(expression, {"inputs": {"phase": "no-such-phase"}}) == ""
    # The form this map replaces: 0 is falsy, so `&& 0 || 1` renders 1 for adopt too.
    trap = "${{ inputs.phase == 'adopt' && 0 || 1 }}"
    assert render(trap, {"inputs": {"phase": "adopt"}}) == "1"


def test_only_the_adopt_phase_resolves_a_health_sha_against_origin_main() -> None:
    """--phase adopt runs adopt-live's adopt or supersede (which gather); any other runs unadopt."""

    text = ADOPT_LIFECYCLE.read_text(encoding="utf-8")
    body = re.search(r"(?ms)^adopt_live_mode\(\) \{\n(.*?)^\}", text)
    assert body, "adopt_live_mode() in the adopt phase lifecycle"
    mode = body.group(1)

    assert "--main-ref refs/remotes/origin/main" in mode
    assert '  ADOPT_LIVE_MODE="unadopt"\n' in mode, "unadopt is the default mode"
    assert '[[ "$phase" == "adopt" && -n "${MIP_ADOPT_LIVE_SUPERSEDE:-}" ]]' in mode
    assert 'elif [[ "$phase" == "adopt" ]]; then\n    ADOPT_LIVE_MODE="adopt"' in mode
    assert re.findall(r'ADOPT_LIVE_MODE="(\w+)"', mode) == ["unadopt", "supersede", "adopt"]


def test_the_build_job_stays_shallow() -> None:
    """It stages and seals this run's commit only (``--ref "$GITHUB_SHA"``): depth 1 is enough."""

    (checkout,) = _checkouts("build")

    assert checkout["uses"] == "actions/checkout@v6"
    assert checkout["with"] == {"persist-credentials": False}
