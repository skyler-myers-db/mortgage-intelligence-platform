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
"""

from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from databricks.sdk.errors import NotFound, ResourceDoesNotExist

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
            raise RuntimeError("AlreadyExists")
        self.data[path] = content.read()

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
    get_calls: int = 0
    deployments: dict[str, SimpleNamespace] = field(default_factory=dict)
    active_id: str = BLUE_ID
    pending_id: str = ""
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
            resources=[],
            description="",
        )
        app.as_dict = lambda: {"name": APP_NAME, "description": "", "resources": []}
        return app

    def get(self, _name: str) -> SimpleNamespace:
        self.get_calls += 1
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
        return SimpleNamespace(response=SimpleNamespace(pending_deployment=pending))

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
class Estate:
    files: FakeFiles
    secrets: FakeSecrets
    apps: FakeApps

    @property
    def workspace(self) -> SimpleNamespace:
        return SimpleNamespace(apps=self.apps, secrets=self.secrets, workspace=self.files)


def make_estate(env_names: list[str], behaviour: StartBehaviour | None = None) -> Estate:
    files = FakeFiles()
    files.put_tree(RELEASE, SOURCE_FILES)
    files.put_tree(BLUE_ARTIFACT, SOURCE_FILES)
    apps = FakeApps(files=files, env_names=env_names, behaviour=behaviour or StartBehaviour())
    return Estate(files=files, secrets=FakeSecrets(), apps=apps)


def stored_record(estate: Estate, *, scope: str = "mip") -> dict[str, Any]:
    from tools.databricks.app_rollback_record_contract import _record_key

    return json.loads(estate.secrets.values[(scope, _record_key(APP_NAME))])
