"""``python -m tools.databricks.estate.cli --plan | --check --ack-plan SHA | --emit-exposure-inventory``.

* ``--plan`` prints the accepted-exposure plan's sha256 and a public summary
  (entry counts by identity, class and kind, and the expiry dates). The
  plan itself is the profile's ``MIP_ACCEPTED_EXPOSURE_JSON``; the owner
  reviews it there.
* ``--check --ack-plan SHA`` exits 0 only when the recomputed plan's digest is
  ``SHA``: the same check every consumer runs against
  ``MIP_ACK_ACCEPTED_EXPOSURE_PLAN`` before it judges an exposure.
* ``--emit-exposure-inventory`` reads, as the workspace-admin deployer, every
  Genie grant that reaches the App or the agent-runtime outside the governed
  space and, with ``--catalog``, the agent-runtime's direct grants on every
  accessible foreign catalog, and prints each as a candidate entry for the
  owner's review. It never applies anything: each candidate keeps a
  ``<placeholder>`` reason and expiry, which the policy refuses until the owner
  fills them in after review. A grant that can never be accepted (it reaches
  the verifier, or it is elevated for the agent-runtime) is printed as a note
  with its remedy instead. Every identifying value (ids, names, grantees,
  paths, workspace ids) is registered as a runner mask before it prints, as
  the profile tools do (``tools.clients.redact.emit_mask``), so a CI log shows
  the structure and the schema vocabulary only.

Read-only: GET and list calls only. Exit 0 on success, 2 on a refusal.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from tools.clients.profile import _maskable, iter_leaves
from tools.clients.redact import emit_mask
from tools.clients.schema import JSON_LEAF_CLASS, LeafClass
from tools.clients.shapes import INHERITED_SOURCE_PREFIX
from tools.databricks.estate.accepted_exposure import (
    ELEVATED,
    EXPOSURE_ENV,
    UC_GRANT,
    Exposure,
    ExposurePolicy,
    ExposureRefusal,
    load_policy,
    privilege_class,
)
from tools.databricks.estate.genie_exposure import entry_exposures

_PREFIX = "[mip-exposure]"
_REVIEW = {"reason": "<owner review: why this exposure is acceptable>", "expires_at": "<expiry date>"}
_LEAVES = JSON_LEAF_CLASS[EXPOSURE_ENV]
_PLATFORM_CATALOGS = frozenset({"__databricks_internal", "samples", "system"})


def _summary(policy: ExposurePolicy) -> list[str]:
    by_identity = Counter(identity for entry in policy.entries for identity in entry.identities)
    by_class = Counter(entry.privilege_class for entry in policy.entries)
    by_kind = Counter(entry.exposure.kind for entry in policy.entries)
    expired = sum(1 for entry in policy.entries if entry.expires_at <= policy.now)
    expiries = sorted({entry.expires_text for entry in policy.entries})

    def counts(counter: Counter[str]) -> str:
        return ", ".join(f"{name}={count}" for name, count in sorted(counter.items())) or "none"

    return [
        f"accepted-exposure plan sha256={policy.digest} entries={len(policy.entries)}",
        f"identities: {counts(by_identity)}; classes: {counts(by_class)}; kinds: {counts(by_kind)}",
        f"expires: {', '.join(expiries) or 'none'}; already expired: {expired}",
    ]


def mask_candidate(candidate: Mapping[str, Any]) -> None:
    """Register every identifying leaf of a candidate entry before it prints."""

    for path, leaf in iter_leaves(dict(candidate)):
        if _LEAVES.get(f"[].{path}", LeafClass.IDENTIFYING) is not LeafClass.IDENTIFYING:
            continue
        value = leaf.removeprefix(INHERITED_SOURCE_PREFIX) if path == "source" else leaf
        if _maskable(value):
            emit_mask(value)


def _note(exposure: Exposure, text: str) -> str:
    """A note names the exposure's space or catalog and grantee: masked like a candidate."""

    mask_candidate(exposure.canonical())
    return text


def candidate(exposure: Exposure, identities: Iterable[str]) -> dict[str, Any]:
    """An ``ExposureEntry`` for the owner to review; the review fields stay placeholders."""

    return {
        **exposure.canonical(),
        "identities": sorted(set(identities)),
        "privilege_class": privilege_class(exposure),
        **_REVIEW,
    }


def genie_candidates(
    workspace: Any,
    *,
    app_name: str,
    runtime_application_id: str,
    verifier_application_id: str,
    governed_title: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Group and direct Genie grants reaching the App or the runtime, as the audits read them."""

    from tools.databricks.agent_runtime_access import _field, _genie_spaces, _items, _text
    from tools.databricks.agent_runtime_uc_baseline import authoritative_workspace_id
    from tools.databricks.deploy_prestop_probes import (
        audited_principals,
        exposure_labels,
        principal_effective_groups,
    )
    from tools.databricks.m2m_access_policy import resolve_effective_groups

    principals = audited_principals(
        workspace,
        app_name=app_name,
        runtime_application_id=runtime_application_id,
        verifier_application_id=verifier_application_id,
    )
    labels = exposure_labels(principals, runtime_application_id=runtime_application_id)
    groups = {
        label: principal_effective_groups(workspace, scim_id=scim_id, resolve_groups=resolve_effective_groups)
        for label, scim_id in principals.resolved.items()
    }
    direct = {principals.app_client_id: "app", runtime_application_id: "runtime"}
    verifier_groups = next((value for label, value in groups.items() if label.startswith("verifier ")), frozenset())
    titles = _genie_spaces(workspace)
    workspace_id = authoritative_workspace_id(workspace)
    candidates: list[dict[str, Any]] = []
    notes: list[str] = []
    for space_id in sorted(titles):
        if governed_title and titles[space_id] == governed_title:
            continue
        acl = workspace.api_client.do("GET", f"/api/2.0/permissions/genie/{space_id}")
        for entry in _items(_field(acl, "access_control_list")):
            group = _text(_field(entry, "group_name"))
            reached = {labels[label] for label, held in groups.items() if label in labels and group and group in held}
            principal = _text(_field(entry, "service_principal_name"))
            if principal in direct and principal:
                reached.add(direct[principal])
            if not reached:
                continue
            for exposure in entry_exposures(entry, space_id=space_id, title=titles[space_id], workspace_id=workspace_id):
                if group and group in verifier_groups:
                    notes.append(
                        _note(
                            exposure,
                            f"Genie space {space_id}: group {group!r} also reaches the verifier, which "
                            "never consults a matcher: the space owner must remove or narrow the grant",
                        )
                    )
                    continue
                identities = set(reached)
                if privilege_class(exposure) == ELEVATED and "runtime" in identities:
                    identities.discard("runtime")
                    notes.append(
                        _note(
                            exposure,
                            f"Genie space {space_id}: {exposure.grantee!r} holds an elevated level for "
                            "the agent-runtime, which is never accepted: the owner must narrow it to "
                            "CAN_VIEW or CAN_RUN, or the agentic tier stays off",
                        )
                    )
                if identities:
                    candidates.append(candidate(exposure, identities))
    return candidates, notes


def uc_candidates(workspace: Any, *, catalog: str, runtime_application_id: str) -> tuple[list[dict[str, Any]], list[str]]:
    """The agent-runtime's direct grants on each accessible foreign catalog."""

    from tools.databricks.agent_runtime_uc_baseline import authoritative_workspace_id
    from tools.databricks.agent_runtime_uc_inventory import _effective_privilege_sources
    from tools.databricks.audit_agent_runtime_foreign_uc_access import _catalog_bindings

    workspace_id = authoritative_workspace_id(workspace)
    candidates: list[dict[str, Any]] = []
    notes: list[str] = []
    for item in workspace.catalogs.list(include_browse=True, include_unbound=True):
        name = str(getattr(item, "name", "") or "")
        mode = str(getattr(getattr(item, "isolation_mode", None), "value", getattr(item, "isolation_mode", "")) or "")
        if not name or name == catalog or name in _PLATFORM_CATALOGS:
            continue
        bound = sorted(_catalog_bindings(workspace, catalog=name)) if mode.upper() == "ISOLATED" else []
        if mode.upper() == "ISOLATED" and workspace_id not in bound:
            continue  # binding-denied: the reviewed binding policy owns it
        held: dict[str, set[str]] = {}
        for privilege, sources in _effective_privilege_sources(
            workspace, securable_type="catalog", full_name=name, principal=runtime_application_id
        ).items():
            for grantee, parent_type, _parent in sources:
                if not parent_type:
                    held.setdefault(grantee, set()).add(privilege)
        for grantee, privileges in sorted(held.items()):
            exposure = Exposure(
                kind=UC_GRANT,
                securable_type="CATALOG",
                securable_id=name,
                securable_name=name,
                grantee=grantee,
                privileges=frozenset(privileges),
                source="direct",
                isolation_mode=mode.upper(),
                workspace_ids=tuple(bound),
            )
            if privilege_class(exposure) == ELEVATED:
                notes.append(
                    _note(
                        exposure,
                        f"catalog {name}: {grantee!r} holds {', '.join(sorted(privileges))} for the "
                        "agent-runtime, which is elevated and never accepted: the catalog owner must "
                        "narrow it to BROWSE, USE_CATALOG, USE_SCHEMA or SELECT, or the agentic tier "
                        "stays off",
                    )
                )
                continue
            candidates.append(candidate(exposure, ["runtime"]))
    return candidates, notes


def _emit(candidates: Sequence[Mapping[str, Any]], notes: Sequence[str]) -> None:
    for item in candidates:
        mask_candidate(item)
        print(json.dumps(item, sort_keys=True, separators=(",", ":")))
    for note in notes:
        print(f"{_PREFIX} note: {note}", file=sys.stderr)
    print(
        f"{_PREFIX} {len(candidates)} candidate entries for the owner's review; nothing was applied. "
        f"Fill in each reason and expiry, add the reviewed ones to {EXPOSURE_ENV}, then acknowledge "
        "the plan (--plan).",
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--emit-exposure-inventory", action="store_true")
    parser.add_argument("--ack-plan", default="", help="--check: the reviewed plan's sha256")
    for name in ("--app-name", "--runtime-application-id", "--verifier-application-id", "--expected-inventory-principal"):
        parser.add_argument(name, default="")
    parser.add_argument("--genie-space-name", default="")
    parser.add_argument("--catalog", default="", help="--emit-exposure-inventory: also list UC candidates")
    args = parser.parse_args(argv)
    try:
        if args.plan:
            for line in _summary(load_policy(require_ack=False)):
                print(f"{_PREFIX} {line}")
            return 0
        if args.check:
            policy = load_policy(ack=args.ack_plan)
            print(f"{_PREFIX} the plan sha256={policy.digest} is acknowledged ({len(policy.entries)} entries)")
            return 0
    except ExposureRefusal as exc:
        for line in exc.lines:
            print(f"{_PREFIX} REFUSED: {line}", file=sys.stderr)
        return 2
    missing = [
        flag
        for flag, value in (
            ("--app-name", args.app_name),
            ("--runtime-application-id", args.runtime_application_id),
            ("--verifier-application-id", args.verifier_application_id),
            ("--expected-inventory-principal", args.expected_inventory_principal),
        )
        if not value
    ]
    if missing:
        parser.error("--emit-exposure-inventory requires " + ", ".join(missing))
    from databricks.sdk import WorkspaceClient
    from tools.databricks.audit_global_m2m_access import assert_workspace_admin_inventory_identity

    workspace = WorkspaceClient()
    assert_workspace_admin_inventory_identity(workspace, expected_principal=args.expected_inventory_principal)
    candidates, notes = genie_candidates(
        workspace,
        app_name=args.app_name,
        runtime_application_id=args.runtime_application_id,
        verifier_application_id=args.verifier_application_id,
        governed_title=args.genie_space_name,
    )
    if args.catalog:
        found, said = uc_candidates(workspace, catalog=args.catalog, runtime_application_id=args.runtime_application_id)
        candidates.extend(found)
        notes.extend(said)
    _emit(candidates, notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
