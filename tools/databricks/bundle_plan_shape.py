"""The ``databricks bundle plan -o json`` document, parsed fail closed (spike S1).

The shape is derived from the Databricks CLI's own source at the installed
tag, v1.10.0, and measured live on that version on throwaway bundle roots:
spike S9 parsed 14 documents for one experiment (create, skip, update and
recreate, with ``remote_state`` and ``changes``), and spike S10 the whole
bundle's fresh-root plan (18 creates over the 8 declared kinds, with
``depends_on`` and ``vars``; tests/fixtures/bundle_plans/ rebuilds both
synthetically). No governed run has parsed one yet. Plans #1 and #2 of an
adoption stop at the client live checks and run no bundle validate or plan;
the first governed parse is the adopt phase's pre-bind plan (the first-client
cutover's 10B-1, ``install_state_adopt plan``), and for a new client the
first plan phase that passes the live checks. The parser refuses every shape
it does not know:

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
``PlanShapeError``. Its message names fields, keys and change paths, never
values, and stays private all the same: in a plan over bound objects a change
path or a duplicate field can be a remote map key (a tag key, a task key). A
public line prints only ``PlanShapeError.public``: the symbolic resource
(``<kind>.<key>``, or ``bundle.plan`` for the document) and a field class
from ``FIELD_CLASSES``. Each one also carries the document's value-free
skeleton (``bundle_plan_skeleton``) for the private report.

A shape this parser accepts can still mean something else when another CLI
made it, so the document's ``cli_version`` must be exactly
``REVIEWED_CLI_VERSION`` (no prefix, suffix, padding or other release), right
after ``plan_version`` and before any entry is read: ``CliVersionRefused``, a
``PlanShapeError`` whose public reason also names the two version strings
(``cli_version "1.7.0", reviewed "1.10.0"``; a value outside the version
characters prints as ``(not a version string)``). The one document no CLI
made, the owner's adopt preflight's declared plan, says so with
``DECLARED_CLI_VERSION`` and is held to exactly that.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from tools.databricks.bundle_plan_skeleton import skeleton

# The one Databricks CLI version the governed path was reviewed on: this
# parser, the plan gate and every cutover spike. deploy-dev.yml's setup-cli pin
# must install exactly it (tests/unit/test_governed_cli_version.py), and
# ``parse_plan`` refuses a plan whose ``cli_version`` is any other string.
REVIEWED_CLI_VERSION = "1.10.0"
# The ``cli_version`` of a plan document no CLI produced: adopt_preflight's
# declared plan, built from databricks.yml. No other caller may expect it.
DECLARED_CLI_VERSION = ""
# What a public line may print of a version string; anything else is a class.
_PUBLIC_VERSION = re.compile(r"[0-9A-Za-z.+-]{1,64}\Z")

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


# What a refusal's public line may say about where the document broke.
FIELD_CLASSES = frozenset(
    {
        "json", "duplicate_field", "number_constant", "document", "plan_version",
        "cli_version", "lineage", "serial", "plan", "key", "child_node", "entry",
        "id", "gone", "depends_on", "new_state", "action", "changes",
    }
)  # fmt: skip
DOCUMENT_RESOURCE = "bundle.plan"


class PlanShapeError(ValueError):
    """The plan JSON is not the reviewed v1.10.0 shape.

    ``str()`` is the private message. ``resource`` is the entry's symbolic
    ``<kind>.<key>[.<child>]`` when its key is in the grammar, else
    ``DOCUMENT_RESOURCE``; ``field`` is one of ``FIELD_CLASSES``; ``public``
    joins the two for a public line. ``parse_plan`` sets ``skeleton``.
    """

    def __init__(self, message: str, *, field: str, key: str = "") -> None:
        super().__init__(message)
        if field not in FIELD_CLASSES:
            raise ValueError("a plan shape refusal names an unreviewed field class")
        match = _RESOURCE_KEY.fullmatch(key)
        child = match.group("child") if match is not None else None
        self.field = field
        self.resource = DOCUMENT_RESOURCE
        if match is not None and (child is None or child in CHILD_NODES):
            self.resource = f"{match.group('kind')}.{match.group('name')}" + (f".{child}" if child else "")
        self.skeleton: dict[str, Any] | None = None

    @property
    def reason(self) -> str:
        """The public reason: the field class (``CliVersionRefused`` adds the two versions)."""

        return self.field

    @property
    def public(self) -> str:
        """``<resource> (<reason>)``: no change path, no map key, no value."""

        return f"{self.resource} ({self.reason})"

    def private(self) -> dict[str, Any]:
        """The private report's record: the message and the document's skeleton."""

        return {
            "code": "malformed_plan",
            "resource": self.resource,
            "field": self.field,
            "message": str(self),
            "skeleton": self.skeleton,
        }


def public_version(value: str) -> str:
    """A version string as a public line may print it: quoted, or ``(not a version string)``."""

    return json.dumps(value) if value == "" or _PUBLIC_VERSION.fullmatch(value) else "(not a version string)"


class CliVersionRefused(PlanShapeError):
    """The plan's ``cli_version`` is not exactly the version the parser expects.

    Its public reason names both version strings (harmless: printable version
    characters only); the private message carries the found value's repr.
    """

    def __init__(self, found: str, expected: str) -> None:
        super().__init__(
            f"cli_version {found[:80]!r} is not {expected!r}, the Databricks CLI version this parser "
            "was reviewed on: a plan another CLI made can mean something the gates were never reviewed on",
            field="cli_version",
        )
        self.found = found
        self.expected = expected

    @property
    def reason(self) -> str:
        return f"cli_version {public_version(self.found)}, reviewed {public_version(self.expected)}"


def _expected(value: str) -> str:
    """The expected ``cli_version``: the reviewed one, or the declared plan's; a third is a bug."""

    if value not in (REVIEWED_CLI_VERSION, DECLARED_CLI_VERSION):
        raise ValueError("a plan is expected from the reviewed CLI, or from no CLI at all")
    return value


def cli_version_refusal(found: str, expected: str = REVIEWED_CLI_VERSION) -> CliVersionRefused | None:
    """``None`` when ``found`` is exactly ``expected``; else the refusal (exact string equality)."""

    return None if found == _expected(expected) else CliVersionRefused(found, expected)


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


def require_reviewed_cli(plan: BundlePlan) -> BundlePlan:
    """``plan``, or ``CliVersionRefused`` unless the reviewed CLI made it (a governed consumer's own check)."""

    refused = cli_version_refusal(plan.cli_version)
    if refused is not None:
        raise refused
    return plan


def canonical_json(value: Any) -> str:
    """One stable encoding for digests: sorted keys, no whitespace, ASCII only."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("ascii")).hexdigest()


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PlanShapeError(f"duplicate field {key!r}", field="duplicate_field")
        result[key] = value
    return result


def _refuse_constant(name: str) -> Any:
    raise PlanShapeError(f"non-JSON number constant {name}", field="number_constant")


def _object(value: Any, where: str, allowed: frozenset[str], field: str, key: str = "") -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PlanShapeError(f"{where} is not an object", field=field, key=key)
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise PlanShapeError(f"{where} has unknown field(s) {', '.join(unknown)}", field=field, key=key)
    return value


def _text(value: Any, where: str, field: str, key: str = "") -> str:
    if not isinstance(value, str):
        raise PlanShapeError(f"{where} is not a string", field=field, key=key)
    return value


def _integer(value: Any, where: str, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PlanShapeError(f"{where} is not an integer", field=field)
    return value


def _action(value: Any, where: str, field: str, key: str) -> str:
    action = _text(value, where, field, key)
    if action not in ACTIONS:
        raise PlanShapeError(f"{where} is not a known action", field=field, key=key)
    return action


def _changes(value: Any, where: str, key: str) -> dict[str, PlanChange]:
    if not isinstance(value, dict):
        raise PlanShapeError(f"{where} is not an object", field="changes", key=key)
    changes: dict[str, PlanChange] = {}
    for path, raw in value.items():
        at = f"{where}[{path!r}]"
        body = _object(raw, at, _CHANGE_FIELDS, "changes", key)
        if "action" not in body:
            raise PlanShapeError(f"{at} has no action", field="changes", key=key)
        changes[path] = PlanChange(
            action=_action(body["action"], f"{at}.action", "changes", key),
            reason=_text(body.get("reason", ""), f"{at}.reason", "changes", key),
            old=body.get("old"),
            new=body.get("new"),
            remote=body.get("remote"),
        )
    return changes


def _entry(key: str, raw: Any) -> PlanEntry:
    where = f"plan[{key!r}]"
    match = _RESOURCE_KEY.fullmatch(key)
    if match is None:
        raise PlanShapeError(f"{where}: the key is not resources.<kind>.<key>[.<child>]", field="key")
    child = match.group("child")
    if child is not None and child not in CHILD_NODES:
        raise PlanShapeError(f"{where}: unknown child node", field="child_node")
    body = _object(raw, where, _ENTRY_FIELDS, "entry", key)
    if "action" not in body:
        raise PlanShapeError(f"{where} has no action", field="entry", key=key)
    if "id" in body:
        _text(body["id"], f"{where}.id", "id", key)
    gone = body.get("gone", False)
    if not isinstance(gone, bool):
        raise PlanShapeError(f"{where}.gone is not a boolean", field="gone", key=key)
    depends = body.get("depends_on", [])
    if not isinstance(depends, list):
        raise PlanShapeError(f"{where}.depends_on is not a list", field="depends_on", key=key)
    for index, item in enumerate(depends):
        at = f"{where}.depends_on[{index}]"
        edge = _object(item, at, _DEPENDS_FIELDS, "depends_on", key)
        _text(edge.get("node"), f"{at}.node", "depends_on", key)
        _text(edge.get("label", ""), f"{at}.label", "depends_on", key)
    new_value: Mapping[str, Any] | None = None
    new_vars: dict[str, str] = {}
    if "new_state" in body:
        state = _object(body["new_state"], f"{where}.new_state", _STATE_FIELDS, "new_state", key)
        if "value" not in state:
            raise PlanShapeError(f"{where}.new_state has no value", field="new_state", key=key)
        if not isinstance(state["value"], dict):
            raise PlanShapeError(f"{where}.new_state.value is not an object", field="new_state", key=key)
        new_value = state["value"]
        refs = state.get("vars", {})
        if not isinstance(refs, dict):
            raise PlanShapeError(f"{where}.new_state.vars is not an object", field="new_state", key=key)
        new_vars = {path: _text(ref, f"{where}.new_state.vars", "new_state", key) for path, ref in refs.items()}
    kind = match.group("kind") + (f".{child}" if child else "")
    return PlanEntry(
        key=key,
        kind=kind,
        name=match.group("name"),
        action=_action(body["action"], f"{where}.action", "action", key),
        gone=gone,
        new_value=new_value,
        new_vars=new_vars,
        changes=_changes(body.get("changes", {}), f"{where}.changes", key),
    )


def _parse(text: str, expected_cli_version: str) -> BundlePlan:
    try:
        document = json.loads(
            text, object_pairs_hook=_no_duplicate_keys, parse_constant=_refuse_constant
        )
    except json.JSONDecodeError as exc:
        raise PlanShapeError(f"the plan is not JSON (line {exc.lineno})", field="json") from None
    top = _object(document, "the plan document", _TOP_FIELDS, "document")
    version = _integer(top.get("plan_version", 0), "plan_version", "plan_version")
    if version != PLAN_VERSION:
        raise PlanShapeError(
            f"plan_version {version} is not the reviewed version {PLAN_VERSION}", field="plan_version"
        )
    # Before any entry: a plan another CLI made refuses as that, whatever else it holds.
    cli_version = _text(top.get("cli_version", ""), "cli_version", "cli_version")
    refused = cli_version_refusal(cli_version, expected_cli_version)
    if refused is not None:
        raise refused
    if "plan" not in top:
        raise PlanShapeError("the plan document has no plan map", field="document")
    if not isinstance(top["plan"], dict):
        raise PlanShapeError("plan is not an object", field="plan")
    serial = _integer(top.get("serial", 0), "serial", "serial")
    if serial < 0:
        raise PlanShapeError("serial is negative", field="serial")
    entries = tuple(
        sorted((_entry(key, raw) for key, raw in top["plan"].items()), key=lambda e: e.key)
    )
    return BundlePlan(
        plan_version=version,
        cli_version=cli_version,
        lineage=_text(top.get("lineage", ""), "lineage", "lineage"),
        serial=serial,
        entries=entries,
        sha256=sha256_hex(document),
    )


def parse_plan(text: str, *, expected_cli_version: str = REVIEWED_CLI_VERSION) -> BundlePlan:
    """Parse ``bundle plan -o json`` output, or raise ``PlanShapeError`` carrying its skeleton.

    The document's ``cli_version`` must equal ``expected_cli_version`` exactly:
    ``REVIEWED_CLI_VERSION`` for every plan a CLI made, ``DECLARED_CLI_VERSION``
    only for adopt_preflight's declared plan. No third value is accepted.
    """

    expected = _expected(expected_cli_version)
    try:
        return _parse(text, expected)
    except PlanShapeError as exc:
        exc.skeleton = skeleton(text)
        raise
