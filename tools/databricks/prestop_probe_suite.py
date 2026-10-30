"""The pre-stop probes that reuse step 0a's own assertions (M0-PR5).

``deploy_prestop_probes`` runs these after its Supervisor and Genie-grant
clauses, sharing what those already read (the App's identity and groups, the
Supervisor listing and every Genie ACL). Order matters only for inputs: the
rollback record comes first because it pins the governed Genie space and the
Gateway and Supervisor endpoints the later probes judge against. Each probe
collects its own refusals; none stops the others, so one run lists them all.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tools.databricks import genie_space_pin
from tools.databricks.prestop_genie_budget import (
    deployer_token_provider,
    genie_budget_refusals,
)
from tools.databricks.prestop_genie_grants import GenieAcl
from tools.databricks.prestop_owner_equivalent import (
    approved_principals,
    owner_equivalent_refusals,
)
from tools.databricks.prestop_owner_policy import owner_policy_refusals
from tools.databricks.prestop_refusals import OWNER_EQUIVALENT, PrestopRefusal
from tools.databricks.prestop_rollback_record import rollback_record_verdict
from tools.databricks.prestop_serving_endpoints import serving_endpoint_refusals
from tools.databricks.uc_owner_policy import (
    account_client_from_env,
    parse_approved_owner_principals,
)

# The provisioner's own resolution (attack G12, M1-PR3): the space step 0a
# updates is the one the pin record or the signed record names (a record whose
# space was retired is superseded), or the one title match an exact digest ack
# adopts; with neither it creates one. A title alone never names it.
GOVERNED_SPACE = genie_space_pin.resolve


@dataclass(frozen=True)
class ProbeSuiteConfig:
    """The deploy controls the suite needs; every value comes from deploy.sh."""

    app_name: str
    rollback_scope: str
    lakebase_instance: str
    catalog: str
    genie_space_name: str
    canonical_name: str
    rebase_unverified_app: bool
    deployer: str
    approved_owner_principals: str
    genie_manager_principals: str
    # MIP_GENIE_PIN_ACK: the sha256 of the read-only pin plan the preflight printed.
    genie_pin_ack: str = ""


def journal_state_reader(
    workspace: Any, config: ProbeSuiteConfig
) -> Callable[[], str]:
    """The signed first-install journal's read-only state, as the plan gate reads it."""

    def read() -> str:
        # Imported here: the journal's crypto stack is needed only for an existing App.
        from tools.databricks.app_first_install_journal import recovery_state

        return recovery_state(
            workspace,
            app_name=config.app_name,
            rollback_scope=config.rollback_scope,
            expected_lakebase_instance=config.lakebase_instance,
        )

    return read


def canonical_supervisor_endpoint(agents: list[dict[str, Any]], canonical_name: str) -> str:
    """The endpoint of the one Supervisor on the canonical name (the one cutover replaces)."""

    matches = [row for row in agents if row.get("display_name") == canonical_name.strip()]
    if len(matches) != 1:
        return ""
    return str(matches[0].get("endpoint_name") or "").strip()


def governed_genie_space(
    workspace: Any, *, contract: Any, config: ProbeSuiteConfig
) -> tuple[str, list[PrestopRefusal], list[str]]:
    """The space the provisioner resolves after step 0a, by its pin (never a title alone).

    With a signed record that is normally the record's own space. After a
    disaster-recovery retire (tools/databricks/genie_space_pin_retire.py) the
    record still names the gone space until the next capture, and the
    provisioner creates or uses another; the probes judge that one.
    """

    record_space = contract.genie_space_id if contract is not None else ""
    title = config.genie_space_name.strip()
    if not title:
        return record_space, [], []
    try:
        resolution = GOVERNED_SPACE(
            workspace,
            app_name=config.app_name,
            title=title,
            scope=config.rollback_scope,
            lakebase_instance=config.lakebase_instance,
            ack_plan=config.genie_pin_ack,
        )
    except genie_space_pin.GeniePinRefusal as exc:
        return "", [
            PrestopRefusal(
                OWNER_EQUIVALENT,
                f"the governed Genie space cannot be resolved by its pin ({exc}); the "
                "provisioner refuses the same after step 0a.",
            )
        ], []
    except Exception as exc:  # noqa: BLE001 - the class only: an SDK message can carry an id
        return "", [
            PrestopRefusal(
                OWNER_EQUIVALENT,
                f"the governed Genie space pin could not be read ({type(exc).__name__}); the "
                "provisioner refuses the same after step 0a.",
            )
        ], []
    space_id = resolution.space_id or ""
    if record_space and space_id == record_space:
        return space_id, [], []
    prefix = "the signed record names a retired Genie space" if record_space else "no signed record"
    if not space_id:
        return "", [], [
            f"{prefix}; no pin names a Genie space and none has the governed title yet; the "
            "provisioner creates and pins it"
        ]
    return space_id, [], [f"{prefix}; the governed Genie space is {space_id} by its {resolution.source}"]


def suite_refusals(
    workspace: Any,
    *,
    config: ProbeSuiteConfig,
    app_client_id: str,
    app_scim_id: str,
    app_groups: frozenset[str],
    agents: list[dict[str, Any]],
    acls: list[GenieAcl] | None,
    journal_state: Callable[[], str] | None = None,
    account_factory: Callable[[], Any] = account_client_from_env,
    genie_client_factory: Callable[..., Any] | None = None,
    token_provider: Callable[[], str] | None = None,
) -> tuple[list[PrestopRefusal], list[str]]:
    """Run the record, owner, owner-equivalent, serving and Genie-budget probes."""

    refusals: list[PrestopRefusal] = []
    notes: list[str] = []
    verdict = rollback_record_verdict(
        workspace,
        app_name=config.app_name,
        scope=config.rollback_scope,
        app_exists=bool(app_client_id),
        rebase_unverified_app=config.rebase_unverified_app,
        journal_state=journal_state or journal_state_reader(workspace, config),
    )
    refusals.extend(verdict.refusals)
    notes.extend(verdict.notes)
    contract = verdict.contract

    found, said = owner_policy_refusals(
        workspace,
        app_client_id=app_client_id,
        catalog=config.catalog,
        approved_owner_principals=parse_approved_owner_principals(
            config.approved_owner_principals
        ),
        app_groups=app_groups,
        account_factory=account_factory,
    )
    refusals.extend(found)
    notes.extend(said)

    genie_space_id, found, said = governed_genie_space(workspace, contract=contract, config=config)
    refusals.extend(found)
    notes.extend(said)
    found, said = owner_equivalent_refusals(
        workspace,
        catalog=config.catalog,
        genie_space_id=genie_space_id,
        acls=acls,
        approved=approved_principals(
            approved_owners=config.approved_owner_principals,
            deployer=config.deployer,
            genie_managers=config.genie_manager_principals,
        ),
    )
    refusals.extend(found)
    notes.extend(said)

    # The post-stop App audit reviews the record's Gateway and the Supervisor the
    # cutover replaces, and pin_journal replaces only the one on the canonical
    # name. So the record's own Supervisor endpoint is reviewed only while it is
    # that canonical one; once renamed, the App's access there is unrelated.
    pinned = [canonical_supervisor_endpoint(agents, config.canonical_name)]
    if contract is not None:
        pinned.append(contract.gateway_endpoint)
    found, said = serving_endpoint_refusals(
        workspace,
        app_name=config.app_name,
        app_client_id=app_client_id,
        app_scim_id=app_scim_id,
        app_groups=app_groups,
        pinned_endpoints=pinned,
    )
    refusals.extend(found)
    notes.extend(said)

    budget_kwargs: dict[str, Any] = {}
    if genie_client_factory is not None:
        budget_kwargs["client_factory"] = genie_client_factory
    found, said = genie_budget_refusals(
        host=str(getattr(getattr(workspace, "config", None), "host", "") or ""),
        token_provider=token_provider or deployer_token_provider(workspace),
        space_id=genie_space_id,
        **budget_kwargs,
    )
    refusals.extend(found)
    notes.extend(said)
    return refusals, notes
