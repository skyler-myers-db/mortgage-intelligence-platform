"""The legacy map an adoption binds, computed read-only and digested (M2-PR6).

An existing install that another identity deployed keeps its direct-engine
bundle state in that identity's root: ``MIP_LEGACY_BUNDLE_ROOT`` (the
client profile), whose ``state/resources.json`` maps each
``resources.<kind>.<key>`` to a live id (tools/databricks/bundle_state.py).
``compute`` intersects those keys with the bundle's own (the pre-bind plan of
``bundle_env plan -t client``) and checks each object against the live estate,
with one GET by id per object:

* **missing**: the id no longer resolves (a trashed dashboard and a deleted
  experiment count as missing);
* **renamed**: the live name is neither the name the bundle gives the key nor
  that name under the CLI's development-mode prefix (``[dev <user>] ``, on the
  basename of an experiment path). Apps, Lakebase instances and catalogs are
  named by their id, so their names must be equal: a different one would be
  an identity change, which the bundle could only apply by recreating;
* **foreign_bound**: a job or pipeline whose bundle deployment metadata names
  a root other than the legacy root or this deployer's own; or an id this
  deployer's state already binds under another key, or that another legacy
  key also claims (``duplicate_id``); or a key this deployer's state binds to
  another object (``bound_elsewhere``);
* **dashboard_parent_moved** (attack O9): a legacy dashboard whose live folder
  is not ``MIP_DASHBOARD_PARENT_PATH``. Spike S4 has not shown whether a
  ``parent_path`` change updates in place, so the worst case is assumed: it
  recreates. An adopted install's dashboards therefore stay in their legacy
  folder, permanently until S4 (``DASHBOARD_POLICY``; the profile requires the
  folder inside the legacy root, tools/clients/rules.py).

The canonical document of what would change is digested with
``bundle_plan_shape.sha256_hex``. That digest is the exact acknowledgement a
mutating run must be given (the owner attestation is deferred); the apply
recomputes the document immediately before the first state edit and refuses
on any difference. ``unadopt`` is the exact inverse: it unbinds each legacy
key this deployer's state binds to the legacy id, and nothing else, in the
reverse of adopt's bind order, so the App's binding is the last one removed.

Refusal reasons name keys, fields and error classes, never a value. Every id
read from either state is registered as a runner mask the moment it is read.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from tools.clients.redact import emit_mask
from tools.databricks.bundle_plan_lookups import KIND_LOOKUPS
from tools.databricks.bundle_plan_shape import BundlePlan, sha256_hex

PLAN_SCHEMA = "mip.install-state-adopt.plan.v1"
PUBLIC_SCHEMA = "mip.install-state-adopt.public.v1"
PHASES = ("adopt", "unadopt")
DASHBOARD_POLICY = "pinned_permanent"
STATE_METADATA = "/state/metadata.json"
# Named by their id: the live name must be the bundle's own.
EXACT_NAME_KINDS = frozenset({"apps", "database_instances", "database_catalogs"})
_DEV_PREFIX = re.compile(r"\[dev [^\]\n]+\] ")
_STATE_KEY = re.compile(
    r"resources\.(?P<kind>[a-z][a-z0-9_]*)\.(?P<name>[A-Za-z0-9][A-Za-z0-9_-]*)"
    r"(?:\.(?P<child>[a-z]+))?\Z"
)
_ABSENT = (NotFound, ResourceDoesNotExist)

REMEDIES: Mapping[str, str] = MappingProxyType(
    {
        "not_an_adopted_install": (
            "the client profile names no MIP_LEGACY_BUNDLE_ROOT, so there is no legacy state to "
            "adopt; set it to the existing install's bundle root"
        ),
        "legacy_state_missing": (
            "MIP_LEGACY_BUNDLE_ROOT holds no bundle state; name the root the existing install "
            "was deployed from"
        ),
        "malformed_state": (
            "a bundle state entry is not resources.<kind>.<key> with a live id; reconcile the "
            "state through a reviewed change, then re-run"
        ),
        "ambiguous_key": (
            "two bundle resources share one key, which bundle deployment bind cannot tell apart; "
            "rename one in databricks.yml"
        ),
        "legacy_key_unknown": (
            "the legacy state binds a key this bundle does not declare, so adopting would leave "
            "its object unmanaged; reconcile it through a reviewed change, then re-run"
        ),
        "deployer_state_orphan": (
            "this deployer's state binds a key the bundle no longer declares, which a plan would "
            "delete; reconcile it through a reviewed change, then re-run"
        ),
        "missing": "the legacy object no longer exists; reconcile the legacy state, then re-run",
        "renamed": (
            "the live object's name is not the one the bundle gives this key (or its "
            "development-mode form): it was renamed, or the legacy state names another object"
        ),
        "dashboard_parent_moved": (
            "the legacy dashboard is not in MIP_DASHBOARD_PARENT_PATH. Spike S4 is owed, so a "
            "parent_path change is assumed to recreate the dashboard: pin "
            "MIP_DASHBOARD_PARENT_PATH to the dashboard's live folder"
        ),
        "foreign_bound": (
            "the object's bundle deployment names another root than the legacy root or this "
            "deployer's: another bundle manages it, so it is not this install's to adopt"
        ),
        "bound_elsewhere": (
            "this deployer's state already binds this key to another object, or this object "
            "under another key; reconcile the state through a reviewed change, then re-run"
        ),
        "duplicate_id": "two legacy keys bind one object; reconcile the legacy state, then re-run",
        "unresolved_name": (
            "the bundle plan has not resolved this resource's name, so it cannot be checked; "
            "fix the unresolved reference, then re-run"
        ),
        "state_mismatch": (
            "the bundle plan and this deployer's state disagree about whether this key is bound "
            "(the state changed during the run); re-run"
        ),
        "read_failed": (
            "the live object could not be read (the full report says why); a read error is "
            "never read as absent. Fix the read access, then re-run"
        ),
    }
)


@dataclass(frozen=True)
class AdoptInputs:
    """The profile's view of one adoption: whose state, from where, into where."""

    client_key: str
    deployer_root: str
    legacy_root: str | None
    dashboard_parent_path: str | None


@dataclass(frozen=True)
class AdoptRefusal:
    code: str
    resource: str
    reason: str = ""

    @property
    def remedy(self) -> str:
        return REMEDIES[self.code]


@dataclass(frozen=True)
class Binding:
    """One state edit: ``resource`` is ``<kind>.<key>``; ``key`` is what the CLI binds by."""

    resource: str
    key: str
    live_id: str
    names: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"resource": self.resource, "key": self.key, "id": self.live_id, "names": list(self.names)}


@dataclass(frozen=True)
class AdoptPlan:
    """What one adopt or unadopt would change, and its digest."""

    phase: str
    document: Mapping[str, Any]
    edits: tuple[Binding, ...]
    already: tuple[str, ...]
    expected_creates: tuple[str, ...]
    bound_after: tuple[str, ...]
    refusals: tuple[AdoptRefusal, ...] = ()
    errors: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    @property
    def sha256(self) -> str:
        return sha256_hex(self.document)

    @property
    def refused(self) -> bool:
        return bool(self.refusals)

    def public_summary(self) -> dict[str, Any]:
        """Codes, counts, the digest and symbolic keys; never a name, an id or a path."""

        return {
            "schema": PUBLIC_SCHEMA,
            "phase": self.phase,
            "verdict": "refused" if self.refused else "planned",
            "plan_sha256": self.sha256,
            "counts": {
                "bind" if self.phase == "adopt" else "unbind": len(self.edits),
                "already": len(self.already),
                "expected_creates": len(self.expected_creates),
            },
            "expected_creates": list(self.expected_creates),
            "dashboard_parent_path": DASHBOARD_POLICY,
            "refusals": [{"code": r.code, "resource": r.resource} for r in self.refusals],
        }

    def full(self) -> dict[str, Any]:
        return {
            **self.public_summary(),
            "document": dict(self.document),
            "refusals": [
                {"code": r.code, "resource": r.resource, "reason": r.reason, "remedy": r.remedy}
                for r in self.refusals
            ],
            "errors": dict(self.errors),
        }


@dataclass(frozen=True)
class LiveObject:
    names: tuple[str, ...]
    deployment_path: str = ""


class UnreadableId(ValueError):
    """The state holds an id no reviewed GET can take (the message names no value)."""


def _attr(item: Any, *path: str) -> Any:
    for name in path:
        if item is None:
            return None
        item = item.get(name) if isinstance(item, Mapping) else getattr(item, name, None)
    return item


def _text(item: Any, *path: str) -> str:
    value = _attr(item, *path)
    return str(getattr(value, "value", value) or "").strip()


def read_live(workspace: Any, kind: str, live_id: str) -> LiveObject | None:
    """One read-only GET by id; ``None`` only on a typed not-found (or a trashed object)."""

    try:
        if kind == "apps":
            return LiveObject((_text(workspace.apps.get(name=live_id), "name"),))
        if kind == "database_instances":
            return LiveObject((_text(workspace.database.get_database_instance(name=live_id), "name"),))
        if kind == "database_catalogs":
            return LiveObject((_text(workspace.database.get_database_catalog(name=live_id), "name"),))
        if kind == "sql_warehouses":
            return LiveObject((_text(workspace.warehouses.get(id=live_id), "name"),))
        if kind == "jobs":
            if not live_id.isdigit():
                raise UnreadableId("a job id is a decimal number")
            job = workspace.jobs.get(job_id=int(live_id))
            return LiveObject(
                (_text(job, "settings", "name"),),
                _text(job, "settings", "deployment", "metadata_file_path"),
            )
        if kind == "pipelines":
            pipeline = workspace.pipelines.get(pipeline_id=live_id)
            return LiveObject(
                (_text(pipeline, "name") or _text(pipeline, "spec", "name"),),
                _text(pipeline, "spec", "deployment", "metadata_file_path"),
            )
        if kind == "dashboards":
            dashboard = workspace.lakeview.get(dashboard_id=live_id)
            if _text(dashboard, "lifecycle_state") == "TRASHED":
                return None
            return LiveObject((_text(dashboard, "parent_path"), _text(dashboard, "display_name")))
        if kind == "experiments":
            experiment = _attr(workspace.experiments.get_experiment(experiment_id=live_id), "experiment")
            if _text(experiment, "lifecycle_stage").casefold() == "deleted":
                return None
            return LiveObject((_text(experiment, "name"),))
    except _ABSENT:
        return None
    raise UnreadableId(f"no reviewed read by id for kind {kind}")


def same_name(kind: str, live: str, expected: str) -> bool:
    """The bundle's name, or (for a prefixable kind) its development-mode form."""

    if live == expected:
        return True
    if kind in EXACT_NAME_KINDS:
        return False
    head, base = expected.rpartition("/")[0::2] if kind == "experiments" else ("", expected)
    live_head, live_base = live.rpartition("/")[0::2] if kind == "experiments" else ("", live)
    prefix = _DEV_PREFIX.match(live_base)
    return head == live_head and prefix is not None and live_base[prefix.end() :] == base


def _root(path: str) -> str:
    return path.removeprefix("/Workspace").rstrip("/").casefold()


def state_entries(document: Mapping[str, Any] | None, where: str) -> tuple[dict[str, str], list[str], list[AdoptRefusal]]:
    """``(<kind>.<key> -> live id, child keys, refusals)`` of one state document."""

    bound: dict[str, str] = {}
    children: list[str] = []
    refusals: list[AdoptRefusal] = []
    for key, entry in sorted(((document or {}).get("state") or {}).items()):
        match = _STATE_KEY.fullmatch(str(key))
        if match is None:
            refusals.append(AdoptRefusal("malformed_state", where, "a key is not resources.<kind>.<key>"))
            continue
        symbolic = f"{match.group('kind')}.{match.group('name')}"
        if match.group("child"):
            children.append(f"{symbolic}.{match.group('child')}")
            continue
        live_id = entry.get("__id__") if isinstance(entry, Mapping) else None
        if not isinstance(live_id, str) or not live_id.strip():
            refusals.append(AdoptRefusal("malformed_state", where, f"{symbolic} has no live id"))
            continue
        bound[symbolic] = emit_mask(live_id.strip())
    return bound, children, refusals


def _lineage(document: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if document is None:
        return None
    serial = document.get("serial")
    return {
        "lineage": str(document.get("lineage") or ""),
        "serial": serial if isinstance(serial, int) and not isinstance(serial, bool) else 0,
    }


def _bundle_entries(plan: BundlePlan) -> tuple[dict[str, Any], list[AdoptRefusal]]:
    # A delete is a state key the bundle no longer declares (deployer_state_orphan), never a bundle key.
    entries = {e.symbolic: e for e in plan.entries if "." not in e.kind and e.action != "delete"}
    by_key: dict[str, list[str]] = {}
    for symbolic, entry in entries.items():
        by_key.setdefault(entry.name, []).append(symbolic)
    refusals = [
        AdoptRefusal("ambiguous_key", resource, "the bare key names more than one resource")
        for key, resources in sorted(by_key.items())
        if len(resources) > 1
        for resource in resources
    ]
    return entries, refusals


def _expected_names(entry: Any) -> tuple[tuple[str, ...] | None, str]:
    spec = KIND_LOOKUPS.get(entry.kind)
    if spec is None or entry.new_value is None:
        return None, "the create carries no resolved name"
    names = []
    for name_field in spec.name_fields:
        value = entry.new_value.get(name_field)
        if name_field in entry.new_vars or not isinstance(value, str) or not value.strip():
            return None, f"{name_field} is unresolved or empty"
        names.append(value)
    return tuple(names), ""


class _Planner:
    def __init__(self, inputs: AdoptInputs, workspace: Any) -> None:
        self.inputs = inputs
        self.workspace = workspace
        self.refusals: list[AdoptRefusal] = []
        self.errors: dict[str, str] = {}

    def refuse(self, code: str, resource: str, reason: str = "") -> None:
        self.refusals.append(AdoptRefusal(code, resource, reason))

    def check_live(self, resource: str, kind: str, live_id: str, expected: tuple[str, ...] | None) -> tuple[str, ...]:
        """The live names of one legacy object, refusing what the module docstring lists."""

        try:
            live = read_live(self.workspace, kind, live_id)
        except UnreadableId as exc:
            self.refuse("malformed_state", resource, str(exc))
            return ()
        except Exception as exc:  # noqa: BLE001 - any read failure is unproven, never absent
            self.refuse("read_failed", resource, type(exc).__name__)
            self.errors[resource] = str(exc)
            return ()
        if live is None:
            self.refuse("missing", resource)
            return ()
        if expected is not None:
            if kind == "dashboards":
                if live.names[0].rstrip("/") != expected[0].rstrip("/"):
                    self.refuse("dashboard_parent_moved", resource, "the live parent_path is not the configured one")
                if not same_name(kind, live.names[1], expected[1]):
                    self.refuse("renamed", resource, "display_name")
            elif not same_name(kind, live.names[0], expected[0]):
                self.refuse("renamed", resource, "name")
        if live.deployment_path:
            path = live.deployment_path
            roots = {_root(self.inputs.deployer_root), _root(self.inputs.legacy_root or "")}
            if not path.endswith(STATE_METADATA) or _root(path[: -len(STATE_METADATA)]) not in roots:
                self.refuse("foreign_bound", resource, "the bundle deployment metadata names another root")
        return live.names


def compute(
    phase: str,
    inputs: AdoptInputs,
    plan: BundlePlan,
    deployer_state: Mapping[str, Any] | None,
    legacy_state: Mapping[str, Any] | None,
    workspace: Any,
) -> AdoptPlan:
    """The adopt or unadopt plan; read-only (GETs by id for adopt, none for unadopt)."""

    if phase not in PHASES:
        raise ValueError("phase must be adopt or unadopt")
    planner = _Planner(inputs, workspace)
    if not inputs.legacy_root:
        planner.refuse("not_an_adopted_install", "profile.legacy_root")
    elif legacy_state is None:
        planner.refuse("legacy_state_missing", "legacy.state")
    bundle, refusals = _bundle_entries(plan)
    planner.refusals += refusals
    ours, _own_children, refusals = state_entries(deployer_state, "deployer.state")
    planner.refusals += refusals
    legacy, legacy_children, refusals = state_entries(legacy_state, "legacy.state")
    planner.refusals += refusals

    edits: list[Binding] = []
    already: list[str] = []
    claimed: dict[tuple[str, str], str] = {}
    for resource, live_id in sorted(legacy.items()):
        kind = resource.split(".", 1)[0]
        if (kind, live_id) in claimed:
            planner.refuse("duplicate_id", resource, f"also claimed by {claimed[(kind, live_id)]}")
            continue
        claimed[(kind, live_id)] = resource
        if resource not in bundle:
            if phase == "adopt":
                planner.refuse("legacy_key_unknown", resource)
            continue
        entry = bundle[resource]
        bound = ours.get(resource)
        other = sorted(k for k, v in ours.items() if v == live_id and k != resource and k.split(".", 1)[0] == kind)
        if (bound is not None and bound != live_id) or other:
            planner.refuse("bound_elsewhere", resource)
            continue
        if phase == "unadopt":
            if bound == live_id:
                edits.append(Binding(resource, entry.name, live_id))
            else:
                already.append(resource)
            continue
        if bound == live_id:
            planner.check_live(resource, kind, live_id, None)
            already.append(resource)
            continue
        if entry.action != "create":
            planner.refuse("state_mismatch", resource)
            continue
        expected, why = _expected_names(entry)
        if expected is None:
            planner.refuse("unresolved_name", resource, why)
            continue
        names = planner.check_live(resource, kind, live_id, expected)
        edits.append(Binding(resource, entry.name, live_id, names))

    # Both phases: a release plan would delete such a key's object, and
    # unadopt, which unbinds declared keys only, would leave it bound.
    for resource in sorted(set(ours) - set(bundle)):
        planner.refuse("deployer_state_orphan", resource)
    if phase == "unadopt":
        # adopt binds in key order (apps first) and its undo unbinds in
        # reverse; unadopt does the same, so a partial one leaves the App bound.
        edits.reverse()
    bound_after = (
        sorted(set(ours) | {edit.resource for edit in edits})
        if phase == "adopt"
        else sorted(set(ours) - {edit.resource for edit in edits})
    )
    expected_creates = sorted(set(bundle) - set(bound_after))
    document: dict[str, Any] = {
        "schema": PLAN_SCHEMA,
        "phase": phase,
        "client": inputs.client_key,
        "deployer_root": inputs.deployer_root,
        "legacy_root": inputs.legacy_root,
        "deployer_state": _lineage(deployer_state),
        "legacy_state": _lineage(legacy_state),
        "bind" if phase == "adopt" else "unbind": [edit.as_dict() for edit in edits],
        "already": sorted(already),
        "expected_creates": expected_creates,
        "legacy_children": sorted(legacy_children),
        "dashboards": {"parent_path_policy": DASHBOARD_POLICY, "owed": "spike S4"},
    }
    return AdoptPlan(
        phase=phase,
        document=document,
        edits=tuple(edits),
        already=tuple(sorted(already)),
        expected_creates=tuple(expected_creates),
        bound_after=tuple(bound_after),
        refusals=tuple(planner.refusals),
        errors=dict(planner.errors),
    )
