"""A fake Apps estate with the Apps API spike S7 measured (attack O1, M0-PR8).

``FakeApps`` models tests/fixtures/measured_apps.py: a start never resumes the
stopped deployment. It snapshots the App's ``default_source_code_path`` into
a fresh deployment id (SNAPSHOT mode) whose ``/src/`` snapshot is a byte copy
of that folder, with the newest deployment's env-name set. The start response
is empty and the new deployment shows only after ``HIDDEN_READS`` reads (the
measured ~100 s), then pending for one GET, then active; a STOPPED App's GET
has no active (or pending) deployment; a FAILED deployment stays active and
moves the default source; ``update`` never moves it and ``create_update``
rejects its mask. Knobs cover every way a start can differ from blue: another
source, a tampered snapshot, a dropped env, a FAILED start, a deployment that
never shows, a second new one with it. A deployment shows what spike S7
recorded of one: env NAMES only (never a value or a ``value_from``), mode
SNAPSHOT, no command, no git source, and as its creator the caller that made
it (``FakeApps.caller``: the deployer, or the owner for a break-glass start).

``Estate`` also holds the workspace files (the release folder, the snapshots,
the window journal) and the secret scope holding the signed rollback record,
which ``signed_blue`` writes for blue with the rollback tests' reviewed
payload and Gateway contract.

``FakeJobs`` (M0-PR9) takes one-time ``jobs submit`` runs (one per
idempotency token) and cancels. A submitted run starts the way ``start``
says: ``arms`` runs the PUBLISHED dead-man's ``announce`` (its armed marker),
``fails`` ends INTERNAL_ERROR before it starts, ``silent`` stays RUNNING and
never arms. ``run_deadman`` plays the Jobs service: it reads the submitted
task's ``python_file`` from the fake workspace, so it runs exactly the bytes
the window published, never the repo's. ``window_hooks``
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

from tests.fixtures.measured_app_updates import UpdateLedger, apply_create_update, apply_patch
from tests.fixtures.measured_apps import HIDDEN_READS, StartVisibility
from tools.databricks import app_release_contract as contract
from tools.databricks import quiesce_window_deadman as deadman
from tools.databricks.app_release_source import PublishPlan
from tools.databricks.quiesce_window_budget import require_admitted

APP_NAME = "mip-app"
RELEASE = "/Workspace/Users/deployer/.bundle/mip/client/app-src/aaaaaaaaaaaa-bbbbbbbbbbbb"
OTHER_SOURCE = "/Workspace/Users/deployer/.bundle/mip/client/files"
BLUE_ID = "deployment-blue"
BLUE_ARTIFACT = f"/Workspace/Users/app-sp/src/{BLUE_ID}"
# Synthetic callers: the CI deployer every governed call runs as, and the owner's own login.
DEPLOYER_CALLER = "deployer-caller"
OWNER_CALLER = "owner-caller"
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
    creator: str = DEPLOYER_CALLER,
) -> SimpleNamespace:
    return SimpleNamespace(
        deployment_id=deployment_id,
        source_code_path=source,
        deployment_artifacts=SimpleNamespace(source_code_path=artifact),
        # Names only, as S7 measured (env_keys ['name']): a deploy's literal
        # values and value_from bindings never show in get_deployment.
        env_vars=[SimpleNamespace(name=name, value=None, value_from=None) for name in env_names],
        status=SimpleNamespace(state=state),
        mode="SNAPSHOT",
        command=None,
        git_source=None,
        creator=creator,
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
    hidden_reads: int = HIDDEN_READS  # the measured ~100 s before the id shows
    never_visible: bool = False  # the start's deployment never shows (start_timeout)
    twin: bool = False  # a second new deployment shows with it (start_ambiguous)


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
    # Who makes the next start or deploy (a deployment's creator, S7-1a).
    caller: str = DEPLOYER_CALLER
    # Its description, and what each update did (measured_app_updates, spike S2).
    description: str = ""
    update_ledger: UpdateLedger = field(default_factory=UpdateLedger)
    get_calls: int = 0
    deployments: dict[str, SimpleNamespace] = field(default_factory=dict)
    # The App's last active deployment; a STOPPED (or STARTING) App's GET omits it.
    active_id: str = BLUE_ID
    pending_id: str = ""
    # Not a measured shape: a pending deployment a STOPPED App's GET shows, for
    # the dead-man's defensive branch only.
    unmeasured_stopped_pending: str = ""
    _start: StartVisibility | None = None
    _hidden: dict[str, SimpleNamespace] = field(default_factory=dict)
    _polls: dict[str, int] = field(default_factory=dict)
    _final: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.deployments[BLUE_ID] = deployment(
            BLUE_ID, source=RELEASE, artifact=BLUE_ARTIFACT, env_names=self.env_names
        )

    # --- reads --------------------------------------------------------------
    def _app(self) -> SimpleNamespace:
        # Measured: no active deployment while STOPPED, STARTING or a start's id is
        # still pending. Failure injection (``never_visible``) keeps the last one.
        serving = self.compute not in {"STOPPED", "STARTING"}
        in_flight = self._start is not None and not self._start.never
        active = self.deployments.get(self.active_id) if serving and not in_flight else None
        pending = self.deployments.get(self.pending_id) if self.pending_id and serving else None
        if self.compute == "STOPPED" and self.unmeasured_stopped_pending:
            pending = SimpleNamespace(deployment_id=self.unmeasured_stopped_pending)
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
            description=self.description,
        )
        app.as_dict = lambda: {
            "name": APP_NAME,
            "description": self.description,
            "resources": copy.deepcopy(self.resources),
        }
        return app

    def _tick(self, *, is_get: bool) -> None:
        """One read of a start in flight: hidden, then listed or pending, then active (S7)."""

        start = self._start
        if start is None:
            return
        phase = start.read(is_get=is_get)
        if phase == "hidden":
            return
        if phase == "compute_only":
            self.compute = "ACTIVE"
            return
        surfaced = [item for item in self._hidden if item.startswith(start.deployment_id)]
        for hidden_id in surfaced:
            self.deployments[hidden_id] = self._hidden.pop(hidden_id)
        if surfaced:
            self.compute = "ACTIVE"  # the id shows only once the compute is ACTIVE
        if phase == "pending":
            self.pending_id = start.deployment_id
        elif phase == "active":
            self._activate(start)

    def _activate(self, start: StartVisibility) -> None:
        """The start's deployment is active: it moved the default source, FAILED or not (S7-3)."""

        self.pending_id, self.active_id, self._start = "", start.deployment_id, None
        self.default_source = self.deployments[start.deployment_id].source_code_path

    def get(self, _name: str) -> SimpleNamespace:
        self.get_calls += 1
        self._tick(is_get=True)
        return self._app()

    def list(self) -> list[SimpleNamespace]:
        return [self._app()]

    def list_deployments(self, _name: str) -> list[SimpleNamespace]:
        self._tick(is_get=False)
        return sorted(self.deployments.values(), key=lambda item: item.create_time, reverse=True)

    def get_deployment(self, _name: str, deployment_id: str) -> SimpleNamespace:
        if deployment_id not in self.deployments:
            raise NotFound("deployment not found")
        found = self.deployments[deployment_id]
        start = self._start
        if start is not None and start.deployment_id == deployment_id:
            self._activate(start)  # a poll of a shown start: time passed, it is active
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

    # --- primitives ---------------------------------------------------------
    def stop(self, _name: str) -> object:
        self.stops += 1
        # Not measured: a start stopped before its deployment showed never shows,
        # and one that showed had become the App's last active deployment.
        if self._start is not None and self._start.phase == "hidden":
            self._hidden.clear()
            self._start = None
        elif self._start is not None:
            self._activate(self._start)
        self.compute = "STOPPED"
        return SimpleNamespace(response=self._app())

    def wait_get_app_stopped(self, _name: str, timeout: object = None) -> object:
        self.compute = "STOPPED"
        return self._app()

    def _newest(self) -> SimpleNamespace:
        return max(self.deployments.values(), key=lambda item: (item.create_time, item.deployment_id))

    def start(self, _name: str) -> object:
        """The measured start: a new deployment that shows ~100 s later; an empty response."""

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
        # The newest deployment's env-name set carries over (S7-1c).
        newest = [str(item.name) for item in self._newest().env_vars]
        names = newest if self.behaviour.carry_env else ["DATABRICKS_WAREHOUSE_ID"]
        created = f"2026-10-02T00:00:{self.starts:02d}Z"
        self._hidden[new_id] = deployment(
            new_id, source=source, artifact=artifact, env_names=names, state="IN_PROGRESS", created=created,
            creator=self.caller,
        )  # fmt: skip
        if self.behaviour.twin:
            twin = f"{new_id}-twin"
            self._hidden[twin] = deployment(
                twin, source=source, artifact=artifact, env_names=names, state="IN_PROGRESS", created=created
            )
        self.compute = "STARTING"
        self._start = StartVisibility(
            new_id, hidden=self.behaviour.hidden_reads, never=self.behaviour.never_visible
        )
        self._polls[new_id] = self.behaviour.polls_in_progress
        self._final[new_id] = self.behaviour.state
        return SimpleNamespace(
            response=SimpleNamespace(
                name=APP_NAME,
                compute_status=SimpleNamespace(state="STARTING"),
                active_deployment=None,
                pending_deployment=None,
            )
        )

    def update(self, _name: str, app: object) -> object:
        """A PATCH as measured (S2): it clears an omitted description; never a deployment or compute.

        A default_source_code_path in it is ignored (S7-2).
        """

        self.updates += 1
        apply_patch(self, app, self.update_ledger)
        return self._app()

    def create_update(self, app_name: str, update_mask: str, *, app: object = None) -> object:
        """Only the masked fields, as measured (S2, S2b); a resources mask re-grants CREATE.

        The mask check S7-2 measured (default_source_code_path is rejected) runs
        inside apply_create_update, on measured_apps' one mask set.
        """

        self.updates += 1
        return apply_create_update(self, app_name, update_mask, app, self.update_ledger)

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
            created=f"2026-10-03T00:00:{self.deploys:02d}Z",
            creator=self.caller,
        )
        self.active_id = new_id
        self.compute = "ACTIVE"
        # Only a deployment moves the default source (S7-2), a redeploy included.
        self.default_source = str(payload["source_code_path"])
        return self.deployments[new_id]


@dataclass
class FakeJobs:
    """One-time runs: submit (one per idempotency token), get and cancel; never a delete."""

    runs: dict[int, SimpleNamespace] = field(default_factory=dict)
    tokens: dict[str, int] = field(default_factory=dict)
    submits: int = 0
    cancels: list[int] = field(default_factory=list)
    fail_submit: bool = False
    # How a submitted run starts: "arms", "fails" or "silent" (see the module).
    start: str = "arms"
    files: FakeFiles | None = None

    def submit(self, **request: Any) -> SimpleNamespace:
        self.submits += 1
        if self.fail_submit:
            raise ConnectionError("the Jobs API is unreachable")
        token = str(request["idempotency_token"])
        if token not in self.tokens:
            run_id = 9000 + len(self.runs) + 1
            started = "INTERNAL_ERROR" if self.start == "fails" else "RUNNING"
            self.runs[run_id] = SimpleNamespace(
                run_id=run_id,
                run_name=request["run_name"],
                tasks=list(request["tasks"]),
                state=SimpleNamespace(life_cycle_state=started),
                request=request,
            )
            self.tokens[token] = run_id
            if self.start == "arms":
                self._announce(self.runs[run_id])
        return SimpleNamespace(response=SimpleNamespace(run_id=self.tokens[token]))

    def _announce(self, run: SimpleNamespace) -> None:
        """The run starts: the published dead-man writes its armed marker."""

        assert self.files is not None, "the estate wires the jobs service to its files"
        task = run.tasks[0].spark_python_task
        module = load_deadman(self.files.download(contract.api_path(task.python_file)).read(), "window_deadman_armed")
        window = module["window_from_argv"](
            list(task.parameters), workspace_factory=lambda: SimpleNamespace(workspace=self.files)
        )
        module["announce"](window)

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

    def __post_init__(self) -> None:
        if self.jobs.files is None:
            self.jobs.files = self.files

    @property
    def workspace(self) -> SimpleNamespace:
        return SimpleNamespace(
            apps=self.apps,
            secrets=self.secrets,
            workspace=self.files,
            jobs=self.jobs,
            # Every governed call is the deployer's; the owner's start is ``apps.caller``.
            current_user=SimpleNamespace(me=lambda: SimpleNamespace(user_name=DEPLOYER_CALLER)),
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
# The release's uv.lock pins the dead-man's SDK (sdk_requirement).
UV_LOCK = (REPO / "uv.lock").read_bytes()
# The rollback tests' lease (tests/unit/test_app_deployment_rollback.py LEASE_ID).
LEASE_ID = "11111111-1111-4111-8111-111111111111"


def green_release(
    stage_root: Path | None = None, *, extra: dict[str, bytes] | None = None
) -> PublishPlan:
    """This run's staged green release: blue's files, the dead-man and uv.lock, as step 1 stages it."""

    root = Path(stage_root or tempfile.mkdtemp(prefix="mip-deadman-stage-"))
    stage = root / "source"
    files = {**SOURCE_FILES, deadman.DEADMAN_RELPATH: DEADMAN_BYTES, "uv.lock": UV_LOCK, **(extra or {})}
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
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
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
            clock=clock,
            sleep=sleep,
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
