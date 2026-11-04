"""bundle_env edits deployment state for a non-App key only under an acknowledged adopt plan (M2-PR6).

``bundle_env deployment bind|unbind`` admits the governed App binding, as
before, and otherwise exactly one bind or unbind that an adopt plan names,
given the plan file and its acknowledged sha256 (wrapper-only flags that never
reach the CLI).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fixtures import adopt_estate as es
from tools.databricks import bundle_env
from tools.databricks import install_state_adopt as adopt


@pytest.fixture
def acked(tmp_path: Path) -> tuple[tuple[str, str], adopt.legacy_map.AdoptPlan]:
    estate = es.Estate()
    plan = adopt.compute_plan("adopt", es.inputs(), es.FakeBundleCli(estate), estate)
    path = tmp_path / "adopt-plan.json"
    path.write_text(json.dumps(plan.document), encoding="utf-8")
    return (str(path), plan.sha256), plan


def _check(args: list[str], authority: tuple[str, str] | None) -> str | None:
    return bundle_env._validate_app_deployment_command(args, expected_app_name="synthetic-mip_app", adopt_authority=authority)


def test_exactly_the_planned_bind_and_its_inverse_are_admitted(acked: tuple[tuple[str, str], object]) -> None:
    authority, plan = acked
    job = next(edit for edit in plan.edits if edit.resource.startswith("jobs."))  # type: ignore[attr-defined]

    assert _check(["bind", job.key, job.live_id, "-t", "client", "--auto-approve"], authority) is None
    assert _check(["unbind", job.key, "-t", "client"], authority) is None
    assert _check(["bind", "mip_app", "synthetic-mip_app", "-t", "client", "--auto-approve"], authority) is None


@pytest.mark.parametrize(
    "args",
    (
        ["bind", "{key}", "999999", "-t", "client", "--auto-approve"],
        ["bind", "mip_scheduled_refresh", "1", "-t", "client", "--auto-approve"],
        ["bind", "{key}", "{id}", "-t", "ci", "--auto-approve"],
        ["bind", "{key}", "{id}", "extra", "-t", "client"],
        ["bind", "{key}", "{id}", "-t", "client", "--plan", "x"],
        ["bind", "mip_app", "another-app", "-t", "client", "--auto-approve"],
        ["unbind", "mip_scheduled_refresh", "-t", "client"],
    ),
    ids=("another-id", "an-unplanned-key", "another-target", "an-extra-argument", "an-unknown-flag", "another-app", "an-unplanned-unbind"),
)
def test_anything_else_is_refused(acked: tuple[tuple[str, str], object], args: list[str]) -> None:
    authority, plan = acked
    job = next(edit for edit in plan.edits if edit.resource.startswith("jobs."))  # type: ignore[attr-defined]
    filled = [arg.format(key=job.key, id=job.live_id) for arg in args]

    assert _check(filled, authority) is not None


def test_a_tampered_or_unacknowledged_plan_admits_nothing(acked: tuple[tuple[str, str], object], tmp_path: Path) -> None:
    (path, sha), plan = acked
    job = next(edit for edit in plan.edits if edit.resource.startswith("jobs."))  # type: ignore[attr-defined]
    args = ["bind", job.key, job.live_id, "-t", "client", "--auto-approve"]

    assert _check(args, (path, "0" * 64)) is not None
    assert _check(args, (str(tmp_path / "absent.json"), sha)) == "the adopt plan could not be read"
    Path(path).write_text(Path(path).read_text(encoding="utf-8").replace('"bind"', '"bind" ', 1), encoding="utf-8")
    assert _check(args, (path, sha)) is None, "whitespace does not change the canonical digest"
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    document["bind"][0]["id"] = "1"
    Path(path).write_text(json.dumps(document), encoding="utf-8")
    assert _check(args, (path, sha)) is not None
    assert _check(args, None) is not None, "without a plan only the App binding is admitted"


@pytest.mark.parametrize(
    ("args", "kept", "authority", "error"),
    (
        (["bind", "k", "1", "-t", "client"], ["bind", "k", "1", "-t", "client"], None, None),
        (["bind", "k", "1", "--adopt-plan", "p", "--adopt-plan-sha256", "s"], ["bind", "k", "1"], ("p", "s"), None),
        (["bind", "k", "--adopt-plan=p", "--adopt-plan-sha256=s"], ["bind", "k"], ("p", "s"), None),
        (["bind", "k", "--adopt-plan", "p"], None, None, "--adopt-plan and --adopt-plan-sha256 go together"),
        (["bind", "k", "--adopt-plan", "p", "--adopt-plan", "q", "--adopt-plan-sha256", "s"], None, None, "--adopt-plan must be given exactly once, with a value"),
        (["bind", "k", "--adopt-plan-sha256"], None, None, "--adopt-plan-sha256 requires a value"),
    ),
)
def test_the_wrapper_flags_go_together_once_each_and_never_reach_the_cli(
    args: list[str], kept: list[str] | None, authority: tuple[str, str] | None, error: str | None
) -> None:
    result = bundle_env._split_adopt_authority(args)

    assert result[2] == error
    if error is None:
        assert result[0] == kept and result[1] == authority
