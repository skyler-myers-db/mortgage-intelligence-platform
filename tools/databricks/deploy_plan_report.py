"""The bundle plan gate's verdict, split into a public summary and a full report.

``PlanReport.public_summary()`` is what a public log, a GitHub step summary or
a CI artifact may carry: refusal codes, counts, digests and symbolic names
(the ``<kind>.<key>`` resource keys databricks.yml declares, which are the
same for every client). It never carries a resolved object name, a dev-mode
name prefix, a state lineage, a workspace path or a lookup result, and it
refuses to render if a value outside those classes reached it.

``PlanReport.full()`` adds what an operator needs to act: the resolved names
each create was looked up under, the lookup that answered, the state lineage
and serial, and the database-instance field diff an acknowledgement digests.
It stays with the operator: a local file today, the client workspace's
plan-reports folder once the governed plan phase writes one (M1-PR7).
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
    }
)
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
class LookupRecord:
    """One existence lookup a create needed (full report only)."""

    resource: str
    kind: str
    names: tuple[str, ...]
    unique_within: str
    method: str
    outcome: str  # "absent", "exists" or "failed: <error class>"


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
        report["instance_update"] = [dict(item) for item in self.instance_update]
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
