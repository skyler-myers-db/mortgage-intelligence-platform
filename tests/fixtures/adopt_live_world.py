"""An offline install for adopt-live: a git repo, a live App on D0 and its source folder (M2-PR7).

``World`` holds a synthetic repo whose ``main`` carries the App runtime
closure, a fake workspace (tests/fixtures/fake_workspace.py) with D0's legacy
source folder and its byte-equal ``/src/`` snapshot, an App serving D0 with
core-only bindings, and a secrets store for the signed record. Every App
mutation the SDK offers is a spy that records the attempt and raises, so a
test can prove adopt-live made none. Every value is synthetic.
"""

from __future__ import annotations

import copy
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tests.fixtures import rollback_records as rr
from tests.fixtures.fake_workspace import FakeTree
from tools.databricks import adopt_live
from tools.databricks.adopt_live_facts import Readers

APP_YAML = b"command: ['python', '-m', 'backend.runtime']\n"
REPO_FILES = {
    "app.yaml": APP_YAML,
    "requirements.txt": b"# deps-digest: synthetic\n-c uv.lock\n",
    "backend/__init__.py": b"",
    "backend/main.py": b"print('mip')\n",
}
DIST = {
    "frontend/dist/index.html": b"<!doctype html><title>MIP</title>\n",
    "frontend/dist/assets/app-abcd1234.js": b"console.log('mip')\n",
}
MUTATIONS = (
    "stop",
    "stop_and_wait",
    "start",
    "start_and_wait",
    "deploy",
    "deploy_and_wait",
    "update",
    "create_update",
    "create_update_and_wait",
    "set_permissions",
    "update_permissions",
    "delete",
    "create",
    "create_and_wait",
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
            "HOME": str(repo),
            "PATH": "/usr/bin:/bin:/opt/homebrew/bin",
        },
    ).stdout.strip()


def make_repo(root: Path) -> tuple[Path, str, str]:
    """A repo with the closure on ``main`` and one commit off it; returns (repo, main sha, side sha)."""

    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    for relpath, data in REPO_FILES.items():
        (repo / relpath).parent.mkdir(parents=True, exist_ok=True)
        (repo / relpath).write_bytes(data)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "closure")
    main = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "side")
    (repo / "backend" / "main.py").write_bytes(b"print('side')\n")
    _git(repo, "commit", "-q", "-am", "side")
    side = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    return repo, main, side


class SpyApps:
    """The Apps API of one App; every mutation is recorded and refused."""

    def __init__(self, world: World) -> None:
        self.world = world
        self.mutations: list[str] = []
        self.permissions_error: Exception | None = None

    def __getattr__(self, name: str) -> Any:
        if name in MUTATIONS:

            def refuse(*_args: Any, **_kwargs: Any) -> None:
                self.mutations.append(name)
                raise AssertionError(f"adopt-live called apps.{name}")

            return refuse
        raise AttributeError(name)

    def get(self, name: str) -> SimpleNamespace:
        world = self.world
        assert name == rr.APP_NAME
        return SimpleNamespace(
            name=name,
            url="https://mip-app.example.invalid",
            compute_status=SimpleNamespace(state=world.compute),
            active_deployment=world.deployment(world.active),
            pending_deployment=world.pending,
            default_source_code_path=world.deployment(world.active).source_code_path,
            service_principal_client_id="app-client",
            service_principal_id="app-scim-id",
            resources=copy.deepcopy(world.resources),
        )

    def list_deployments(self, _name: str, **_: Any) -> list[SimpleNamespace]:
        return [self.world.deployment(item) for item in self.world.deployments]

    def get_deployment(self, _name: str, deployment_id: str) -> SimpleNamespace:
        return self.world.deployment(deployment_id)

    def get_permissions(self, _name: str) -> SimpleNamespace:
        if self.permissions_error is not None:
            raise self.permissions_error
        return SimpleNamespace(
            access_control_list=[
                SimpleNamespace(
                    user_name=None,
                    group_name="admins",
                    service_principal_name=None,
                    all_permissions=[
                        SimpleNamespace(permission_level="CAN_MANAGE", inherited=True)
                    ],
                ),
                SimpleNamespace(
                    user_name=None,
                    group_name=None,
                    service_principal_name="normal-client",
                    all_permissions=[SimpleNamespace(permission_level="CAN_USE", inherited=False)],
                ),
            ]
        )


@dataclass
class World:
    repo: Path
    main: str
    side: str
    tree: FakeTree = field(default_factory=FakeTree)
    secrets: rr.Secrets = field(default_factory=rr.Secrets)
    compute: str = "ACTIVE"
    active: str = "deployment-blue"
    pending: object | None = None
    deployments: list[str] = field(default_factory=lambda: ["deployment-old", "deployment-blue"])
    env_names: list[str] = field(default_factory=rr.core_env_names)
    resources: list[dict[str, object]] = field(
        default_factory=lambda: copy.deepcopy(rr.APP_RESOURCES)
    )
    health: str = ""
    treatment: dict[str, list[str] | None] = field(
        default_factory=lambda: {"runtime": ["drift"], "quiesce": []}
    )
    lease_calls: int = 0

    def __post_init__(self) -> None:
        self.apps = SpyApps(self)
        self.workspace = self.tree  # the Workspace API surface
        self.health = self.health or self.main[:12]
        for deployment_id in self.deployments:
            self.add_files(deployment_id)

    # -- the live App ---------------------------------------------------------------
    def folder(self, deployment_id: str) -> str:
        return rr.LEGACY_FOLDER.replace("0123abcd", deployment_id[-8:].replace("-", "x"))

    def snapshot(self, deployment_id: str) -> str:
        return f"/Workspace/Users/app-sp/src/{deployment_id}"

    def add_files(self, deployment_id: str, files: dict[str, bytes] | None = None) -> None:
        for relpath, data in (files or {**REPO_FILES, **DIST}).items():
            for root in (self.folder(deployment_id), self.snapshot(deployment_id)):
                self.tree.add_file(f"{root.removeprefix('/Workspace')}/{relpath}", data)

    def deployment(self, deployment_id: str) -> SimpleNamespace:
        order = self.deployments.index(deployment_id)
        return SimpleNamespace(
            deployment_id=deployment_id,
            source_code_path=self.folder(deployment_id),
            deployment_artifacts=SimpleNamespace(source_code_path=self.snapshot(deployment_id)),
            env_vars=[
                SimpleNamespace(name=name, value=None, value_from=None) for name in self.env_names
            ],
            status=SimpleNamespace(state="SUCCEEDED"),
            create_time=f"2026-10-2{order}T00:00:00Z",
            update_time=f"2026-10-2{order}T00:01:00Z",
        )

    def redeploy(self, deployment_id: str) -> None:
        """A newer deployment (a peer's emergency deploy) becomes active."""

        self.deployments.append(deployment_id)
        self.add_files(deployment_id)
        self.active = deployment_id

    # -- adopt-live's injected reads ------------------------------------------------
    def readers(self, **overrides: Any) -> Readers:
        values: dict[str, Any] = {
            "health_sha": lambda: self.health,
            "treatment": lambda _principal, mode: self.treatment[mode],
            "repo": self.repo,
            "main_ref": "refs/heads/main",
            "source_sha": self.main,
        }
        values.update(overrides)
        return Readers(**values)

    def target(self, *, tier: str = "off", supersede: str = "") -> adopt_live.Target:
        return adopt_live.Target(
            app_name=rr.APP_NAME,
            scope=rr.SCOPE,
            lakebase_instance=rr.LAKEBASE,
            principal=rr.DEPLOYER,
            root_leaf=rr.CLIENT_KEY,
            agentic_tier=tier,
            supersede=supersede,
        )

    def lease(self) -> None:
        self.lease_calls += 1

    def record(self) -> dict[str, Any] | None:
        from tools.databricks.app_rollback_record_contract import load_signed_record

        return load_signed_record(
            self, app_name=rr.APP_NAME, scope=rr.SCOPE, expected_lakebase_instance=rr.LAKEBASE
        )

    def apply(
        self,
        mode: str = "adopt",
        *,
        ack: str | None = None,
        readers: Readers | None = None,
        supersede: str = "",
        reviewed: list[dict[str, object]] | None = None,
        tier: str = "off",
    ) -> Any:
        from tools.databricks.operation_ack import ack_digest

        target = self.target(supersede=supersede, tier=tier)
        used = readers or self.readers()
        if ack is None:
            plan, _facts, _record = adopt_live.compute_plan(self, mode, target, used)
            ack = ack_digest(adopt_live.PURPOSE[mode], plan)
        return adopt_live.apply(
            self,
            mode,
            target,
            used,
            ack=ack,
            lease=self.lease,
            reviewed_bindings=lambda: copy.deepcopy(
                rr.APP_RESOURCES if reviewed is None else reviewed
            ),
            ensure_scope=lambda: None,
        )
