"""The owner's read-only preflight of an adoption's bind plan (cutover step 6b).

``scripts/deploy.sh -t client --phase adopt`` computes the adopt plan before
its lease (tools/databricks/install_state_adopt.py, ``plan``): which legacy
keys it would bind, and which it refuses because the legacy object is gone,
carries another name, is bound elsewhere or sits in another dashboard folder
(tools/databricks/adopt_legacy_map.py). In a cutover that plan first runs in
CI after both ownership handovers, so a refusal found there leaves the install
half handed over. This tool computes the same plan before the handovers, as
the owner, with the owner's own CLI profile:

  DATABRICKS_CONFIG_PROFILE=<owner-profile> python -m tools.databricks.adopt_preflight --report-out <private-dir>/adopt-preflight.json

It hands the unchanged ``adopt_legacy_map.compute`` the legacy state under
``MIP_LEGACY_BUNDLE_ROOT``, this deployer's state, one GET by id per legacy
object, and the name the bundle gives each key. Only that last input differs
from the adopt phase. There it is the bundle CLI's own plan
(``bundle_env plan -t client``), which only the CI deployer runs. Here
``declared_plan`` reads the names from databricks.yml and the client profile's
bundle variables (``tools.clients.profile.bundle_vars``, the values bundle_env
exports), and refuses (``DeclaredNameError``) every bundle shape that would
let the CLI plan other keys or other names than this reader sees: a client
target that is not ``mode: production`` or that sets a name prefix (either
would prefix names), a top-level ``include`` or target-level ``variables``
(either could add keys or change a name unseen), and a kind the plan gate has
no reviewed read for. A name field that is neither a literal nor exactly one
``${var.<name>}`` this profile or the variable's literal default resolves is
carried unresolved, so the plan refuses ``unresolved_name`` for that key and
never guesses a name.

It prints counts, symbolic keys, each refusal's code and remedy, the bind
plan document's sha256, the profile ack of the local profile it judged and one
verdict; never a name, an id or a path. The full plan, which names resources,
goes to ``--report-out`` only (mode 0600). The profile ack is the value every
governed plan prints as ``owner_attestation=``: a ``clear`` computed from a
local profile whose ack is not the one CI prints says nothing about the names
CI will use.

Read-only: workspace GETs and two state downloads; no bundle CLI, no child
process, no lease, no state edit and no workspace write. It is no
acknowledgement: the digest a mutating adopt run needs is the one the adopt
phase's own plan prints. It does not measure the CI deployer's own reads, the
CLI's plan, the pre-stop probes or adopt-live.

Exit 0: ``verdict: clear``. Exit 2: ``verdict: refused`` (the plan's own
codes), or ``verdict: not measured`` (the profile refuses, databricks.yml is
not a shape this reader resolves, the CLI profile is on another host than the
client profile, or a read failed: its class only).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.databricks import adopt_legacy_map as legacy_map  # noqa: E402
from tools.databricks.bundle_plan_lookups import KIND_LOOKUPS  # noqa: E402
from tools.databricks.bundle_plan_shape import BundlePlan, PlanShapeError, parse_plan  # noqa: E402
from tools.databricks.bundle_state import STATE_FILE, BundleStateError, read_state  # noqa: E402

_PREFIX = "[mip-adopt-preflight]"
TARGET = "client"
REPORT_SCHEMA = "mip.adopt-preflight.report.v1"
NAMES_FROM = "databricks.yml and the client profile's bundle variables (no bundle CLI plan)"
_VAR = re.compile(r"\$\{var\.([a-z_][a-z0-9_]*)\}")
_UNDECLARED = "(undeclared)"


class DeclaredNameError(RuntimeError):
    """databricks.yml is not a shape this reader resolves names from; ``code`` is reviewed, never a value."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _mapping(value: Any, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DeclaredNameError(code)
    return value


def _literal(value: Any) -> bool:
    return isinstance(value, str) and "${" not in value


def _resolved(raw: Any, variables: Mapping[str, str], declared: Mapping[str, Any]) -> str | None:
    """A name field's value: a literal, or one declared variable the profile or its literal default gives."""

    if _literal(raw):
        return str(raw)
    match = _VAR.fullmatch(raw) if isinstance(raw, str) else None
    if match is None or match.group(1) not in declared:
        return None
    name = match.group(1)
    if name in variables:
        return variables[name] if _literal(variables[name]) else None
    spec = declared[name]
    default = spec.get("default") if isinstance(spec, Mapping) else None
    return str(default) if _literal(default) else None


def declared_plan(bundle: Any, variables: Mapping[str, str]) -> BundlePlan:
    """Every resource the client target declares, as a pre-bind plan: a create with its resolved names."""

    top = _mapping(bundle, "not_a_bundle")
    if top.get("include"):
        raise DeclaredNameError("bundle_include")
    target = _mapping(_mapping(top.get("targets"), "no_client_target").get(TARGET), "no_client_target")
    if target.get("mode") != "production":
        raise DeclaredNameError("target_not_production")
    if target.get("variables"):
        raise DeclaredNameError("target_variables")
    for presets in (top.get("presets"), target.get("presets")):
        if presets is not None and "name_prefix" in _mapping(presets, "presets_shape"):
            raise DeclaredNameError("target_name_prefix")
    declared = _mapping(top.get("variables") or {}, "variables_shape")
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for resources in (top.get("resources"), target.get("resources")):
        for kind, entries in _mapping(resources or {}, "resources_shape").items():
            if kind not in KIND_LOOKUPS:
                raise DeclaredNameError("unknown_kind")
            for key, body in _mapping(entries or {}, "resources_shape").items():
                # The target's fields override the top-level ones, as the CLI merges them.
                resource = (str(kind), str(key))
                merged[resource] = {**merged.get(resource, {}), **_mapping(body or {}, "resources_shape")}
    plan: dict[str, Any] = {}
    for (kind, key), body in sorted(merged.items()):
        value: dict[str, str] = {}
        unresolved: dict[str, str] = {}
        for name_field in KIND_LOOKUPS[kind].name_fields:
            raw = body.get(name_field)
            name = _resolved(raw, variables, declared)
            if name is None:
                unresolved[name_field] = _UNDECLARED if raw is None else str(raw)
            value[name_field] = unresolved[name_field] if name is None else name
        state: dict[str, Any] = {"value": value}
        if unresolved:
            state["vars"] = unresolved
        plan[f"resources.{kind}.{key}"] = {"action": "create", "new_state": state}
    document = {"plan_version": 2, "cli_version": "", "lineage": "", "serial": 0, "plan": plan}
    try:
        return parse_plan(json.dumps(document))
    except PlanShapeError:
        raise DeclaredNameError("resource_key_shape") from None


def _profile() -> Any:
    from tools.clients.environment import from_local, resolve

    return resolve(from_local(os.environ, REPO / ".env.local"))


def _workspace() -> Any:
    from tools.databricks.workspace_auth import deployment_workspace_client

    return deployment_workspace_client()


def _write_private(path: Path, payload: Mapping[str, Any]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.chmod(path, 0o600)


def _not_measured(reason: str) -> int:
    print(f"{_PREFIX} REFUSED: {reason}", file=sys.stderr)
    print(f"{_PREFIX} verdict: not measured")
    return 2


def _print(plan: legacy_map.AdoptPlan, profile_ack: str) -> None:
    print(
        f"{_PREFIX} read-only (no bundle CLI, no lease, nothing bound): {len(plan.edits)} legacy keys would "
        f"bind, {len(plan.already)} already bound, {len(plan.expected_creates)} expected creates"
    )
    print(f"{_PREFIX} expected creates after the binds: {', '.join(plan.expected_creates) or 'none'}")
    for refusal in plan.refusals:
        detail = f" ({refusal.reason})" if refusal.reason else ""
        print(f"{_PREFIX} REFUSED {refusal.code} {refusal.resource}{detail}: {refusal.remedy}", file=sys.stderr)
    print(
        f"{_PREFIX} bind plan document sha256: {plan.sha256} (the digest the adopt phase's own plan is expected "
        "to print while the legacy state, the objects and their names are unchanged; this preflight is no "
        "acknowledgement)"
    )
    print(
        f"{_PREFIX} profile ack of the local profile judged: {profile_ack} (a governed plan prints it as "
        "owner_attestation=; if CI prints another, CI's profile is not this one, and this verdict does not "
        "cover the names CI will use)"
    )


def main(
    argv: list[str] | None = None,
    *,
    profile: Callable[[], Any] = _profile,
    workspace_factory: Callable[[], Any] = _workspace,
    bundle_path: Path = REPO / "databricks.yml",
) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--report-out", type=Path, help="where the full plan goes (it names resources): mode 0600")
    args = parser.parse_args(argv)
    from tools.clients import dispatch_acks
    from tools.clients import profile as views
    from tools.clients.errors import ProfileRefusal
    from tools.clients.live_identity import host_refusals
    from tools.databricks.install_state_adopt import inputs_from_profile

    try:
        resolved = profile()
        if not resolved.legacy_bundle_root:
            print(
                f"{_PREFIX} REFUSED not_an_adopted_install profile.legacy_root: "
                f"{legacy_map.REMEDIES['not_an_adopted_install']}",
                file=sys.stderr,
            )
            print(f"{_PREFIX} verdict: refused")
            return 2
        profile_ack = dispatch_acks.profile_ack(resolved.client, views.digest(resolved))
        bundle = declared_plan(yaml.safe_load(bundle_path.read_text(encoding="utf-8")), views.bundle_vars(resolved))
        inputs = inputs_from_profile(resolved)
        workspace = workspace_factory()
        if host_refusals(resolved, str(getattr(workspace.config, "host", "") or "")):
            return _not_measured(
                "the CLI profile's workspace host is not the client profile's DATABRICKS_HOST; nothing was read"
            )
        ours = read_state(workspace, f"{inputs.deployer_root}/{STATE_FILE}")
        legacy = read_state(workspace, f"{inputs.legacy_root}/{STATE_FILE}")
        plan = legacy_map.compute("adopt", inputs, bundle, ours, legacy, workspace)
    except ProfileRefusal as refusal:
        print(f"{_PREFIX} client profile refused:", file=sys.stderr)
        for line in refusal.lines:
            print(f"  - {line}", file=sys.stderr)
        print(f"{_PREFIX} verdict: not measured")
        return 2
    except DeclaredNameError as exc:
        return _not_measured(
            f"databricks.yml is not a shape this preflight reads names from ({type(exc).__name__}: {exc.code})"
        )
    except BundleStateError as exc:
        return _not_measured(str(exc))
    except Exception as exc:  # noqa: BLE001 - the class only: an SDK message can carry an id or a path
        return _not_measured(f"the read did not complete ({type(exc).__name__})")
    if args.report_out is not None:
        report = {**plan.full(), "preflight": {"schema": REPORT_SCHEMA, "names_from": NAMES_FROM, "profile_ack": profile_ack}}
        _write_private(args.report_out, report)
    _print(plan, profile_ack)
    print(f"{_PREFIX} verdict: {'refused' if plan.refused else 'clear'}")
    return 2 if plan.refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
