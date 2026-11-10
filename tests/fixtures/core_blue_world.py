"""An adopted core blue the core release can restore, offline (M2-PR7b).

``CoreWorld`` is adopt-live's World (tests/fixtures/adopt_live_world.py)
after its adoption, with an Apps API that can also deploy, start and update,
in the shape spike S7 measured (tests/fixtures/measured_apps.py): a deploy
records the payload and becomes the active deployment from the payload's own
source and env-name set, its ``/src/`` snapshot a byte copy of that source at
deploy time (as adopt-live proves of the live App). A start answers with no
deployment and snapshots the default source (the last active deployment's
folder) with the newest deployment's env-name set into a new deployment
that shows only ``HIDDEN_READS`` reads later, pending for one GET, then
active IN_PROGRESS until its first poll settles it (``start_final``; a
FAILED one stays active). A STOPPED App's GET has no active deployment, and
an update replaces the bindings only. Every call is recorded in
``apps.mutations``. ``serves`` and ``converge_treatment`` are the injected checks the
core tools take, recording what they were asked. Every value is synthetic.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from databricks.sdk.service.workspace import ObjectType

from tests.fixtures import rollback_records as rr
from tests.fixtures.adopt_live_world import DIST, World, make_repo
from tests.fixtures.measured_apps import HIDDEN_READS, StartVisibility
from tools.databricks import blue_restore_proof as proof

GREEN_SHA = "c" * 40
GREEN_RELEASE = f"{GREEN_SHA[:12]}-{'d' * 12}"


class CoreApps:
    """The Apps API of one App that can deploy, start and update."""

    def __init__(self, world: CoreWorld, reads: Any) -> None:
        self.world = world
        self.reads = reads
        self.mutations: list[str] = []
        self.deployed: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.reads, name)

    def deploy_and_wait(self, name: str, deployment: Any, timeout: Any = None) -> SimpleNamespace:
        assert name == rr.APP_NAME
        self.mutations.append("deploy_and_wait")
        payload = deployment.as_dict()
        self.deployed.append(payload)
        new = f"deployment-restored-{len(self.world.deployments)}"
        names = sorted(str(item["name"]) for item in payload["env_vars"])
        self.world.add_deployment(new, source=str(payload["source_code_path"]), env_names=names)
        self.world.snapshot_source(new, str(payload["source_code_path"]))
        return SimpleNamespace(deployment_id=new, status=SimpleNamespace(state="SUCCEEDED"))

    def start(self, name: str) -> SimpleNamespace:
        """The measured start: an empty response; the new deployment shows reads later."""

        self.mutations.append("start")
        world = self.world
        new = f"deployment-started-{len(world.deployments)}"
        newest = world.deployment(world.deployments[-1])
        source = world.deployment(world.active).source_code_path  # the default source
        world.pending_start = (new, source, [str(item.name) for item in newest.env_vars])
        world.snapshot_source(new, source)
        world.in_flight = StartVisibility(new, hidden=world.start_hidden_reads)
        world.compute = "STARTING"
        return SimpleNamespace(
            response=SimpleNamespace(
                compute_status=SimpleNamespace(state="STARTING"),
                active_deployment=None,
                pending_deployment=None,
            )
        )

    def get(self, name: str) -> SimpleNamespace:
        self.world.tick(is_get=True)
        app = self.reads.get(name)
        if self.world.in_flight is not None and not self.world.in_flight.never:
            app.active_deployment = None  # measured: nothing active until the start's id is
        return app

    def list_deployments(self, name: str, **kwargs: Any) -> list[SimpleNamespace]:
        self.world.tick(is_get=False)
        return self.reads.list_deployments(name, **kwargs)

    def get_deployment(self, name: str, deployment_id: str) -> SimpleNamespace:
        self.world.settle(deployment_id)
        return self.reads.get_deployment(name, deployment_id)

    def update(self, name: str, app: Any) -> None:
        """Bindings only: a default_source_code_path in the App is ignored (S7-2)."""

        self.mutations.append("update")
        self.world.resources = [item.as_dict() for item in app.resources]


@dataclass
class CoreWorld(World):
    sources: dict[str, str] = field(default_factory=dict)
    env_sets: dict[str, list[str]] = field(default_factory=dict)
    states: dict[str, str] = field(default_factory=dict)
    # A start in flight (measured_apps): what it creates, and how the API shows it.
    pending_start: tuple[str, str, list[str]] | None = None
    in_flight: StartVisibility | None = None
    start_hidden_reads: int = HIDDEN_READS
    start_final: str = "SUCCEEDED"
    treatments: list[str] = field(default_factory=list)
    health_calls: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        super().__post_init__()
        self.apps = CoreApps(self, self.apps)  # type: ignore[assignment]

    def deployment(self, deployment_id: str) -> SimpleNamespace:
        base = super().deployment(deployment_id)
        base.status.state = self.states.get(deployment_id, base.status.state)
        if deployment_id in self.sources:
            base.source_code_path = self.sources[deployment_id]
            base.env_vars = [
                SimpleNamespace(name=name, value=None, value_from=None)
                for name in self.env_sets[deployment_id]
            ]
        return base

    def add_deployment(
        self, deployment_id: str, *, source: str, env_names: list[str], active: bool = True
    ) -> None:
        self.deployments.append(deployment_id)
        self.sources[deployment_id] = source
        self.env_sets[deployment_id] = env_names
        if active:
            self.active = deployment_id

    def tick(self, *, is_get: bool) -> None:
        """One read of a start in flight: hidden, then listed or pending, then active (S7)."""

        start = self.in_flight
        if start is None:
            return
        phase = start.read(is_get=is_get)
        if phase == "hidden":
            return
        if phase == "compute_only":
            self.compute = "ACTIVE"
            return
        if self.pending_start is not None:
            new, source, names = self.pending_start
            self.pending_start = None
            self.add_deployment(new, source=source, env_names=names, active=False)
            self.states[new] = "IN_PROGRESS"
            self.compute = "ACTIVE"
        if phase == "pending":
            self.pending = SimpleNamespace(deployment_id=start.deployment_id)
        elif phase == "active":
            self.pending, self.active, self.in_flight = None, start.deployment_id, None

    def settle(self, deployment_id: str) -> None:
        """A poll of the start's deployment: it is active by now, and terminal at this poll."""

        if self.in_flight is not None and self.in_flight.deployment_id == deployment_id:
            if self.pending_start is not None:
                return  # never shown: not readable yet
            self.pending, self.active, self.in_flight = None, deployment_id, None
        if self.states.get(deployment_id) == "IN_PROGRESS":
            self.states[deployment_id] = self.start_final

    def snapshot_source(self, deployment_id: str, source: str) -> None:
        """The platform's ``/src/`` snapshot of ``source`` for ``deployment_id``: every file, copied now."""

        root = source.removeprefix("/Workspace")
        target = self.snapshot(deployment_id).removeprefix("/Workspace")
        for path in self.tree.below(root):
            node = self.tree.nodes[path]
            if node.kind is ObjectType.FILE:
                self.tree.add_file(target + path[len(root) :], node.data)

    # -- the checks the core tools inject ------------------------------------------
    def serves(self, record: dict[str, Any], deployment_id: str) -> None:
        from tools.databricks.core_blue import CoreBlueRefused

        self.health_calls.append(deployment_id)
        if self.active != deployment_id or record["git_sha"] != self.main:
            raise CoreBlueRefused("health_mismatch", "the fake App serves another commit")

    def converge_treatment(self, mode: str) -> None:
        self.treatments.append(mode)

    def kwargs(self, **overrides: Any) -> dict[str, Any]:
        values: dict[str, Any] = {
            "app_name": rr.APP_NAME,
            "scope": rr.SCOPE,
            "lakebase_instance": rr.LAKEBASE,
            "agentic_tier": "off",
            "health": self.serves,
            "lease": self.lease,
            "treatment": self.converge_treatment,
            "run_git_sha": GREEN_SHA,  # the restoring run's commit
        }
        values.update(overrides)
        return values

    def prove_restore_payload(self) -> dict[str, object]:
        """The release's pre-activation proof: blue's restore payload recorded."""

        record = self.record()
        assert record is not None
        payload = rr.payload(record["restore_source_path"])
        proof.prepare_activation(
            self,
            app_name=rr.APP_NAME,
            scope=rr.SCOPE,
            lakebase_instance=rr.LAKEBASE,
            lease=self.lease,
            agentic_tier="off",
            green_git_sha=GREEN_SHA,
            green_release_id=GREEN_RELEASE,
            restore_payload=payload,
        )
        return payload

    def activate_green(self) -> None:
        """A green activation: a newer deployment from another folder is active."""

        self.redeploy("deployment-green")


def _reference_build(tmp_path: Path) -> Any:
    """adopt-live's own reference build of the health sha: the deployed dist, byte for byte."""

    def build(_commit: str) -> Path:
        dist = tmp_path / "reference-dist"
        for relpath, data in DIST.items():
            target = dist / relpath.removeprefix("frontend/dist/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return dist

    return build


def adopted_core_world(tmp_path: Path, *, verified: bool = True, **fields: Any) -> CoreWorld:
    """Client #1's case: an adopted core blue, the App serving it, the record signed.

    ``verified`` adopts with MIP_ADOPT_BUILD_REFERENCE_DIST=1's reference build,
    so blue's frontend is verified; otherwise it is unverified.
    """

    repo, main, side = make_repo(tmp_path)
    world = CoreWorld(repo=repo, main=main, side=side, **fields)
    world.apply(readers=world.readers(build_reference=_reference_build(tmp_path)) if verified else None)
    world.apps.mutations.clear()
    world.resources = copy.deepcopy(rr.APP_RESOURCES)
    return world
