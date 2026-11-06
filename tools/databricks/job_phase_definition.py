"""The live definition of a governed data-phase job, read-only (M1-PR6).

``tools/databricks/job_phase_runner.py`` runs a data job only under the
definition its read-only plan digested here, before the lease, and recomputed
under it:

* the job is the one this deployer's bundle state binds
  (``tools/databricks/bundle_state.py``), and so is every job its
  ``run_job_task`` closure starts and every pipeline a ``pipeline_task``
  starts;
* each was deployed by this deployer's bundle: ``deployment.kind`` BUNDLE and
  its metadata file under the client target's root (an unspiked assumption no
  plan section 7 spike covers: the first governed data phase owes the live
  capture of both fields, and any other shape refuses);
* the **definition digest** is the sha256 of the closure's bound ids, live job
  settings and pipeline specs, so a rebind (a job recreated under a new id),
  an edit, or a bundle deploy that changes any of them between the plan, the
  run and the ledger's earlier attempt is a different definition. It does not
  cover the deployed files a task names by path (the SQL and Python content):
  a release that changes only those keeps the digest, so a job that succeeded
  in the open generation still skips (the rerun ack retires it);
* ``touches_gold`` fails closed: a task touches gold or the UC functions
  unless it is a silver, ref or catalog-DDL SQL file or the FRED or KPI
  script; a run, pipeline or notebook task always does. The data phase
  reconciles and verifies the reviewed function EXECUTE grants after every job
  that does (attack O11).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.databricks import bundle_state, phase_budget
from tools.databricks.app_release_contract import bundle_root

PLAN_SCHEMA = "mip.data-phase-plan.v1"
_GOLD_FREE_SQL = re.compile(
    r"(?:^|/)sql/_rendered/(?:ref/[a-z0-9_]+|ddl/(?:001_catalogs_schemas|004_ref_tables|silver_[a-z0-9_]+))\.sql\Z"
)
_GOLD_FREE_PYTHON = re.compile(r"(?:^|/)jobs/(?:fred_rates_ingest|kpi_snapshot)\.py\Z")



class Refused(RuntimeError):
    """The runner refused; nothing was run and the ledger is unchanged."""


@dataclass(frozen=True)
class Definition:
    key: str
    job_id: str
    sha256: str
    touches_gold: bool
    jobs: tuple[str, ...]
    pipelines: tuple[str, ...]


def enum_value(item: object) -> str:
    return str(getattr(item, "value", item) or "")


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def task_touches_gold(task: Mapping[str, Any]) -> bool:
    """Fail closed: only a silver, ref or catalog-DDL SQL file or a FRED/KPI script is gold-free."""

    if "sql_task" in task:
        path = str(((task.get("sql_task") or {}).get("file") or {}).get("path") or "")
        return _GOLD_FREE_SQL.search(path) is None
    if "spark_python_task" in task:
        path = str((task.get("spark_python_task") or {}).get("python_file") or "")
        return _GOLD_FREE_PYTHON.search(path) is None
    return True


def _assert_bundle_deployed(kind: str, key: str, deployment: object, *, root: str) -> None:
    deployment = deployment if isinstance(deployment, Mapping) else {}
    path = str(deployment.get("metadata_file_path") or "")
    expected = f"{root}/state/metadata.json"
    if enum_value(deployment.get("kind")) != "BUNDLE" or path not in {expected, expected.removeprefix("/Workspace")}:
        raise Refused(f"{kind}.{key} is not deployed by this deployer's bundle under the client target's root")


def _reverse(document: Mapping[str, Any] | None, kind: str) -> dict[str, str]:
    """Live id -> bundle key for every ``resources.<kind>.<key>`` the state binds."""

    prefix = f"resources.{kind}."
    mapping: dict[str, str] = {}
    for name in (document or {}).get("state", {}):
        if name.startswith(prefix) and name.count(".") == 2:
            live = bundle_state.bound_id(document, name)
            if live is not None:
                mapping[live] = name.removeprefix(prefix)
    return mapping


def definition(workspace: Any, *, document: Mapping[str, Any] | None, root: str, job_key: str) -> Definition:
    """The live definition of ``job_key``'s closure, read-only, refusing anything unbound."""

    if job_key not in phase_budget.DATA_JOBS:
        raise Refused(f"{job_key} is not a reviewed data-phase job")
    job_id = bundle_state.bound_id(document, f"resources.jobs.{job_key}")
    if job_id is None:
        raise Refused(f"this deployer's bundle state binds no jobs.{job_key}; run the release first")
    jobs_by_id, pipelines_by_id = _reverse(document, "jobs"), _reverse(document, "pipelines")
    settings: dict[str, Any] = {}
    specs: dict[str, Any] = {}
    # The bound ids are part of the definition: a job recreated under a new id
    # with byte-identical settings is another job (the ledger names the old one).
    ids: dict[str, dict[str, str]] = {"jobs": {}, "pipelines": {}}
    touches = False
    frontier = [(job_key, job_id)]
    while frontier:
        key, live_id = frontier.pop()
        if key in settings:
            continue
        body = workspace.jobs.get(int(live_id)).settings.as_dict()
        _assert_bundle_deployed("jobs", key, body.get("deployment"), root=root)
        if body.get("name") != key:
            raise Refused(f"jobs.{key} is named otherwise than its bundle key")
        settings[key] = body
        ids["jobs"][key] = str(live_id)
        for task in body.get("tasks") or ():
            touches = touches or task_touches_gold(task)
            child = str((task.get("run_job_task") or {}).get("job_id") or "")
            if child:
                if child not in jobs_by_id:
                    raise Refused(f"jobs.{key} starts a job this deployer's bundle state does not bind")
                frontier.append((jobs_by_id[child], child))
            pipeline = str((task.get("pipeline_task") or {}).get("pipeline_id") or "")
            if pipeline:
                if pipeline not in pipelines_by_id:
                    raise Refused(f"jobs.{key} starts a pipeline this deployer's bundle state does not bind")
                spec = workspace.pipelines.get(pipeline).spec.as_dict()
                _assert_bundle_deployed("pipelines", pipelines_by_id[pipeline], spec.get("deployment"), root=root)
                specs[pipelines_by_id[pipeline]] = spec
                ids["pipelines"][pipelines_by_id[pipeline]] = pipeline
    digest = hashlib.sha256(
        b"mip-data-phase-definition\0" + canonical_json({"ids": ids, "jobs": settings, "pipelines": specs})
    ).hexdigest()
    return Definition(job_key, job_id, digest, touches, tuple(sorted(settings)), tuple(sorted(specs)))


def profile_install(environ: Mapping[str, str], env_file: Path) -> tuple[str, str]:
    from tools.clients.environment import from_local, resolve

    profile = resolve(from_local(environ, env_file))
    return profile.identity_ids.deployer.strip().lower(), profile.client


def live_definitions(workspace: Any, *, deployer: str, client_key: str, jobs: Sequence[str]) -> list[Definition]:
    """Each job's live definition, from one read of this deployer's bundle state."""

    document = bundle_state.read_state(workspace, bundle_state.state_path(deployer=deployer, client_key=client_key))
    root = bundle_root(principal=deployer, target="client", root_leaf=client_key)
    return [definition(workspace, document=document, root=root, job_key=key) for key in jobs]


def build_plan(
    workspace: Any, *, deployer: str, client_key: str, app_name: str, jobs: Sequence[str], skip_silver: bool
) -> dict[str, Any]:
    """The data plan: each job's id, definition digest, closure and gold flag, and the phase budget."""

    definitions = live_definitions(workspace, deployer=deployer, client_key=client_key, jobs=jobs)
    return {
        "schema": PLAN_SCHEMA,
        "app_name": app_name,
        "client_key": client_key,
        "jobs": [
            {
                "key": item.key,
                "job_id": item.job_id,
                "definition_sha256": item.sha256,
                "touches_gold": item.touches_gold,
                "closure": list(item.jobs),
                "pipelines": list(item.pipelines),
            }
            for item in definitions
        ],
        "budget": phase_budget.predict("data", skip_silver=skip_silver),
    }


