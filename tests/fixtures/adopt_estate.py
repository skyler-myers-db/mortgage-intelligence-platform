"""A synthetic existing install, its legacy bundle state and a fake bundle CLI (M2-PR6).

Nothing here is a capture or names a live install. The bundle is the 18
resources databricks.yml declares, each named ``synthetic-<key>``; the legacy
install (``LEGACY_ROOT``, owner ``legacy-owner@example.invalid``) holds 16 of
them, bound in its direct-engine state, under the CLI's development-mode names
(``[dev example] synthetic-<key>``; apps, Lakebase instances and catalogs are
named by their id and keep the bundle's name). The two jobs the legacy install
never had (``NEW_KEYS``) are the adoption's two expected creates.

``Estate`` is a WorkspaceClient double: the Workspace API is a FakeTree (the
two state files, the legacy root's folders), every other service answers the
reads by id the adoption makes and the name lookups the bundle plan gate
makes, and records every call. ``FakeBundleCli`` plans the bundle against the
deployer's state the way the direct engine does (unbound: create; bound: an
update when a name differs, a recreate when a dashboard's parent differs,
skip otherwise; a state key the bundle dropped: delete) and edits that state
on bind and unbind, after checking each edit with
``bundle_env_adopt.verify_state_edit``, as bundle_env does: its
``acknowledged`` stands for the ``MIP_ADOPT_PLAN_ACK`` bundle_env would see (by
default the plan's own digest, an operator who acknowledged it) and the lease
is held. Every authority it is handed is recorded in ``authorities``.

A dashboard's folder is spelled the way each API answered spike S4 (CLI
v1.10.0): the profile and the plan's ``new_state`` say
``/Workspace/Users/...`` (``configured``), ``lakeview.get`` answers
``parent_path`` and ``path`` as ``/Users/...`` (``live_folder``), and the
direct engine reports the remote folder as ``/Workspace/Users/...`` and plans no
``parent_path`` change between the two spellings of one folder
(``plan_folder``). tests/unit/test_adopt_dashboard_parent_path.py pins all
three, so the fake cannot drift back to answering the configured form.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from databricks.sdk.errors import NotFound
from databricks.sdk.service.workspace import ObjectType

from tests.fixtures import bundle_plan_fixtures as fx
from tests.fixtures.fake_workspace import FakeTree
from tools.databricks.adopt_legacy_map import AdoptInputs
from tools.databricks.app_release_contract import api_path
from tools.databricks.bundle_env_adopt import verify_state_edit
from tools.databricks.bundle_plan_shape import BundlePlan, parse_plan
from tools.databricks.install_state_adopt import Authority

BUNDLE = "/Workspace/Users/{owner}/.bundle/mortgage-intelligence-platform/{leaf}"
LEGACY_ROOT = BUNDLE.format(owner="legacy-owner@example.invalid", leaf="dev")
DEPLOYER_ROOT = BUNDLE.format(owner="00000000-0000-4000-8000-00000000d0e1", leaf="client-one")
OTHER_ROOT = BUNDLE.format(owner="someone-else@example.invalid", leaf="other")
DASHBOARD_PARENT = f"{LEGACY_ROOT}/resources"
DEV = "[dev example] "
NEW_KEYS = ("jobs.mip_growth_agent_monitor_scheduler", "jobs.mip_scheduled_refresh")
EXACT_KINDS = frozenset({"apps", "database_instances", "database_catalogs"})
# The two folders the Workspace API also spells without ``/Workspace``.
_ALIASED = ("/Workspace/Users/", "/Workspace/Shared/")


def live_folder(path: str) -> str:
    """A folder as ``lakeview.get`` answers it (spike S4): ``/Users/...``, never ``/Workspace/Users/...``."""

    return path.removeprefix("/Workspace") if path.startswith(_ALIASED) else path


def plan_folder(path: str) -> str:
    """A folder as the direct engine reports it in a plan's remote state (spike S4): under ``/Workspace``."""

    return f"/Workspace{path}" if f"/Workspace{path}".startswith(_ALIASED) else path


def inputs(**overrides: Any) -> AdoptInputs:
    values = {
        "client_key": "client-one",
        "deployer_root": DEPLOYER_ROOT,
        "legacy_root": LEGACY_ROOT,
        "dashboard_parent_path": DASHBOARD_PARENT,
    }
    values.update(overrides)
    return AdoptInputs(**values)


def declared() -> list[str]:
    return [f"{kind}.{key}" for kind, key in fx.declared_resources()]


def configured(resource: str) -> dict[str, str]:
    """The names the bundle resolves for one resource."""

    kind, key = resource.split(".", 1)
    if kind == "dashboards":
        return {"parent_path": DASHBOARD_PARENT, "display_name": f"synthetic-{key}"}
    if kind == "experiments":
        return {"name": f"/Shared/synthetic-{key}"}
    return {"name": f"synthetic-{key}"}


@dataclass
class LiveObject:
    kind: str
    live_id: str
    names: dict[str, str]
    deployment: str = ""
    gone: bool = False


def _legacy_names(resource: str) -> dict[str, str]:
    kind = resource.split(".", 1)[0]
    names = configured(resource)
    if kind in EXACT_KINDS:
        return names
    if kind == "dashboards":
        return {"parent_path": live_folder(names["parent_path"]), "display_name": DEV + names["display_name"]}
    if kind == "experiments":
        head, _, base = names["name"].rpartition("/")
        return {"name": f"{head}/{DEV}{base}"}
    return {"name": DEV + names["name"]}


def _legacy_id(index: int, resource: str) -> str:
    kind = resource.split(".", 1)[0]
    if kind in EXACT_KINDS:
        return configured(resource)["name"]
    if kind in {"jobs", "experiments"}:
        return str(1000 + index)
    return f"{kind[:4]}{index:04d}"


class Estate:
    """The workspace double; see the module docstring."""

    def __init__(self, *, legacy_keys: tuple[str, ...] | None = None) -> None:
        self.tree = FakeTree()
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.objects: dict[tuple[str, str], LiveObject] = {}
        self.extra_names: dict[str, set[str]] = {}
        # The bundle files root bound jobs and the pipeline run from: the
        # deployer's after a release, the legacy root's before one.
        self.files_root = f"{DEPLOYER_ROOT}/files"
        keys = legacy_keys if legacy_keys is not None else tuple(k for k in declared() if k not in NEW_KEYS)
        legacy_state = {}
        for index, resource in enumerate(sorted(keys), start=1):
            kind = resource.split(".", 1)[0]
            live_id = _legacy_id(index, resource)
            deployment = f"{LEGACY_ROOT}/state/metadata.json" if kind in {"jobs", "pipelines"} else ""
            self.objects[(kind, live_id)] = LiveObject(kind, live_id, _legacy_names(resource), deployment)
            legacy_state[f"resources.{resource}"] = {"__id__": live_id, "state": {}}
        self.legacy_ids = {key.removeprefix("resources."): value["__id__"] for key, value in legacy_state.items()}
        self.write_state(LEGACY_ROOT, {"state_version": 2, "lineage": "legacy-lineage", "serial": 855, "state": legacy_state})
        self.tree.add_dir(api_path(f"{DEPLOYER_ROOT}/files"))
        self.workspace = self.tree

    # -- state files -----------------------------------------------------------
    def write_state(self, root: str, document: dict[str, Any]) -> None:
        self.tree.add_file(api_path(f"{root}/state/resources.json"), json.dumps(document).encode())

    def state(self, root: str = DEPLOYER_ROOT) -> dict[str, Any] | None:
        path = api_path(f"{root}/state/resources.json")
        if not self.tree.exists(path):
            return None
        return json.loads(self.tree.nodes[path].data)

    def bound(self) -> dict[str, str]:
        document = self.state() or {"state": {}}
        return {key.removeprefix("resources."): entry["__id__"] for key, entry in document["state"].items()}

    def object_of(self, resource: str) -> LiveObject:
        return self.objects[(resource.split(".", 1)[0], self.legacy_ids[resource])]

    # -- the SDK surface ---------------------------------------------------------
    def __getattr__(self, service: str) -> Any:
        if service.startswith("__"):
            raise AttributeError(service)
        estate = self

        class _Service:
            def __getattr__(self, method: str) -> Callable[..., Any]:
                def call(*args: Any, **kwargs: Any) -> Any:
                    estate.calls.append((service, method, kwargs))
                    return estate._answer(service, method, args, kwargs)

                return call

        return _Service()

    def _live(self, kind: str, live_id: str) -> LiveObject:
        found = self.objects.get((kind, str(live_id)))
        if found is None or found.gone:
            raise NotFound(f"{kind} not found")
        return found

    def _named(self, kind: str) -> list[LiveObject]:
        return [obj for obj in self.objects.values() if obj.kind == kind and not obj.gone]

    def _answer(self, service: str, method: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        if (service, method) == ("current_user", "me"):
            return SimpleNamespace(groups=[SimpleNamespace(display="admins")])
        if (service, method) == ("apps", "get"):
            obj = self._live("apps", kwargs["name"])
            return SimpleNamespace(name=obj.names["name"], default_source_code_path=f"{LEGACY_ROOT}/app-src/0123456789ab-fedcba987654",
                                   active_deployment=None, pending_deployment=None)
        if (service, method) == ("database", "get_database_instance"):
            return SimpleNamespace(name=self._live("database_instances", kwargs["name"]).names["name"])
        if (service, method) == ("database", "get_database_catalog"):
            return SimpleNamespace(name=self._live("database_catalogs", kwargs["name"]).names["name"])
        if (service, method) == ("catalogs", "get"):
            if kwargs["name"] in {o.names["name"] for o in self._named("database_catalogs")}:
                return SimpleNamespace(name=kwargs["name"])
            raise NotFound("catalog")
        if (service, method) == ("warehouses", "get"):
            return SimpleNamespace(name=self._live("sql_warehouses", kwargs["id"]).names["name"])
        if (service, method) == ("warehouses", "list"):
            return [SimpleNamespace(name=o.names["name"]) for o in self._named("sql_warehouses")]
        if (service, method) == ("jobs", "get"):
            obj = self._live("jobs", str(kwargs["job_id"]))
            deployment = SimpleNamespace(metadata_file_path=obj.deployment) if obj.deployment else None
            task = {"notebook_task": {"notebook_path": f"{self.files_root}/jobs/{obj.live_id}"}}
            settings = {"name": obj.names["name"], "tasks": [task]}
            return SimpleNamespace(
                settings=SimpleNamespace(name=obj.names["name"], deployment=deployment, as_dict=lambda: settings)
            )
        if (service, method) == ("jobs", "list"):
            wanted = str(kwargs.get("name") or "").casefold()
            names = [o.names["name"] for o in self._named("jobs")] + sorted(self.extra_names.get("jobs", ()))
            return [SimpleNamespace(settings=SimpleNamespace(name=n)) for n in names if n.casefold() == wanted]
        if (service, method) == ("pipelines", "get"):
            obj = self._live("pipelines", kwargs["pipeline_id"])
            deployment = SimpleNamespace(metadata_file_path=obj.deployment) if obj.deployment else None
            spec = {"name": obj.names["name"], "libraries": [{"file": {"path": f"{self.files_root}/pipelines/x.py"}}]}
            return SimpleNamespace(
                name=obj.names["name"],
                spec=SimpleNamespace(name=obj.names["name"], deployment=deployment, as_dict=lambda: spec),
            )
        if (service, method) == ("pipelines", "list_pipelines"):
            return [SimpleNamespace(name=o.names["name"]) for o in self._named("pipelines")]
        if (service, method) == ("lakeview", "get"):
            obj = self._live("dashboards", kwargs["dashboard_id"])
            return SimpleNamespace(
                parent_path=obj.names["parent_path"],
                display_name=obj.names["display_name"],
                path=f"{obj.names['parent_path']}/{obj.names['display_name']}.lvdash.json",
                lifecycle_state="ACTIVE",
            )
        if (service, method) == ("experiments", "get_experiment"):
            obj = self._live("experiments", kwargs["experiment_id"])
            return SimpleNamespace(experiment=SimpleNamespace(name=obj.names["name"], lifecycle_stage="active"))
        if (service, method) == ("experiments", "get_by_name"):
            names = {o.names["name"] for o in self._named("experiments")} | self.extra_names.get("experiments", set())
            if kwargs["experiment_name"] in names:
                return SimpleNamespace(name=kwargs["experiment_name"])
            raise NotFound("experiment")
        raise AssertionError(f"unexpected call {service}.{method}")

    def reads(self) -> list[tuple[str, str]]:
        return [(service, method) for service, method, _ in self.calls]


@dataclass
class FakeBundleCli:
    """Plans and edits the deployer's state over ``estate``; see the module docstring."""

    estate: Estate
    calls: list[tuple[str, ...]] = field(default_factory=list)
    fail_bind: dict[str, Exception] = field(default_factory=dict)
    fail_after_bind: dict[str, Exception] = field(default_factory=dict)
    fail_unbind: dict[str, Exception] = field(default_factory=dict)
    recreate_dashboards: bool = False
    instance_drift: bool = False
    acknowledged: str | None = None
    authorities: list[Authority] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def _key(self, key: str) -> str:
        matches = [resource for resource in declared() if resource.split(".", 1)[1] == key]
        assert len(matches) == 1, key
        return matches[0]

    def plan(self) -> BundlePlan:
        self.calls.append(("plan",))
        bound = self.estate.bound()
        entries: dict[str, dict[str, Any]] = {}
        for resource in declared():
            kind = resource.split(".", 1)[0]
            config = configured(resource)
            if resource not in bound:
                entries[f"resources.{resource}"] = fx.entry("create", value=config)
                continue
            live = self.estate.objects.get((kind, bound[resource]))
            remote = dict(live.names) if live is not None else {}
            if kind == "dashboards" and "parent_path" in remote:
                remote["parent_path"] = plan_folder(remote["parent_path"])
            changes = {
                name: {"action": "update", "old": remote.get(name), "new": value, "remote": remote.get(name)}
                for name, value in config.items()
                if remote.get(name) != value
            }
            action = "update" if changes else "skip"
            if kind == "dashboards" and ("parent_path" in changes or self.recreate_dashboards):
                action = "recreate"
            if kind == "database_instances" and self.instance_drift:
                action = "update"
                changes["capacity"] = {"action": "update", "old": "CU_1", "new": "CU_2", "remote": "CU_1"}
            entries[f"resources.{resource}"] = (
                fx.entry("skip", remote={**remote, "id": bound[resource]})
                if action == "skip"
                else fx.entry(action, value=config, changes=changes, remote=remote)
            )
        for resource in sorted(set(bound) - set(declared())):
            entries[f"resources.{resource}"] = fx.entry("delete")
        document = self.estate.state()
        lineage = str((document or {}).get("lineage") or "")
        serial = int((document or {}).get("serial") or 0)
        return parse_plan(fx.dumps(fx.document(entries, lineage=lineage, serial=serial)))

    def _edit(self, resource: str, live_id: str | None) -> None:
        document = copy.deepcopy(self.estate.state()) or {"state_version": 2, "lineage": "deployer-lineage", "serial": 0, "state": {}}
        if live_id is None:
            document["state"].pop(f"resources.{resource}", None)
        else:
            document["state"][f"resources.{resource}"] = {"__id__": live_id, "state": {}}
        document["serial"] += 1
        self.estate.write_state(DEPLOYER_ROOT, document)

    def _authorize(self, action: str, key: str, live_id: str | None, authority: Authority) -> None:
        self.authorities.append(authority)
        acknowledged = authority.sha256 if self.acknowledged is None else self.acknowledged
        refusal = verify_state_edit(
            authority, action, key, live_id, acknowledged=acknowledged, inputs=inputs(), lease=lambda: None
        )
        assert refusal is None, refusal

    def bind(self, key: str, live_id: str, authority: Authority) -> None:
        self.calls.append(("bind", key, live_id))
        self._authorize("bind", key, live_id, authority)
        if key in self.fail_bind:
            raise self.fail_bind[key]
        self._edit(self._key(key), live_id)
        if key in self.fail_after_bind:
            raise self.fail_after_bind[key]

    def unbind(self, key: str, authority: Authority) -> None:
        self.calls.append(("unbind", key))
        self._authorize("unbind", key, None, authority)
        if key in self.fail_unbind:
            raise self.fail_unbind[key]
        self._edit(self._key(key), None)

    def edits(self) -> list[tuple[str, ...]]:
        return [call for call in self.calls if call[0] != "plan"]


class LanguageTree(FakeTree):
    """A FakeTree whose notebooks carry a language and import from SOURCE with one."""

    def _status(self, path: str) -> SimpleNamespace:
        status = super()._status(path)
        if status.object_type is ObjectType.NOTEBOOK:
            status.language = "PYTHON"
        return status

    def upload(self, path: str, content: Any, *, format: Any = None, language: Any = None, overwrite: bool | None = False) -> None:  # noqa: A002
        super().upload(path, content, format=format, language=language, overwrite=overwrite)
        if language is not None:
            self.nodes[path] = type(self.nodes[path])(ObjectType.NOTEBOOK, self.nodes[path].data)


def adopted_estate(**kwargs: Any) -> Estate:
    """The estate after the adoption: the deployer's state binds every legacy key."""

    estate = Estate(**kwargs)
    tree = LanguageTree()
    tree.nodes = estate.tree.nodes
    estate.tree = estate.workspace = tree
    cli = FakeBundleCli(estate)
    for resource, live_id in sorted(estate.legacy_ids.items()):
        cli._edit(resource, live_id)
    return estate
