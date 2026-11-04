"""A fake Apps estate in which ``apps start`` creates a NEW deployment (attack O1, M0-PR8).

The worst case spike S7 has not ruled out: a start never resumes the stopped
deployment. It snapshots the App's ``default_source_code_path`` into a fresh
deployment id (SNAPSHOT mode) whose ``/src/`` snapshot is a byte copy of that
folder, and the env config may or may not carry over. ``FakeApps`` does that,
with knobs for every way a start can differ from blue: another source, a
tampered snapshot, a dropped env, a FAILED start, no readable pending id.

``Estate`` also holds the workspace files (the release folder, the snapshots,
the window journal) and the secret scope holding the signed rollback record,
which ``signed_blue`` writes for blue with the rollback tests' reviewed
payload and Gateway contract.

``FakeJobs`` (M0-PR9) takes one-time ``jobs submit`` runs (one per
idempotency token) and cancels; ``run_deadman`` plays the Jobs service: it
reads the submitted task's ``python_file`` from the fake workspace, so it runs
exactly the bytes the window published, never the repo's. ``window_hooks``
gives ``open_window`` the real admission and the real arm, publishing a staged
green release that carries ``jobs/window_deadman.py``.
"""

from __future__ import annotations

import base64
import copy
import io
import json
import sys
import tempfile
import time
import types
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from databricks.sdk.errors import NotFound, ResourceAlreadyExists, ResourceDoesNotExist

from tools.databricks import app_release_contract as contract
from tools.databricks import quiesce_window_deadman as deadman
from tools.databricks.app_release_source import PublishPlan
from tools.databricks.quiesce_window_budget import require_admitted

APP_NAME = "mip-app"
RELEASE = "/Workspace/Users/deployer/.bundle/mip/client/app-src/aaaaaaaaaaaa-bbbbbbbbbbbb"
OTHER_SOURCE = "/Workspace/Users/deployer/.bundle/mip/client/files"
BLUE_ID = "deployment-blue"
BLUE_ARTIFACT = f"/Workspace/Users/app-sp/src/{BLUE_ID}"
SOURCE_FILES = {
    "app.yaml": b"command: [python, -m, backend.runtime]\n",
    "backend/runtime.py": b"print('blue')\n",
    "frontend/dist/index.html": b"<html>blue</html>\n",
}


def _api(path: str) -> str:
    return path.removeprefix("/Workspace")


class FakeFiles:
    """Workspace files by API path: download, upload, list, delete."""

    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    def put_tree(self, workspace_path: str, files: dict[str, bytes]) -> None:
        for relpath, data in files.items():
            self.data[f"{_api(workspace_path)}/{relpath}"] = data

    def download(self, path: str) -> io.BytesIO:
        if path not in self.data:
            raise NotFound("missing")
        return io.BytesIO(self.data[path])

    def upload(self, path: str, content: Any, *, format: Any = None, overwrite: bool = False) -> None:
        if path in self.data and not overwrite:
            raise ResourceAlreadyExists("AlreadyExists")
        self.data[path] = content.read()

    def mkdirs(self, _path: str) -> None:
        """Folders are implicit in the fake."""

    def delete(self, path: str) -> None:
        self.data.pop(path, None)

    def list(self, root: str, recursive: bool = False) -> list[object]:
        prefix = f"{root}/"
        if not any(path.startswith(prefix) for path in self.data):
            raise NotFound("missing folder")
        return [
            SimpleNamespace(path=path, object_type="FILE", size=len(data))
            for path, data in sorted(self.data.items())
            if path.startswith(prefix)
        ]


class FakeSecrets:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_secret(self, scope: str, key: str) -> object:
        try:
            raw = self.values[(scope, key)]
        except KeyError as exc:
            raise ResourceDoesNotExist("missing") from exc
        return SimpleNamespace(value=base64.b64encode(raw.encode()).decode())

    def put_secret(self, *, scope: str, key: str, string_value: str) -> None:
        self.values[(scope, key)] = string_value

    def delete_secret(self, scope: str, key: str) -> None:
        self.values.pop((scope, key), None)


def deployment(
    deployment_id: str,
    *,
    source: str,
    artifact: str,
    env_names: list[str],
    state: str = "SUCCEEDED",
    created: str = "2026-10-01T00:00:00Z",
) -> SimpleNamespace:
    return SimpleNamespace(
        deployment_id=deployment_id,
        source_code_path=source,
        deployment_artifacts=SimpleNamespace(source_code_path=artifact),
        env_vars=[SimpleNamespace(name=name, value=None, value_from=None) for name in env_names],
        status=SimpleNamespace(state=state),
        mode="SNAPSHOT",
        create_time=created,
        update_time=created,
    )


@dataclass
class StartBehaviour:
    """How the next ``apps start`` differs from a faithful snapshot of blue."""

    source: str | None = None  # None: the App's default source
    carry_env: bool = True
    tamper_snapshot: bool = False
    state: str = "SUCCEEDED"
    polls_in_progress: int = 1
    readable_pending_id: bool = True


@dataclass
class FakeApps:
    files: FakeFiles
    env_names: list[str]
    behaviour: StartBehaviour = field(default_factory=StartBehaviour)
    compute: str = "ACTIVE"
    default_source: str = RELEASE
    stops: int = 0
    starts: int = 0
    deploys: int = 0
    updates: int = 0
    # The App's resource bindings: app-level, so a start or a deploy keeps them.
    resources: list[dict[str, Any]] = field(default_factory=list)
    get_calls: int = 0
    deployments: dict[str, SimpleNamespace] = field(default_factory=dict)
    active_id: str = BLUE_ID
    pending_id: str = ""
    # A start whose id the API never showed: it settles on the next read.
    hidden_start: str = ""
    _polls: dict[str, int] = field(default_factory=dict)
    _final: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.deployments[BLUE_ID] = deployment(
            BLUE_ID, source=RELEASE, artifact=BLUE_ARTIFACT, env_names=self.env_names
        )

    # --- reads --------------------------------------------------------------
    def _app(self) -> SimpleNamespace:
        active = self.deployments.get(self.active_id)
        pending = self.deployments.get(self.pending_id) if self.pending_id else None
        app = SimpleNamespace(
            name=APP_NAME,
            id="app-object-id",
            service_principal_client_id="app-client",
            service_principal_id="app-scim-id",
            compute_status=SimpleNamespace(state=self.compute),
            active_deployment=active,
            pending_deployment=pending,
            default_source_code_path=self.default_source,
            url="https://mip.example",
            resources=copy.deepcopy(self.resources),
            description="",
        )
        app.as_dict = lambda: {
            "name": APP_NAME,
            "description": "",
            "resources": copy.deepcopy(self.resources),
        }
        return app

    def get(self, _name: str) -> SimpleNamespace:
        self.get_calls += 1
        if self.hidden_start:
            hidden, self.hidden_start = self.hidden_start, ""
            self._settle(hidden)
        return self._app()

    def list(self) -> list[SimpleNamespace]:
        return [self._app()]

    def list_deployments(self, _name: str) -> list[SimpleNamespace]:
        return list(self.deployments.values())

    def get_deployment(self, _name: str, deployment_id: str) -> SimpleNamespace:
        found = self.deployments[deployment_id]
        if deployment_id in self._polls:
            if self._polls[deployment_id] > 0:
                self._polls[deployment_id] -= 1
            else:
                self._settle(deployment_id)
        return found

    def _settle(self, deployment_id: str) -> None:
        found = self.deployments[deployment_id]
        del self._polls[deployment_id]
        found.status.state = self._final.pop(deployment_id)
        self.pending_id = ""
        if found.status.state == "SUCCEEDED":
            self.active_id = deployment_id
            self.compute = "ACTIVE"
            self.default_source = found.source_code_path

    # --- primitives ---------------------------------------------------------
    def stop(self, _name: str) -> object:
        self.stops += 1
        self.compute = "STOPPED"
        return SimpleNamespace(response=self._app())

    def wait_get_app_stopped(self, _name: str, timeout: object = None) -> object:
        self.compute = "STOPPED"
        return self._app()

    def start(self, _name: str) -> object:
        self.starts += 1
        new_id = f"deployment-start-{self.starts}"
        source = self.behaviour.source or self.default_source
        artifact = f"/Workspace/Users/app-sp/src/{new_id}"
        snapshot = {
            path.removeprefix(f"{_api(source)}/"): data
            for path, data in self.files.data.items()
            if path.startswith(f"{_api(source)}/")
        }
        if self.behaviour.tamper_snapshot:
            snapshot["backend/runtime.py"] = b"print('not blue')\n"
        self.files.put_tree(artifact, snapshot)
        names = self.env_names if self.behaviour.carry_env else ["DATABRICKS_WAREHOUSE_ID"]
        self.deployments[new_id] = deployment(
            new_id,
            source=source,
            artifact=artifact,
            env_names=names,
            state="IN_PROGRESS",
            created=f"2026-10-02T00:00:0{self.starts}Z",
        )
        self.pending_id = new_id
        self.compute = "STARTING"
        self._polls[new_id] = self.behaviour.polls_in_progress
        self._final[new_id] = self.behaviour.state
        pending = SimpleNamespace(deployment_id=new_id if self.behaviour.readable_pending_id else "")
        if not self.behaviour.readable_pending_id:
            self.pending_id = ""
            self.hidden_start = new_id
        return SimpleNamespace(response=SimpleNamespace(pending_deployment=pending))

    def update(self, _name: str, app: object) -> object:
        """A binding update: resources only, never the deployment or the compute."""

        self.updates += 1
        self.resources = copy.deepcopy(app.as_dict()["resources"])
        return self._app()

    def deploy_and_wait(self, _name: str, app_deployment: object, *, timeout: object) -> object:
        self.deploys += 1
        new_id = f"deployment-redeploy-{self.deploys}"
        payload = app_deployment.as_dict()
        artifact = f"/Workspace/Users/app-sp/src/{new_id}"
        self.deployments[new_id] = deployment(
            new_id,
            source=str(payload["source_code_path"]),
            artifact=artifact,
            env_names=[str(item["name"]) for item in payload["env_vars"]],
            created=f"2026-10-03T00:00:0{self.deploys}Z",
        )
        self.active_id = new_id
        self.compute = "ACTIVE"
        return self.deployments[new_id]


@dataclass
class FakeJobs:
    """One-time runs: submit (one per idempotency token), get and cancel; never a delete."""

    runs: dict[int, SimpleNamespace] = field(default_factory=dict)
    tokens: dict[str, int] = field(default_factory=dict)
    submits: int = 0
    cancels: list[int] = field(default_factory=list)
    fail_submit: bool = False

    def submit(self, **request: Any) -> SimpleNamespace:
        self.submits += 1
        if self.fail_submit:
            raise ConnectionError("the Jobs API is unreachable")
        token = str(request["idempotency_token"])
        if token not in self.tokens:
            run_id = 9000 + len(self.runs) + 1
            self.runs[run_id] = SimpleNamespace(
                run_id=run_id,
                run_name=request["run_name"],
                tasks=list(request["tasks"]),
                state=SimpleNamespace(life_cycle_state="RUNNING"),
                request=request,
            )
            self.tokens[token] = run_id
        return SimpleNamespace(response=SimpleNamespace(run_id=self.tokens[token]))

    def get_run(self, run_id: int) -> SimpleNamespace:
        return self.runs[run_id]

    def cancel_run(self, run_id: int) -> SimpleNamespace:
        self.cancels.append(run_id)
        self.runs[run_id].state.life_cycle_state = "TERMINATED"
        return SimpleNamespace(response=None)

    def only_run(self) -> SimpleNamespace:
        assert len(self.runs) == 1, f"{len(self.runs)} dead-man runs"
        return next(iter(self.runs.values()))


@dataclass
class Estate:
    files: FakeFiles
    secrets: FakeSecrets
    apps: FakeApps
    jobs: FakeJobs = field(default_factory=FakeJobs)

    @property
    def workspace(self) -> SimpleNamespace:
        return SimpleNamespace(
            apps=self.apps, secrets=self.secrets, workspace=self.files, jobs=self.jobs
        )


def make_estate(env_names: list[str], behaviour: StartBehaviour | None = None) -> Estate:
    files = FakeFiles()
    files.put_tree(RELEASE, SOURCE_FILES)
    files.put_tree(BLUE_ARTIFACT, SOURCE_FILES)
    apps = FakeApps(files=files, env_names=env_names, behaviour=behaviour or StartBehaviour())
    return Estate(files=files, secrets=FakeSecrets(), apps=apps)


def stored_record(estate: Estate, *, scope: str = "mip") -> dict[str, Any]:
    from tools.databricks.app_rollback_record_contract import _record_key

    return json.loads(estate.secrets.values[(scope, _record_key(APP_NAME))])



# --- the dead-man (M0-PR9) ------------------------------------------------------------

REPO = Path(__file__).resolve().parents[2]
GREEN_COMMIT = "c" * 40
DEPLOYER = "deployer-app-id-PLACEHOLDER"
CLIENT_KEY = "client-key"
DEADMAN_BYTES = (REPO / "jobs" / "window_deadman.py").read_bytes()
# The rollback tests' lease (tests/unit/test_app_deployment_rollback.py LEASE_ID).
LEASE_ID = "11111111-1111-4111-8111-111111111111"


def green_release(
    stage_root: Path | None = None, *, extra: dict[str, bytes] | None = None
) -> PublishPlan:
    """This run's staged green release: blue's files plus the dead-man, as step 1 stages it."""

    root = Path(stage_root or tempfile.mkdtemp(prefix="mip-deadman-stage-"))
    stage = root / "source"
    files = {**SOURCE_FILES, deadman.DEADMAN_RELPATH: DEADMAN_BYTES, **(extra or {})}
    for relpath, data in files.items():
        path = stage / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest = contract.write_manifest(
        root / "manifest.json", commit=GREEN_COMMIT, entries=contract.manifest_entries(stage)
    )
    release_path = contract.release_path(
        principal=DEPLOYER,
        target="client",
        root_leaf=CLIENT_KEY,
        release_id=manifest.release_id,
        commit=GREEN_COMMIT,
    )
    return PublishPlan(manifest, stage, release_path, 1)


def window_hooks(
    estate: Estate,
    *,
    budget_s: float = 3 * 3600,
    release: PublishPlan | None = None,
    app_id: str = "app-object-id",
) -> dict[str, Any]:
    """The open's M0-PR9 hooks for a run with ``budget_s`` seconds of job budget left.

    ``arm`` is the real one: it publishes ``release`` to the fake workspace,
    re-reads the dead-man's bytes, re-admits and submits to ``FakeJobs``.
    """

    from tools.databricks.quiesce_window_plan import body_sha256

    deadline = int(time.time() + budget_s)
    plan = release or green_release()

    def admit(canonical: Any) -> object:
        return require_admitted(canonical, str(deadline), now=time.time())

    def arm(canonical: Any) -> str:
        return deadman.arm_deadman(
            estate.workspace,
            app_name=APP_NAME,
            app_id=app_id,
            canonical=canonical,
            window_sha256=body_sha256(canonical),
            lease_id=LEASE_ID,
            release=plan,
            deadline=deadline,
            admit=lambda: admit(canonical),
        )

    def cancel(run_id: str) -> str:
        return deadman.cancel_deadman(estate.workspace, app_name=APP_NAME, run_id=run_id)

    return {"admit": admit, "arm": arm, "cancel": cancel}


def load_deadman(source: bytes, name: str = "window_deadman_published") -> dict[str, Any]:
    """The dead-man module from exactly ``source``, as the job runs that one file."""

    module = types.ModuleType(name)
    module.__file__ = "window_deadman.py"
    sys.modules[name] = module
    exec(compile(source, "window_deadman.py", "exec"), module.__dict__)  # noqa: S102
    return module.__dict__


def run_deadman(
    estate: Estate,
    run_id: int,
    *,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> int:
    """The Jobs service: run the submitted task's published file with its parameters."""

    task = estate.jobs.get_run(run_id).tasks[0].spark_python_task
    source = estate.files.download(contract.api_path(task.python_file)).read()
    module = load_deadman(source)
    return int(
        module["main"](
            list(task.parameters),
            workspace_factory=lambda: estate.workspace,
            clock=clock,
            sleep=sleep,
        )
    )
