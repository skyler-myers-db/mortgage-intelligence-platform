"""The paychex-shaped bundle render changes only by the jobs identity (M2-PR5).

databricks.yml is resolved for the ``client`` target with a paychex-shaped
profile's bundle variables (tests/fixtures/client_profiles.py; every
``${var.*}`` substituted, platform references such as ``${resources.*}`` left
as written) and each resource M2-PR5 touches is hashed after removing exactly
what M2-PR5 adds:

* every ``run_as`` (the four App-triggerable jobs, mip_scheduled_refresh and
  the pipeline move to the jobs principal);
* the ``converge_governed_owner`` task and the run's verdict after it
  (``assert_refresh_succeeded``), and a job environment no remaining task
  uses (the one the handover runs in);
* ``mip_lakebase_migrate``'s ``--jobs-reader-client-id`` parameter (that
  job's digest is not pinned, see TOUCHED; the normalizer still strips it for
  the whole-bundle comparison below);
* the lifecycle task's ``python_file``, which names its job-only entrypoint
  (jobs/sync_lifecycle_state_job.py runs the install pins, then the
  App-imported jobs/sync_lifecycle_state.py it replaces here).

The digests must equal a golden written from origin/main 3d37e347, before
M2-PR5, by the same resolver and normalizer
(``tests/fixtures/jobs_identity_bundle_golden.json``), so anything else that
changed in those resources is a review finding, not noise. Regenerate it only
for a reviewed change to those resources:

    PYTHONPATH=. python tests/unit/test_jobs_identity_render.py --regen

The PR that wrote the golden ran this normalizer over the whole resolved
bundle on both trees, not only these resources, and found the two equal.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.fixtures.client_profiles import paychex_shaped_profile  # noqa: E402
from tools.clients import profile as views  # noqa: E402
from tools.clients.environment import RawProfile, resolve  # noqa: E402

GOLDEN_PATH = ROOT / "tests" / "fixtures" / "jobs_identity_bundle_golden.json"
TOUCHED = (
    "jobs.mip_sync_lifecycle_state",
    "jobs.mip_fred_rates_ingest",
    "jobs.mip_refresh_silver",
    "jobs.mip_refresh_scores",
    "jobs.mip_scheduled_refresh",
    "pipelines.mip_feature_pipeline",
)
# mip_lakebase_migrate gains only --jobs-reader-client-id (asserted below and in
# tests/unit/test_lakebase_jobs_reader.py); its digest is not pinned here, so a
# parallel change to that task's other parameters does not collide with this one.
OWNER_TASK = "converge_governed_owner"
ADDED_TASKS = frozenset({OWNER_TASK, "assert_refresh_succeeded"})
JOB_ENTRYPOINTS = {"jobs/sync_lifecycle_state_job.py": "jobs/sync_lifecycle_state.py"}
JOBS_READER_PARAMETER = re.compile(r"--jobs-reader-client-id=")
_VARIABLE = re.compile(r"\$\{var\.([a-z_]+)\}")
_GOLDEN_NOTE = (
    "Written from origin/main 3d37e347 (before M2-PR5) by this test's resolver and normalizer "
    "with the paychex-shaped profile. A diff is a change to a resource M2-PR5 touched beyond "
    "its run_as, its converge_governed_owner task and the lifecycle job's entrypoint: review it."
)


def _variables(bundle: dict[str, Any]) -> dict[str, str]:
    profile = resolve(RawProfile(values=paychex_shaped_profile()))
    values: dict[str, Any] = {
        name: spec["default"]
        for name, spec in bundle["variables"].items()
        if isinstance(spec, dict) and "default" in spec
    }
    values.update(views.bundle_vars(profile))
    # A default may name another variable (${var.deployer_principal} before M2-PR5).
    for _ in range(4):
        values = _substitute(values, dict(values))
    return values


def _substitute(node: Any, values: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        return {key: _substitute(item, values) for key, item in node.items()}
    if isinstance(node, list):
        return [_substitute(item, values) for item in node]
    if isinstance(node, str):
        return _VARIABLE.sub(lambda m: str(values.get(m.group(1), m.group(0))), node)
    return node


def resolved_resources(bundle_text: str) -> dict[str, Any]:
    """``kind.key`` -> the resource as the client target resolves it."""

    bundle = yaml.safe_load(bundle_text)
    resources = copy.deepcopy(bundle["resources"])
    for kind, overrides in bundle["targets"]["client"].get("resources", {}).items():
        for key, override in overrides.items():
            resources[kind][key].update(override)
    values = _variables(bundle)
    return {
        f"{kind}.{key}": _substitute(resource, values)
        for kind, entries in resources.items()
        for key, resource in entries.items()
    }


def normalized(resource: dict[str, Any]) -> dict[str, Any]:
    """The resource without what M2-PR5 adds (module docstring)."""

    value = copy.deepcopy(resource)
    value.pop("run_as", None)
    tasks = [task for task in value.get("tasks", []) if task.get("task_key") not in ADDED_TASKS]
    for task in tasks:
        python = task.get("spark_python_task")
        if python:
            python["python_file"] = JOB_ENTRYPOINTS.get(python["python_file"], python["python_file"])
        if python and "parameters" in python:
            python["parameters"] = [
                item for item in python["parameters"] if not JOBS_READER_PARAMETER.match(item)
            ]
    if "tasks" in value:
        value["tasks"] = tasks
        used = {task.get("environment_key") for task in tasks}
        environments = [env for env in value.get("environments", []) if env["environment_key"] in used]
        if environments:
            value["environments"] = environments
        else:
            value.pop("environments", None)
    return value


def digest(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def digests(bundle_text: str, keys: tuple[str, ...] | None = None) -> dict[str, str]:
    resolved = resolved_resources(bundle_text)
    return {key: digest(normalized(resolved[key])) for key in (keys or tuple(sorted(resolved)))}


BUNDLE_TEXT = (ROOT / "databricks.yml").read_text(encoding="utf-8")


def test_the_touched_resources_equal_the_pre_m2_pr5_golden_after_normalizing() -> None:
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    assert golden["resources"] == digests(BUNDLE_TEXT, TOUCHED)


def test_the_normalizer_removes_only_what_m2_pr5_adds() -> None:
    resolved = resolved_resources(BUNDLE_TEXT)
    profile = resolve(RawProfile(values=paychex_shaped_profile()))
    jobs_principal = views.client_target_vars(profile)["jobs_principal"]
    deployer = views.client_target_vars(profile)["deployer_principal"]
    for key in TOUCHED:
        before, after = resolved[key], normalized(resolved[key])
        assert before["run_as"] == {"service_principal_name": jobs_principal}, key
        assert "run_as" not in after
    owner_tasks = {
        key: [task for task in resolved[key]["tasks"] if task["task_key"] == OWNER_TASK]
        for key in TOUCHED
        if key.startswith("jobs.")
    }
    assert {key for key, tasks in owner_tasks.items() if tasks} == {
        "jobs.mip_refresh_silver",
        "jobs.mip_refresh_scores",
        "jobs.mip_fred_rates_ingest",
    }
    assert all(len(tasks) <= 1 for tasks in owner_tasks.values())
    # The verdict leaf rides with the handover, in exactly the same jobs.
    for key in owner_tasks:
        added = sorted(t["task_key"] for t in resolved[key]["tasks"] if t["task_key"] in ADDED_TASKS)
        assert added == (sorted(ADDED_TASKS) if owner_tasks[key] else []), key
    migrate = resolved["jobs.mip_lakebase_migrate"]
    assert migrate["run_as"] == {"service_principal_name": deployer}
    parameters = migrate["tasks"][0]["spark_python_task"]["parameters"]
    assert f"--jobs-reader-client-id={jobs_principal}" in parameters
    kept = normalized(migrate)["tasks"][0]["spark_python_task"]["parameters"]
    assert not any(JOBS_READER_PARAMETER.match(item) for item in kept)
    # A resource M2-PR5 did not touch normalizes to itself apart from run_as.
    untouched = resolved["jobs.mip_ref_seed"]
    assert normalized(untouched) == {k: v for k, v in untouched.items() if k != "run_as"}


def _regen() -> None:
    GOLDEN_PATH.write_text(
        json.dumps({"note": _GOLDEN_NOTE, "resources": digests(BUNDLE_TEXT, TOUCHED)}, indent=2)
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    if sys.argv[1:] == ["--regen"]:
        _regen()
    else:
        raise SystemExit("usage: python tests/unit/test_jobs_identity_render.py --regen")
