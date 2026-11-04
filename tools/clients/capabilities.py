"""Read-only capability preflight: the workspace has every feature the selected tier needs (M2-PR3).

``tools.clients.live_checks`` runs this once the host, the workspace id, the
metastore id and the deployer hold, still before the signed lease and any App
stop (plan §4.15). The tier is the client profile's ``MIP_AGENTIC_TIER``,
never an ambient default.

Each feature is proven by one read of at most one page: a list call the
feature's API answers only when the feature is available to the deployer.

* Both tiers: Databricks Apps, Lakebase database instances, Genie spaces and
  SQL warehouses.
* The agentic tier adds Model Serving (the AI Gateway endpoints and the
  Supervisor's serving endpoint) and MLflow experiments. The Agent Bricks
  Supervisor API itself has no typed reader in the pinned SDK, and
  tools/clients never reaches the raw API client
  (tests/unit/test_bootstrap_account_safety.py); the agentic tier's pre-stop
  probes list the Supervisor agents before the lease anyway, and a listing
  that fails refuses there (``probe_error``).

Fail closed: any error refuses that feature. A typed not-found
(``NotFound``, ``ResourceDoesNotExist``) or ``NotImplemented`` reads as the
feature missing from the workspace; any other error as a read that could not
be completed. Every refusal is collected, none short-circuits the rest, and
each names the feature and the tier, never a value.

This preflight judges the workspace only. That the core tier cannot release
yet (its activation still needs the agentic identities) is the deploy's own
refusal, before the lease (scripts/lib/deploy_step_automation_identities.sh),
so a core ``--phase plan`` or adopt phase over an existing App still runs.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from databricks.sdk import errors as sdk_errors

from tools.clients.schema import ClientProfile
from tools.databricks.m2m_identity_contract import AgenticTier, agentic_tier

_MISSING = (sdk_errors.NotFound, sdk_errors.ResourceDoesNotExist, sdk_errors.NotImplemented)
_BOTH: tuple[AgenticTier, ...] = ("off", "on")
_AGENTIC: tuple[AgenticTier, ...] = ("on",)


def _first(items: Iterable[Any]) -> None:
    """Pull at most one item: a paginated list call proves the API answers."""

    for _item in items:
        break


@dataclass(frozen=True)
class Capability:
    """One workspace feature, the tiers that need it and its read-only probe."""

    name: str
    tiers: tuple[AgenticTier, ...]
    probe: Callable[[Any], None]


CAPABILITIES: tuple[Capability, ...] = (
    Capability("Databricks Apps", _BOTH, lambda client: _first(client.apps.list(page_size=1))),
    Capability(
        "Lakebase database instances",
        _BOTH,
        lambda client: _first(client.database.list_database_instances(page_size=1)),
    ),
    Capability("Genie spaces", _BOTH, lambda client: client.genie.list_spaces(page_size=1)),
    Capability("SQL warehouses", _BOTH, lambda client: _first(client.warehouses.list())),
    Capability(
        "Model Serving (AI Gateway endpoints)",
        _AGENTIC,
        lambda client: _first(client.serving_endpoints.list()),
    ),
    Capability(
        "MLflow experiments",
        _AGENTIC,
        lambda client: _first(client.experiments.list_experiments(max_results=1)),
    ),
)


def required(tier: AgenticTier) -> tuple[Capability, ...]:
    """The features a ``tier`` install needs, in probe order."""

    return tuple(capability for capability in CAPABILITIES if agentic_tier(tier) in capability.tiers)


def _tier_label(tier: AgenticTier) -> str:
    return "the agentic tier (MIP_AGENTIC_TIER=on)" if tier == "on" else "the core tier (MIP_AGENTIC_TIER=off)"


def check(profile: ClientProfile, client: Any) -> list[str]:
    """Every capability refusal for the profile's tier; empty when the workspace has them all."""

    try:
        tier = agentic_tier(profile.agentic_tier)
    except ValueError:
        return ["MIP_AGENTIC_TIER: must be exactly off or on"]
    lines = []
    for capability in required(tier):
        try:
            capability.probe(client)
        except _MISSING:
            lines.append(
                f"MIP_AGENTIC_TIER: {_tier_label(tier)} needs {capability.name}, which this "
                "workspace does not offer"
            )
        except Exception as exc:  # the class only: an SDK message can carry a host or an id
            lines.append(
                f"MIP_AGENTIC_TIER: could not prove {capability.name} for {_tier_label(tier)}: "
                f"{type(exc).__name__}"
            )
    return lines
