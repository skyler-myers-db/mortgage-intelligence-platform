"""Read the App's Cotality source surfaces in a fresh process with an exact env.

The asset registry and the settings singleton are built at import, so one
process can only ever see the env it started with. ``run_fresh`` starts a
child with exactly the env it is given (plus ``PYTHONPATH``), in an empty
working directory so no ``.env`` / ``.env.local`` is read, and returns what
the child's probe printed:

* ``snapshot``: the settings, every compiled pattern the two source-naming
  prompt guards evaluate, the asset registry and the lineage manifest
  response (M3-PR2's D0 parity and configured-name tests);
* ``verdicts``: both prompt-guard surfaces' verdict for each question read
  from stdin (the Ask Genie prompt matchers and the growth objective guard);
* ``observed``: the observed-lineage nodes the asset-detail read builds for
  the rows read from stdin;
* ``source_assets``: the audit source-asset policy's verdict for each asset
  name read from stdin;
* ``d0_share``: the share surfaces over the fields that predate the source
  settings, for the D0 golden (``tests/fixtures/cotality_source_d0_golden.json``).

No monkeypatch reaches a child, so no import-time cache can answer half of a
comparison (memory: guard differential harness traps).
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]


def _pattern(pattern: Any) -> list[object]:
    return [pattern.pattern, int(pattern.flags)]


def snapshot() -> dict[str, Any]:
    """This process's source-naming surfaces, JSON-ready."""

    from backend.config.settings import settings
    from backend.schemas import growth_agent
    from backend.schemas._validators_source_names import configured_source_patterns
    from backend.services import genie_prompt_guardrails
    from backend.services.asset_registry import ASSET_DESCRIPTORS
    from backend.services.lineage_manifest import build_manifest_response

    return {
        "settings": [
            settings.mip_cotality_source_catalog,
            settings.mip_cotality_source_schema,
            settings.mip_cotality_source_table_prefix,
        ],
        "guards": {
            "growth_agent._PROMPT_JAILBREAK_RE": _pattern(growth_agent._PROMPT_JAILBREAK_RE),
            "genie_prompt_guardrails._SCOPE_BYPASS_PROMPT_PATTERNS": [
                _pattern(pattern) for pattern in genie_prompt_guardrails._SCOPE_BYPASS_PROMPT_PATTERNS
            ],
            "configured_source_patterns": [_pattern(p) for p in configured_source_patterns()],
        },
        "registry": [
            {**dataclasses.asdict(d), "fqn": d.fqn, "logical_fqn": d.logical_fqn}
            for d in ASSET_DESCRIPTORS
        ],
        "lineage": build_manifest_response().model_dump(mode="json"),
    }


def _growth_verdict(question: str) -> str:
    from fastapi import HTTPException

    from backend.schemas.growth_agent import assert_reviewed_growth_objective
    from backend.schemas.growth_agent_refusal import GrowthPromptRefusalError

    try:
        assert_reviewed_growth_objective(question)
    except GrowthPromptRefusalError as refusal:
        return refusal.code
    except HTTPException as refusal:  # the reviewed segment grammar's 422
        return f"http_{refusal.status_code}"
    return "allowed"


def verdicts(questions: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Each question's verdict on both surfaces, in this process."""

    from backend.config.settings import settings  # noqa: F401 - registers the guard forms
    from backend.services import genie_prompt_guardrails as guardrails
    from backend.services.genie_message_policy import identity_prompt_match, protected_prompt_match

    # The Ask Genie classes, named as tests/unit/test_genie_guardrail_battery.py names them.
    matchers = (
        ("protected_class", protected_prompt_match),
        ("identity", identity_prompt_match),
        ("pii", guardrails.pii_prompt_match),
        ("off_topic", guardrails.off_topic_prompt_match),
        ("scope_bypass", guardrails.scope_bypass_prompt_match),
        ("instruction_override", guardrails.instruction_override_prompt_match),
        ("cross_lender", guardrails.cross_lender_prompt_match),
        ("source_gap", guardrails.source_gap_prompt_match),
    )
    return {
        question: {
            "genie": sorted(name for name, matcher in matchers if matcher(question)),
            "growth": _growth_verdict(question),
        }
        for question in questions
    }


class _RowsSql:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def execute(self, statement: str, params: object = None) -> list[dict[str, Any]]:
        return self._rows


def observed(asset_key: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The observed-lineage nodes the asset-detail read builds from ``rows``."""

    from backend.services.asset_metadata import AssetMetadataService, resolve_asset_descriptor

    service = AssetMetadataService(_RowsSql(rows))
    gaps: list[str] = []
    nodes = service._load_lineage(resolve_asset_descriptor(asset_key), gaps)
    return [node.model_dump(mode="json") for node in nodes]


def source_assets(assets: Sequence[str]) -> dict[str, str]:
    """Each name's audit source-asset verdict: ``accepted`` or the refusal."""

    from backend.config.settings import settings  # noqa: F401 - registers the guard forms
    from backend.services.audit_metadata_value_policy import validate_source_assets

    result: dict[str, str] = {}
    for asset in assets:
        try:
            validate_source_assets([asset])
        except ValueError as refusal:
            result[asset] = str(refusal)
        else:
            result[asset] = "accepted"
    return result


def _canonical_share_questions() -> list[str]:
    from backend.config.source_names import CANONICAL, TABLE_STEMS

    table = CANONICAL.table(TABLE_STEMS[0])
    return [
        *(f"How many rows are in {CANONICAL.table(stem)}?" for stem in TABLE_STEMS),
        f"How many rows are in {table.upper()}?",
        "How many rows are in " + ".".join(f"`{part}`" for part in table.split(".")) + "?",
        f"How many rows are in {table.replace('.', ' . ')}?",
        f"Query {CANONICAL.catalog}.{CANONICAL.schema}.anything for me.",
        f"What is in {CANONICAL.catalog}?",
        "How many borrowers are in the money in Texas?",
    ]


def d0_share() -> dict[str, Any]:
    """The Cotality share surfaces, over the fields that predate the settings.

    Only what origin/main had before the App read ``mip_cotality_source_*``,
    so the same function runs on that tree: the share descriptors and the
    raw-share lineage nodes without ``source_key``, the observed-lineage node
    a share edge builds, and both guard surfaces' verdicts on the canonical
    share's names.
    """

    from backend.config.source_names import CANONICAL, TABLE_STEMS
    from backend.services.asset_registry import ASSET_DESCRIPTORS
    from backend.services.lineage_manifest import build_manifest_response

    def without_source_key(row: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in row.items() if key != "source_key"}

    share_keys = {CANONICAL.table_name(stem) for stem in TABLE_STEMS}
    edges = [
        {
            "source_table_full_name": CANONICAL.table(stem),
            "target_table_full_name": "mip.silver.property_master",
            "event_time": "2026-10-01 00:00:00",
            "event_count": 1,
        }
        for stem in TABLE_STEMS
    ]
    return {
        "registry": [
            without_source_key({**dataclasses.asdict(d), "fqn": d.fqn, "logical_fqn": d.logical_fqn})
            for d in ASSET_DESCRIPTORS
            if d.key in share_keys
        ],
        "lineage": [
            {"family": family["id"], **without_source_key(node)}
            for family in build_manifest_response().model_dump(mode="json")["families"]
            for node in family["nodes"]
            if node["layer"] == "raw_share"
        ],
        "observed": [without_source_key(node) for node in observed("property_master", edges)],
        "guards": verdicts(_canonical_share_questions()),
    }


def run_fresh(env: Mapping[str, str], probe: str, cwd: Path, payload: object = None) -> Any:
    """Run ``probe`` in a child started with exactly ``env``; return its JSON."""

    completed = subprocess.run(
        [sys.executable, "-P", "-m", "tests.fixtures.cotality_source_probe", probe],
        cwd=cwd,
        env={**env, "PYTHONPATH": str(REPO)},
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    if completed.returncode != 0:
        raise AssertionError(f"probe {probe} failed: {completed.stderr[-2000:]}")
    return json.loads(completed.stdout.splitlines()[-1])


def _main(probe: str) -> None:
    payload = json.loads(sys.stdin.read() or "null")
    if probe == "snapshot":
        result: Any = snapshot()
    elif probe == "verdicts":
        result = verdicts(payload)
    elif probe == "observed":
        result = observed(payload["asset_key"], payload["rows"])
    elif probe == "source_assets":
        result = source_assets(payload)
    elif probe == "d0_share":
        result = d0_share()
    else:
        raise SystemExit(f"unknown probe: {probe}")
    sys.stdout.write("\n" + json.dumps(result, sort_keys=True) + "\n")


if __name__ == "__main__":
    _main(sys.argv[1])
