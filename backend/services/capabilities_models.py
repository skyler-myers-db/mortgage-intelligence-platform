"""Shared capability vocabulary: status enum, row shapes, and claim limits.

Single responsibility: define what a capability row *is* and what may be
claimed, so capability discovery and the live workspace probes agree on
one vocabulary without importing each other.

``backend.services.capabilities`` re-exports every public name here, so
existing import sites keep working unchanged.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum


class CapabilityStatus(str, Enum):
    """Honest maturity/availability state for a single capability."""

    #: GA underlying capability + backing dependency present + configured.
    AVAILABLE = "available"
    #: Flag/credential on and dependency present, but not yet exercised.
    CONFIGURED = "configured"
    #: GA-capable but the backing dependency or credential is missing, OR
    #: the gating feature flag is off. Renders as an honest "not provisioned".
    NOT_PROVISIONED = "not_provisioned"
    #: Preview / no-public-API capability, shown only as a labelled roadmap
    #: pattern (mirror flag on). Never "integrated".
    PREVIEW_MIRROR = "preview_mirror"
    #: Preview capability, mirror flag off -> hidden from the product.
    HIDDEN = "hidden"


# Statuses the product MAY present as an active, working capability. Configured
# means the local/deploy contract exists but has not been live-probed; it must
# render as "configured" rather than "working".
_CLAIMABLE = frozenset({CapabilityStatus.AVAILABLE})


@dataclass(frozen=True)
class Capability:
    """One row of the capability snapshot."""

    key: str
    label: str
    #: Is the underlying Databricks capability Generally Available?
    ga: bool
    status: CapabilityStatus
    detail: str

    @property
    def claimable(self) -> bool:
        """True only when the product may present this as a live capability."""
        return self.status in _CLAIMABLE

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "ga": self.ga,
            "status": self.status.value,
            "claimable": self.claimable,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class LiveCapabilityStatus:
    """Result of a bounded live capability probe."""

    available: bool
    detail: str


LiveCapabilityMap = Mapping[str, LiveCapabilityStatus]
MIN_GROWTH_AGENT_EVAL_CASES = 5
_AI_GATEWAY_CAPABILITY_REQUEST_PREFIX = "mip-capability-"
_AI_GATEWAY_EXACT_LOG_WAIT_S = 90.0
_AI_GATEWAY_EXACT_LOG_ATTEMPTS = 1


def _configured_csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())
