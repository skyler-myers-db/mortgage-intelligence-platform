"""The ``databricks bundle plan -o json`` document, parsed fail closed (spike S1).

The shape is derived from the Databricks CLI's own source at the installed
tag, v1.10.0, not from a live capture. No governed plan has run yet; the first
live capture is owed at the first governed plan phase (M1-PR7), and until then
this parser refuses every shape it does not know:

* ``bundle/deployplan/plan.go``: ``Plan`` is ``plan_version``,
  ``cli_version``, ``lineage``, ``serial`` and ``plan`` (resource key to
  ``PlanEntry``); ``currentPlanVersion = 2``. ``PlanEntry`` is ``id``,
  ``depends_on`` (``DependsOnEntry``: ``node``, ``label``), ``action``,
  ``gone``, ``new_state``, ``remote_state`` and ``changes`` (field path to
  ``ChangeDesc``: ``action``, ``reason``, ``old``, ``new``, ``remote``).
* ``bundle/deployplan/action.go``: the ``ActionType`` values ``skip``,
  ``resize``, ``update``, ``update_id``, ``create``, ``recreate`` and
  ``delete``. ``""`` is ``Undefined`` and never a planned action.
* ``libs/structs/structvar/structvar.go``: ``StructVarJSON`` is ``value``
  plus ``vars``, the references still unresolved at plan time.
* ``cmd/bundle/plan.go``: ``-o json`` prints ``json.MarshalIndent(plan)``.
* ``bundle/direct/bundle_plan.go``: keys are ``resources.<kind>.<key>`` plus
  the ``.permissions`` and ``.grants`` child nodes. An entry the state does
  not hold, or whose remote object is missing, plans ``create``; a state
  entry the configuration dropped plans ``delete`` (``gone`` when the remote
  object no longer exists).

The CLI's own loader (``LoadPlanFromFile``) decodes with
``DisallowUnknownFields`` and refuses another plan version. This parser is at
least as strict: an unknown or duplicate field at any level, a wrong type, an
unknown action or a key outside the ``resources.<kind>.<key>`` grammar raises
``PlanShapeError``. Error messages name fields and keys only, never values.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

PLAN_VERSION = 2
ACTIONS = frozenset({"skip", "resize", "update", "update_id", "create", "recreate", "delete"})
CHILD_NODES = frozenset({"permissions", "grants"})
_TOP_FIELDS = frozenset({"plan_version", "cli_version", "lineage", "serial", "plan"})
_ENTRY_FIELDS = frozenset(
    {"id", "depends_on", "action", "gone", "new_state", "remote_state", "changes"}
)
_DEPENDS_FIELDS = frozenset({"node", "label"})
_STATE_FIELDS = frozenset({"value", "vars"})
_CHANGE_FIELDS = frozenset({"action", "reason", "old", "new", "remote"})
_RESOURCE_KEY = re.compile(
    r"resources\.(?P<kind>[a-z][a-z0-9_]*)\.(?P<name>[A-Za-z0-9][A-Za-z0-9_-]*)"
    r"(?:\.(?P<child>[a-z]+))?\Z"
)


class PlanShapeError(ValueError):
    """The plan JSON is not the reviewed v1.10.0 shape."""


@dataclass(frozen=True)
class PlanChange:
    """One ``ChangeDesc``: the planned action for one field path."""

    action: str
    reason: str
    old: Any
    new: Any
    remote: Any


@dataclass(frozen=True)
class PlanEntry:
    """One ``PlanEntry`` with its key split into kind and bundle key."""

    key: str
    kind: str
    name: str
    action: str
    gone: bool
    new_value: Mapping[str, Any] | None
    new_vars: Mapping[str, str]
    changes: Mapping[str, PlanChange]

    @property
    def symbolic(self) -> str:
        """``<kind>.<key>`` as databricks.yml names it (``jobs.mip_ref_seed``)."""

        base, _, child = self.kind.partition(".")
        return f"{base}.{self.name}" + (f".{child}" if child else "")


@dataclass(frozen=True)
class BundlePlan:
    """A parsed plan; ``entries`` are sorted by key, as ``Plan.GetActions`` sorts them."""

    plan_version: int
    cli_version: str
    lineage: str
    serial: int
    entries: tuple[PlanEntry, ...]
    sha256: str

    def entry(self, key: str) -> PlanEntry | None:
        return next((entry for entry in self.entries if entry.key == key), None)


def canonical_json(value: Any) -> str:
    """One stable encoding for digests: sorted keys, no whitespace, ASCII only."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("ascii")).hexdigest()


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PlanShapeError(f"duplicate field {key!r}")
        result[key] = value
    return result


def _refuse_constant(name: str) -> Any:
    raise PlanShapeError(f"non-JSON number constant {name}")


def _object(value: Any, where: str, allowed: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PlanShapeError(f"{where} is not an object")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise PlanShapeError(f"{where} has unknown field(s) {', '.join(unknown)}")
    return value


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise PlanShapeError(f"{where} is not a string")
    return value


def _integer(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PlanShapeError(f"{where} is not an integer")
    return value


def _action(value: Any, where: str) -> str:
    action = _text(value, where)
    if action not in ACTIONS:
        raise PlanShapeError(f"{where} is not a known action")
    return action


def _changes(value: Any, where: str) -> dict[str, PlanChange]:
    if not isinstance(value, dict):
        raise PlanShapeError(f"{where} is not an object")
    changes: dict[str, PlanChange] = {}
    for path, raw in value.items():
        body = _object(raw, f"{where}[{path!r}]", _CHANGE_FIELDS)
        if "action" not in body:
            raise PlanShapeError(f"{where}[{path!r}] has no action")
        changes[path] = PlanChange(
            action=_action(body["action"], f"{where}[{path!r}].action"),
            reason=_text(body.get("reason", ""), f"{where}[{path!r}].reason"),
            old=body.get("old"),
            new=body.get("new"),
            remote=body.get("remote"),
        )
    return changes


def _entry(key: str, raw: Any) -> PlanEntry:
    where = f"plan[{key!r}]"
    match = _RESOURCE_KEY.fullmatch(key)
    if match is None:
        raise PlanShapeError(f"{where}: the key is not resources.<kind>.<key>[.<child>]")
    child = match.group("child")
    if child is not None and child not in CHILD_NODES:
        raise PlanShapeError(f"{where}: unknown child node")
    body = _object(raw, where, _ENTRY_FIELDS)
    if "action" not in body:
        raise PlanShapeError(f"{where} has no action")
    if "id" in body:
        _text(body["id"], f"{where}.id")
    gone = body.get("gone", False)
    if not isinstance(gone, bool):
        raise PlanShapeError(f"{where}.gone is not a boolean")
    depends = body.get("depends_on", [])
    if not isinstance(depends, list):
        raise PlanShapeError(f"{where}.depends_on is not a list")
    for index, item in enumerate(depends):
        edge = _object(item, f"{where}.depends_on[{index}]", _DEPENDS_FIELDS)
        _text(edge.get("node"), f"{where}.depends_on[{index}].node")
        _text(edge.get("label", ""), f"{where}.depends_on[{index}].label")
    new_value: Mapping[str, Any] | None = None
    new_vars: dict[str, str] = {}
    if "new_state" in body:
        state = _object(body["new_state"], f"{where}.new_state", _STATE_FIELDS)
        if "value" not in state:
            raise PlanShapeError(f"{where}.new_state has no value")
        if not isinstance(state["value"], dict):
            raise PlanShapeError(f"{where}.new_state.value is not an object")
        new_value = state["value"]
        refs = state.get("vars", {})
        if not isinstance(refs, dict):
            raise PlanShapeError(f"{where}.new_state.vars is not an object")
        new_vars = {path: _text(ref, f"{where}.new_state.vars") for path, ref in refs.items()}
    kind = match.group("kind") + (f".{child}" if child else "")
    return PlanEntry(
        key=key,
        kind=kind,
        name=match.group("name"),
        action=_action(body["action"], f"{where}.action"),
        gone=gone,
        new_value=new_value,
        new_vars=new_vars,
        changes=_changes(body.get("changes", {}), f"{where}.changes"),
    )


def parse_plan(text: str) -> BundlePlan:
    """Parse ``bundle plan -o json`` output, or raise ``PlanShapeError``."""

    try:
        document = json.loads(
            text, object_pairs_hook=_no_duplicate_keys, parse_constant=_refuse_constant
        )
    except json.JSONDecodeError as exc:
        raise PlanShapeError(f"the plan is not JSON (line {exc.lineno})") from None
    top = _object(document, "the plan document", _TOP_FIELDS)
    version = _integer(top.get("plan_version", 0), "plan_version")
    if version != PLAN_VERSION:
        raise PlanShapeError(f"plan_version {version} is not the reviewed version {PLAN_VERSION}")
    if "plan" not in top:
        raise PlanShapeError("the plan document has no plan map")
    if not isinstance(top["plan"], dict):
        raise PlanShapeError("plan is not an object")
    serial = _integer(top.get("serial", 0), "serial")
    if serial < 0:
        raise PlanShapeError("serial is negative")
    entries = tuple(
        sorted((_entry(key, raw) for key, raw in top["plan"].items()), key=lambda e: e.key)
    )
    return BundlePlan(
        plan_version=version,
        cli_version=_text(top.get("cli_version", ""), "cli_version"),
        lineage=_text(top.get("lineage", ""), "lineage"),
        serial=serial,
        entries=entries,
        sha256=sha256_hex(document),
    )
