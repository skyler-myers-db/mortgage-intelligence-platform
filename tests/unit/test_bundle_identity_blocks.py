"""databricks.yml declares no bundle-wide run_as, no bundle-wide permissions and no legacy run_as (wave 7f).

Each job and the pipeline name their own run_as; the bundle and its targets
never do. Without a bundle-wide ``run_as`` the CLI's run_as mutator is a no-op
(v1.10.0), and each of these blocks would make the CLI plan objects the
reviewed plan never had:

* a top-level (or a target's) ``permissions`` gives every resource a
  ``.permissions`` child node, which the gate refuses as ``unknown_kind``;
* ``experimental.use_legacy_run_as`` with a ``run_as`` makes the run_as
  mutator grant that identity ``IS_OWNER`` on the pipelines through
  ``.permissions`` children, refused the same way;
* in an adoption the gate first judges the post-bind plan (the pre-bind plan
  at 10B-1 runs no gate), so that refusal comes after the 16 binds and costs
  their undo.

So the bundle file refuses them up front. tests/unit/test_bundle_plan_gate_kinds.py
keeps the gate's lookup table equal to the declared kinds, child nodes included.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]


def identity_block_refusals(body: dict[str, Any]) -> list[str]:
    """Every bundle-wide identity block in a parsed databricks.yml, top level and per target."""

    scopes = [("the bundle", body)]
    scopes += [(f"target {name}", target or {}) for name, target in (body.get("targets") or {}).items()]
    found = []
    for where, scope in scopes:
        for block in ("run_as", "permissions"):
            if block in scope:
                found.append(f"{where} declares {block}")
        if "use_legacy_run_as" in (scope.get("experimental") or {}):
            found.append(f"{where} declares experimental.use_legacy_run_as")
    return found


def _bundle() -> dict[str, Any]:
    return yaml.safe_load((REPO / "databricks.yml").read_text(encoding="utf-8"))


def test_the_bundle_declares_no_bundle_wide_identity_block() -> None:
    assert identity_block_refusals(_bundle()) == []


def test_every_job_and_the_pipeline_still_name_their_own_run_as() -> None:
    resources = _bundle()["resources"]
    owners = {**resources["jobs"], **resources["pipelines"]}

    assert owners and all("run_as" in config for config in owners.values()), "per-resource run_as is the reviewed form"


@pytest.mark.parametrize(
    ("inject", "refusal"),
    (
        (lambda body: body.update(run_as={"service_principal_name": "x"}), "the bundle declares run_as"),
        (lambda body: body.update(permissions=[{"level": "CAN_VIEW", "group_name": "users"}]), "the bundle declares permissions"),
        (lambda body: body.update(experimental={"use_legacy_run_as": True}), "the bundle declares experimental.use_legacy_run_as"),
        (lambda body: body["targets"]["client"].update(run_as={"user_name": "x"}), "target client declares run_as"),
        (lambda body: body["targets"]["client"].update(permissions=[]), "target client declares permissions"),
        (
            lambda body: body["targets"]["ci"].update(experimental={"use_legacy_run_as": False}),
            "target ci declares experimental.use_legacy_run_as",
        ),
    ),
    ids=("run_as", "permissions", "legacy-run-as", "target-run_as", "target-permissions", "target-legacy-run-as"),
)
def test_each_block_injected_is_refused(inject: Any, refusal: str) -> None:
    """Positive control: the check sees each block where the CLI would read it."""

    body = _bundle()
    inject(body)

    assert identity_block_refusals(body) == [refusal]
