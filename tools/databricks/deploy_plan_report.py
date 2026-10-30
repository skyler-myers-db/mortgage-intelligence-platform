"""The bundle plan gate's verdict, split into a public summary and a full report.

``PlanReport.public_summary()`` is what a public log, a GitHub step summary or
a CI artifact may carry: refusal codes, counts, digests and symbolic names
(the ``<kind>.<key>`` resource keys databricks.yml declares, which are the
same for every client). It never carries a resolved object name, a dev-mode
name prefix, a state lineage, a workspace path or a lookup result, and it
refuses to render if a value outside those classes reached it.

``PlanReport.full()`` adds what an operator needs to act: the resolved names
each create was looked up under, the lookup that answered, the state lineage
and serial, the database-instance update an acknowledgement digests, and why
a deferred collision was deferred.
It stays with the operator: a local file today, the client workspace's
plan-reports folder once the governed plan phase writes one (M1-PR7).

The **App ACL preview** (M0-PR5, pre-lease only) says what the run's release
access convergence will do to the existing App's direct ACL: which grants it
keeps, adds, removes or changes (``bundle_plan_app_acl``). The public summary
carries its code and those four counts; only ``full()`` names the principals.

The **EXECUTE-gap prediction** (M0-PR8, attack O12) says whether the catalog
bootstrap will run, and so republish the reviewed functions while blue serves
(``catalog_bootstrap_skip_rule``): ``skip`` when the live catalog already
equals the rendered sources, ``gap`` with the republished functions and the
predicted interval otherwise, ``unknown`` when the rule did not report.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

PUBLIC_SCHEMA = "mip.bundle-plan-report.public.v1"
FULL_SCHEMA = "mip.bundle-plan-report.full.v1"
POSITIONS = frozenset({"pre-lease", "pre-apply"})
REFUSAL_CODES = frozenset(
    {
        "malformed_plan",
        "unknown_kind",
        "delete",
        "recreate",
        "id_change",
        "collision",
        "lookup_failed",
        "instance_update_unacked",
        "not_adopted",
        "app_acl",
    }
)
DEFERRAL_CODES = frozenset({"journal_owned"})
APP_ACL_CODES = frozenset({"unchanged", "changes", "app_absent", "refused"})
APP_ACL_COUNTS = ("keep", "add", "remove", "change")
EXECUTE_GAP_CODES = frozenset({"skip", "gap", "unknown"})
EXECUTE_GAP_KINDS = ("catalogs", "schemas", "tables", "table_properties", "functions")
COUNTED_ACTIONS = ("create", "update", "resize", "update_id", "recreate", "delete", "skip")
_SYMBOLIC = re.compile(r"[a-z][a-z0-9_]*\.[A-Za-z0-9][A-Za-z0-9_-]*(?:\.(?:permissions|grants))?\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_CLI_VERSION = re.compile(r"[0-9A-Za-z.+-]{0,64}\Z")


class UnsafePublicValue(ValueError):
    """A value outside the public classes reached ``public_summary``."""


@dataclass(frozen=True)
class Refusal:
    """One gate refusal. ``detail`` is full-report only; the rest is public."""

    code: str
    resource: str
    remedy: str
    related: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class Deferral:
    """A collision the gate leaves to a later, lease-held step. ``detail`` is full-report only."""

    code: str
    resource: str
    note: str
    detail: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class LookupRecord:
    """One existence lookup a create needed (full report only)."""

    resource: str
    kind: str
    names: tuple[str, ...]
    unique_within: str
    method: str
    outcome: str  # "absent", "exists", "journal-owned (<state>)" or "failed: <error class>"


@dataclass(frozen=True)
class AclGrant:
    """One direct App ACL grant (full report only): principal field, name and level."""

    kind: str  # "user_name", "service_principal_name" or "group_name"
    principal: str
    level: str

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "principal": self.principal, "level": self.level}


@dataclass(frozen=True)
class AppAclPreview:
    """What the release access convergence will do to the App's direct ACL."""

    code: str
    keep: tuple[AclGrant, ...] = ()
    add: tuple[AclGrant, ...] = ()
    remove: tuple[AclGrant, ...] = ()
    change: tuple[tuple[AclGrant, AclGrant], ...] = ()

    def counts(self) -> dict[str, int]:
        return {name: len(getattr(self, name)) for name in APP_ACL_COUNTS}

    def public(self) -> dict[str, Any]:
        if self.code not in APP_ACL_CODES:
            raise UnsafePublicValue("an App ACL preview code is not a reviewed code")
        return {"code": self.code, **self.counts()}

    def full(self) -> dict[str, Any]:
        return {
            **self.public(),
            "keep": [grant.as_dict() for grant in self.keep],
            "add": [grant.as_dict() for grant in self.add],
            "remove": [grant.as_dict() for grant in self.remove],
            "change": [
                {"from": before.as_dict(), "to": after.as_dict()} for before, after in self.change
            ],
        }


@dataclass(frozen=True)
class ExecuteGapPrediction:
    """Whether the catalog bootstrap republishes blue's reviewed functions, and for how long."""

    code: str
    functions: tuple[str, ...] = ()
    differing: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    unknown_statements: int = 0
    predicted_seconds: int = 0
    rendered_sha256: str = ""
    live_sha256: str = ""
    detail: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def from_skip_rule(cls, report: Mapping[str, Any] | None) -> ExecuteGapPrediction:
        if not report:
            return cls(code="unknown")
        differing = report.get("differing") or {}
        return cls(
            code=str(report.get("code") or "unknown"),
            functions=tuple(str(name) for name in report.get("functions_republished") or ()),
            differing={kind: len(differing.get(kind) or ()) for kind in EXECUTE_GAP_KINDS},
            unknown_statements=len(report.get("unknown_statements") or ()),
            predicted_seconds=int((report.get("execute_gap") or {}).get("predicted_seconds") or 0),
            rendered_sha256=str(report.get("rendered_sha256") or ""),
            live_sha256=str(report.get("live_sha256") or ""),
            detail={
                "differing": {kind: list(differing.get(kind) or ()) for kind in EXECUTE_GAP_KINDS},
                "unknown_statements": list(report.get("unknown_statements") or ()),
                "live_error": str(report.get("live_error") or ""),
                "execute_gap": dict(report.get("execute_gap") or {}),
            },
        )

    def public(self) -> dict[str, Any]:
        if self.code not in EXECUTE_GAP_CODES:
            raise UnsafePublicValue("an EXECUTE-gap code is not a reviewed code")
        for digest in (self.rendered_sha256, self.live_sha256):
            if digest and _DIGEST.fullmatch(digest) is None:
                raise UnsafePublicValue("an EXECUTE-gap digest is not a sha256 hex digest")
        return {
            "code": self.code,
            "functions": [_symbolic(name) for name in self.functions],
            "differing": {kind: int(self.differing.get(kind, 0)) for kind in EXECUTE_GAP_KINDS},
            "unknown_statements": int(self.unknown_statements),
            "predicted_seconds": int(self.predicted_seconds),
            "rendered_sha256": self.rendered_sha256,
            "live_sha256": self.live_sha256,
        }

    def full(self) -> dict[str, Any]:
        return {**self.public(), "detail": dict(self.detail)}


@dataclass(frozen=True)
class PlanReport:
    """What the gate found in one plan, at one position of the deploy."""

    position: str
    app_resource: str
    app_bound: bool
    plan_sha256: str = ""
    plan_version: int = 0
    cli_version: str = ""
    lineage: str = ""
    serial: int = 0
    counts: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    refusals: tuple[Refusal, ...] = ()
    instance_update_sha256: str = ""
    instance_update: tuple[Mapping[str, Any], ...] = ()
    lookups: tuple[LookupRecord, ...] = ()
    deferrals: tuple[Deferral, ...] = ()
    app_acl: AppAclPreview | None = None
    execute_gap: ExecuteGapPrediction | None = None

    @property
    def refused(self) -> bool:
        return bool(self.refusals)

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(refusal.code for refusal in self.refusals)

    def public_summary(self) -> dict[str, Any]:
        """Codes, counts, digests and symbolic names only; refuses anything else."""

        if self.position not in POSITIONS:
            raise UnsafePublicValue("position is not a reviewed gate position")
        _symbolic(self.app_resource)
        for digest in (self.plan_sha256, self.instance_update_sha256):
            if digest and _DIGEST.fullmatch(digest) is None:
                raise UnsafePublicValue("a digest is not a sha256 hex digest")
        if _CLI_VERSION.fullmatch(self.cli_version) is None:
            raise UnsafePublicValue("cli_version is not a version string")
        counts = {action: int(self.counts.get(action, 0)) for action in COUNTED_ACTIONS}
        refusals = []
        for refusal in self.refusals:
            if refusal.code not in REFUSAL_CODES:
                raise UnsafePublicValue("a refusal code is not a reviewed code")
            refusals.append(
                {
                    "code": refusal.code,
                    "resource": _symbolic(refusal.resource),
                    "related": [_symbolic(key) for key in refusal.related],
                }
            )
        deferred = []
        for deferral in self.deferrals:
            if deferral.code not in DEFERRAL_CODES:
                raise UnsafePublicValue("a deferral code is not a reviewed code")
            deferred.append({"code": deferral.code, "resource": _symbolic(deferral.resource)})
        return {
            "schema": PUBLIC_SCHEMA,
            "position": self.position,
            "verdict": "refused" if self.refused else "passed",
            "plan_sha256": self.plan_sha256,
            "plan_version": int(self.plan_version),
            "cli_version": self.cli_version,
            "counts": counts,
            "app": {"resource": self.app_resource, "bound": bool(self.app_bound)},
            "instance_update_sha256": self.instance_update_sha256,
            "refusals": refusals,
            "deferred": deferred,
            "app_acl": None if self.app_acl is None else self.app_acl.public(),
            "execute_gap": None if self.execute_gap is None else self.execute_gap.public(),
        }

    def full(self) -> dict[str, Any]:
        """The public summary plus resolved names, lookups, lineage and the instance diff."""

        report = self.public_summary()
        report["schema"] = FULL_SCHEMA
        report["lineage"] = self.lineage
        report["serial"] = self.serial
        report["refusals"] = [
            {
                **public,
                "remedy": refusal.remedy,
                "detail": dict(refusal.detail),
            }
            for public, refusal in zip(report["refusals"], self.refusals, strict=True)
        ]
        report["deferred"] = [
            {**public, "note": deferral.note, "detail": dict(deferral.detail)}
            for public, deferral in zip(report["deferred"], self.deferrals, strict=True)
        ]
        report["instance_update"] = [dict(item) for item in self.instance_update]
        report["app_acl"] = None if self.app_acl is None else self.app_acl.full()
        report["execute_gap"] = None if self.execute_gap is None else self.execute_gap.full()
        report["lookups"] = [
            {
                "resource": record.resource,
                "kind": record.kind,
                "names": list(record.names),
                "unique_within": record.unique_within,
                "method": record.method,
                "outcome": record.outcome,
            }
            for record in self.lookups
        ]
        return report


def _symbolic(value: str) -> str:
    if not isinstance(value, str) or _SYMBOLIC.fullmatch(value) is None:
        raise UnsafePublicValue("a resource is not a symbolic <kind>.<key> name")
    return value
