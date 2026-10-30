"""Read-only deploy probes that refuse before step 0a stops the live App.

Step 0a of ``scripts/deploy.sh`` stops the existing App (and, on the unsigned
rebase path, quarantines its access). With no signed blue record a later
failure leaves the App stopped. These late failures are visible to a
workspace admin before any mutation, so this probe refuses them up front:

1. **Supervisor retirement.** A canonical-name Supervisor whose creator is not
   the configured agent-runtime service principal is replaced, pinned and then
   permanently deleted with its serving endpoint. That needs the operator to
   name it: ``MIP_RETIRE_HUMAN_SUPERVISOR=<supervisor id>``
   (``supervisor_retirement_ack``). It must also carry the complete identity
   the cutover journal pins (id, endpoint, creator, create time), or
   ``pin_journal`` refuses it after step 0a whatever the acknowledgement.
2. **Reserved replacement names.** The Supervisor planner
   (``agentic_supervisor_endpoint.supervisor_candidates``) selects a Supervisor
   on a reserved runtime replacement name (the legacy `` [mip-agent-runtime]``
   suffix or a contract-hashed `` [mip-agent-runtime-<hash>]`` name) as the
   green candidate and then refuses it when the runtime SP did not create it.
   The current contract hash needs the governed Genie space, which is not
   known this early, so every non-runtime Supervisor on a reserved name is
   refused. Renaming it out of the reserved names is the remedy.
3. **Genie grants.** The global Genie audits
   (``agent_runtime_access._assert_no_genie_access`` and
   ``_assert_exact_genie_can_run``) refuse every Genie space, the governed one
   included, that grants any permission to a group the audited service
   principal belongs to. The probe applies that same group clause to the App,
   agent-runtime and verifier service principals. The verifier audit
   (``--forbid-all-genie``) also refuses every grant to the verifier itself,
   which needs no governed space id, so the probe refuses those too. Direct
   App and agent-runtime grants are judged against the governed space, which
   is provisioned later, so they stay with the post-stop audits. A group
   grant the client profile accepts for the App or the agent-runtime
   (``MIP_ACCEPTED_EXPOSURE_JSON``, M2-PR4) is judged by that identity's
   matcher, as the post-stop audit judges it (``prestop_accepted_exposure``);
   the verifier never consults one, and a policy the audits would refuse
   refuses here (``accepted_exposure``).

Group membership is resolved the way the audits' admin-side fallback resolves
it (``m2m_access_policy.resolve_effective_groups``), plus the system ``users``
group: Databricks adds every workspace principal to it, including an App
service principal a first install has not created yet. A runtime or verifier
application ID that does not resolve to exactly one SCIM id (or an existing
App without a complete identity) is refused in the same pass and still
audited for ``users``; ``prestop_genie_grants`` names each grant's source and
builds the remedy for direct and inherited grants. The post-stop audits
still read each target's own ``/Me`` projection, which can also carry
account-level nested groups workspace SCIM does not show, so this probe is an
early refusal, not a replacement for those audits. It changes no exemption
rule and performs only GET/list calls.

Five more probes (M0-PR5) each call the very assertion the post-stop step they
protect calls, so a refusal here is the refusal that step would raise with the
App down:

4. **Rollback record** (``prestop_rollback_record``): signed, absent, drifted
   or tampered, and on the unsigned rebase path the bootstrap gate's absence
   proof, moved here from step 0a.
5. **Owner policy** (``prestop_owner_policy``): ``ApprovedOwnerPolicy`` on the
   governed catalog, its audit schema and the treatment table, and
   ``MetastoreOwnerPolicy`` (exactly ``MIP_UC_METASTORE_OWNER``) on the metastore.
6. **Owner-equivalent grantees** (``prestop_owner_equivalent``, attack G12).
7. **Serving endpoints** (``prestop_serving_endpoints``): App access only on
   record-pinned endpoints.
8. **Genie budget** (``prestop_genie_budget``): one real turn on the
   record-pinned space, the only call that is not a GET or a list.

Every refusal carries a reviewed code (``prestop_refusals``). A probe whose
reads fail refuses (``probe_error``) instead of passing; ``main`` exits 2 on
any refusal, before the signed lease and any App stop.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from tools.databricks.agent_runtime_access import _field, _text
from tools.databricks.prestop_accepted_exposure import PrestopExposure, load_prestop_exposure
from tools.databricks.prestop_genie_grants import (
    GenieAcl,
    genie_acls,
    genie_group_grants,
    genie_group_remedy,
    shared_folder_names,
    verifier_genie_grants,
    verifier_genie_remedy,
)
from tools.databricks.prestop_probe_suite import ProbeSuiteConfig, suite_refusals
from tools.databricks.prestop_refusals import (
    AUDITED_PRINCIPAL_UNRESOLVED,
    GENIE_GROUP_GRANT,
    PROBE_ERROR,
    RESERVED_REPLACEMENT_NAME,
    SUPERVISOR_RETIREMENT,
    VERIFIER_GENIE_GRANT,
    PrestopRefusal,
)
from tools.databricks.supervisor_agent_contract import (
    RUNTIME_REPLACEMENT_PREFIX,
    RUNTIME_REPLACEMENT_SUFFIX,
)
from tools.databricks.supervisor_retirement_ack import (
    ACKNOWLEDGEMENT_ENV,
    is_runtime_created,
    supervisor_retirement_acknowledgement,
    supervisor_retirement_refusal,
)

SYSTEM_USERS_GROUP = "users"
APPROVED_OWNERS_ENV = "MIP_UC_APPROVED_OWNER_PRINCIPALS"
GENIE_MANAGERS_ENV = "MIP_GENIE_MANAGER_PRINCIPALS"
# The identity pin_journal signs for a replaced Supervisor; a gap fails after step 0a.
PINNED_SUPERVISOR_FIELDS = ("supervisor_agent_id", "endpoint_name", "creator", "create_time")
_REPLACEMENT_HASH = r"[0-9a-f]{12}"


def is_reserved_replacement_name(name: str, canonical_name: str) -> bool:
    """Whether ``name`` is one of ``canonical_name``'s reserved runtime replacement names."""

    if name == f"{canonical_name}{RUNTIME_REPLACEMENT_SUFFIX}":
        return True
    pattern = (
        re.escape(canonical_name)
        + re.escape(RUNTIME_REPLACEMENT_PREFIX)
        + _REPLACEMENT_HASH
        + r"\](?:-mq1)?"
    )
    return re.fullmatch(pattern, name) is not None


def supervisor_retirement_refusals(
    agents: list[dict[str, Any]],
    *,
    canonical_name: str,
    runtime_application_id: str,
    acknowledgement: str,
) -> tuple[list[str], list[str]]:
    """Return ``(refusals, notes)`` for the canonical-name Supervisor."""

    name = canonical_name.strip()
    if not name:
        raise ValueError("canonical Supervisor name is required")
    if name != canonical_name:
        # The planner matches display names exactly and the cutover journal
        # strips them, so a padded name could select one Supervisor and pin
        # another; the probe cannot say which would be retired.
        return [
            f"the canonical Supervisor name {canonical_name!r} (MIP_AGENT_SUPERVISOR_NAME) "
            "has leading or trailing whitespace. The Supervisor planner matches display "
            "names exactly while the cutover journal strips them, so the deploy could "
            "select a different Supervisor than the one it pins. Remove the whitespace, "
            "then re-run."
        ], []
    matches = [row for row in agents if row.get("display_name") == name]
    if len(matches) > 1:
        return [
            f"multiple Supervisor agents use the canonical name {name!r}; manual "
            "governance review is required before any deploy can select one"
        ], []
    if not matches:
        notes = [f"no Supervisor holds the canonical name {name!r}; nothing will be retired"]
        if acknowledgement:
            notes.append(f"{ACKNOWLEDGEMENT_ENV} is set but unused")
        return [], notes
    canonical = matches[0]
    supervisor_id = str(canonical.get("supervisor_agent_id") or "").strip()
    creator = canonical.get("creator")
    if is_runtime_created(creator, runtime_application_id=runtime_application_id):
        return [], [f"canonical Supervisor {supervisor_id} is runtime-owned; blue/green applies"]
    missing = [field for field in PINNED_SUPERVISOR_FIELDS if not _text(canonical.get(field))]
    if missing:
        return [
            f"the canonical Supervisor {name!r} ({supervisor_id or 'no id'}) was not created "
            "by the configured agent-runtime service principal and has an incomplete "
            f"immutable identity (missing {', '.join(missing)}). The cutover journal cannot "
            "pin it, so the deploy would refuse after step 0a whatever "
            f"{ACKNOWLEDGEMENT_ENV} says; manual governance review is required."
        ], []
    refusal = supervisor_retirement_refusal(
        supervisor_id=supervisor_id,
        creator=creator,
        runtime_application_id=runtime_application_id,
        acknowledgement=acknowledgement,
        endpoint=str(canonical.get("endpoint_name") or "").strip(),
        canonical_name=name,
    )
    if refusal is not None:
        return [refusal], []
    return [], [
        f"{ACKNOWLEDGEMENT_ENV} acknowledges the permanent deletion of Supervisor "
        f"{supervisor_id} and its serving endpoint after the green cutover"
    ]


def reserved_replacement_refusals(
    agents: list[dict[str, Any]],
    *,
    canonical_name: str,
    runtime_application_id: str,
) -> list[str]:
    """Refuse a non-runtime Supervisor that holds a reserved replacement name."""

    name = canonical_name.strip()
    refusals: list[str] = []
    for row in agents:
        display_name = str(row.get("display_name") or "")
        if not is_reserved_replacement_name(display_name, name):
            continue
        creator = row.get("creator")
        if is_runtime_created(creator, runtime_application_id=runtime_application_id):
            continue
        refusals.append(
            f"Supervisor {_text(row.get('supervisor_agent_id')) or '<no id>'!r} holds the "
            f"reserved runtime replacement name {display_name!r} but was created by "
            f"{_text(creator) or '<missing>'!r}, not by the configured agent-runtime service "
            f"principal {runtime_application_id.strip()!r}. The Supervisor planner selects a "
            "reserved name as the green candidate and refuses this creator after step 0a. "
            "Rename it to a name outside the reserved replacement names (it is neither "
            "pinned nor deleted), then re-run."
        )
    return refusals


class PrincipalResolutionError(RuntimeError):
    """An audited principal does not resolve to exactly one workspace SCIM id."""


@dataclass(frozen=True)
class AuditedPrincipals:
    """SCIM ids by label, plus each principal the probe could not resolve and why."""

    resolved: dict[str, str]
    unresolved: dict[str, str]
    notes: tuple[str, ...]
    app_client_id: str = ""
    app_scim_id: str = ""


def _scim_id(workspace: Any, *, label: str, application_id: str) -> str:
    """Resolve one audited application ID to its SCIM id, naming the principal."""

    application = application_id.strip()
    if not application:
        raise ValueError(f"the {label} application ID is required")
    escaped = application.replace("\\", "\\\\").replace('"', '\\"')
    matches = [
        principal
        for principal in workspace.service_principals.list(filter=f'applicationId eq "{escaped}"')
        if _text(_field(principal, "application_id")) == application
    ]
    if len(matches) != 1:
        remedy = "add it to this workspace" if not matches else "remove the duplicates"
        raise PrincipalResolutionError(
            f"expected exactly one {label} service principal for {application!r}, "
            f"found {len(matches)}; {remedy}"
        )
    principal_id = _text(_field(matches[0], "id"))
    if not principal_id:
        raise PrincipalResolutionError(
            f"the {label} service principal {application!r} has no SCIM id; manual "
            "identity review is required"
        )
    return principal_id


def principal_effective_groups(
    workspace: Any,
    *,
    scim_id: str,
    resolve_groups: Callable[..., Mapping[str, str]],
) -> frozenset[str]:
    """Workspace-visible direct and nested groups plus the system ``users`` group."""

    groups = {str(name) for name in resolve_groups(workspace, sp_id=scim_id).values()}
    groups.add(SYSTEM_USERS_GROUP)
    return frozenset(groups)


def audited_principals(
    workspace: Any,
    *,
    app_name: str,
    runtime_application_id: str,
    verifier_application_id: str,
) -> AuditedPrincipals:
    """Resolve each audited principal's SCIM id; a not-yet-created App is skipped.

    A principal that does not resolve to exactly one SCIM id is returned as
    unresolved, so the caller can refuse it alongside every other refusal
    instead of stopping at the first identity gap. Any read error propagates.
    """

    notes: list[str] = []
    resolved: dict[str, str] = {}
    unresolved: dict[str, str] = {}
    app_client_id = app_scim_id = ""
    try:
        app = workspace.apps.get(app_name)
    except (NotFound, ResourceDoesNotExist):
        app = None
        notes.append(f"App {app_name!r} does not exist yet; its future SP joins 'users'")
    if app is not None:
        app_client_id = _text(_field(app, "service_principal_client_id"))
        app_scim_id = _text(_field(app, "service_principal_id"))
        if app_client_id and app_scim_id:
            resolved[f"App SP {app_client_id}"] = app_scim_id
        else:
            unresolved[f"App {app_name!r} SP"] = (
                f"existing App {app_name!r} has an incomplete identity; manual identity "
                "review is required"
            )
    for label, application_id in (
        ("agent-runtime", runtime_application_id),
        ("verifier", verifier_application_id),
    ):
        key = f"{label} SP {application_id}"
        try:
            resolved[key] = _scim_id(workspace, label=label, application_id=application_id)
        except PrincipalResolutionError as exc:
            unresolved[key] = str(exc)
    complete = bool(app_client_id and app_scim_id)
    return AuditedPrincipals(
        resolved=resolved,
        unresolved=unresolved,
        notes=tuple(notes),
        app_client_id=app_client_id if complete else "",
        app_scim_id=app_scim_id if complete else "",
    )


def exposure_labels(
    principals: AuditedPrincipals, *, runtime_application_id: str
) -> dict[str, str]:
    """The audited labels that have an accepted-exposure identity: the App and the runtime."""

    labels: dict[str, str] = {}
    if principals.app_client_id:
        labels[f"App SP {principals.app_client_id}"] = "app"
    runtime = f"agent-runtime SP {runtime_application_id}"
    if runtime in principals.resolved:
        labels[runtime] = "runtime"
    return labels


@dataclass(frozen=True)
class BaseVerdict:
    """The Supervisor and Genie-grant clauses' refusals, plus what they read."""

    refusals: list[PrestopRefusal]
    notes: list[str]
    agents: list[dict[str, Any]]
    acls: list[GenieAcl]
    principals: AuditedPrincipals
    effective: dict[str, frozenset[str]]

    @property
    def app_groups(self) -> frozenset[str]:
        """The App SP's workspace-visible groups plus ``users`` (just ``users`` if absent)."""

        label = f"App SP {self.principals.app_client_id}"
        return self.effective.get(label, frozenset({SYSTEM_USERS_GROUP}))


def base_verdict(
    workspace: Any,
    *,
    app_name: str,
    runtime_application_id: str,
    verifier_application_id: str,
    canonical_name: str,
    acknowledgement: str,
    list_agents: Callable[[], list[dict[str, Any]]],
    resolve_groups: Callable[..., Mapping[str, str]],
    exposure: PrestopExposure | None = None,
) -> BaseVerdict:
    """Run the Supervisor and Genie-grant clauses; every refusal carries its code.

    ``exposure`` drops each App or agent-runtime principal whose accepted-exposure
    matcher accepts a group grant (``prestop_accepted_exposure``); the verifier
    is never relaxed.
    """

    agents = list_agents()
    retirement, notes = supervisor_retirement_refusals(
        agents,
        canonical_name=canonical_name,
        runtime_application_id=runtime_application_id,
        acknowledgement=acknowledgement,
    )
    refusals = [PrestopRefusal(SUPERVISOR_RETIREMENT, message) for message in retirement]
    refusals.extend(
        PrestopRefusal(RESERVED_REPLACEMENT_NAME, message)
        for message in reserved_replacement_refusals(
            agents,
            canonical_name=canonical_name,
            runtime_application_id=runtime_application_id,
        )
    )
    principals = audited_principals(
        workspace,
        app_name=app_name,
        runtime_application_id=runtime_application_id,
        verifier_application_id=verifier_application_id,
    )
    notes.extend(principals.notes)
    refusals.extend(
        PrestopRefusal(
            AUDITED_PRINCIPAL_UNRESOLVED,
            f"cannot read the {label}'s group memberships: {reason}, then re-run. The probe "
            "refuses rather than pass a principal it could not audit.",
        )
        for label, reason in principals.unresolved.items()
    )
    effective = {
        label: principal_effective_groups(
            workspace, scim_id=scim_id, resolve_groups=resolve_groups
        )
        for label, scim_id in principals.resolved.items()
    }
    # An unresolved principal joins the system 'users' group once it exists, so
    # every 'users' grant is still listed for it.
    effective.update(
        {label: frozenset({SYSTEM_USERS_GROUP}) for label in principals.unresolved}
    )
    acls = genie_acls(workspace)
    grants = genie_group_grants(workspace, effective_groups=effective, acls=acls)
    if exposure is not None:
        grants, accepted, refused = exposure.filter_group_grants(
            workspace,
            grants,
            labels=exposure_labels(principals, runtime_application_id=runtime_application_id),
        )
        notes.extend(accepted)
        refusals.extend(refused)
    verifier_grants = verifier_genie_grants(
        acls, verifier_application_id=verifier_application_id
    )
    folders = shared_folder_names(workspace, [*grants, *verifier_grants])
    if grants:
        refusals.append(
            PrestopRefusal(
                GENIE_GROUP_GRANT,
                "the global Genie audits run after step 0a stops the App and refuse these "
                "group grants: "
                + "; ".join(grant.describe(folders) for grant in grants)
                + ". "
                + genie_group_remedy(grants),
            )
        )
    if verifier_grants:
        refusals.append(
            PrestopRefusal(
                VERIFIER_GENIE_GRANT,
                "the verifier's global Genie audit (--forbid-all-genie) runs after step 0a "
                "and refuses every Genie grant to the verifier: "
                + "; ".join(grant.describe(folders) for grant in verifier_grants)
                + ". "
                + verifier_genie_remedy(verifier_grants),
            )
        )
    return BaseVerdict(refusals, notes, agents, acls, principals, effective)


def run_probes(
    workspace: Any,
    *,
    app_name: str,
    runtime_application_id: str,
    verifier_application_id: str,
    canonical_name: str,
    acknowledgement: str,
    list_agents: Callable[[], list[dict[str, Any]]],
    resolve_groups: Callable[..., Mapping[str, str]],
) -> tuple[list[str], list[str]]:
    """Run the Supervisor and Genie-grant clauses and return every refusal at once."""

    verdict = base_verdict(
        workspace,
        app_name=app_name,
        runtime_application_id=runtime_application_id,
        verifier_application_id=verifier_application_id,
        canonical_name=canonical_name,
        acknowledgement=acknowledgement,
        list_agents=list_agents,
        resolve_groups=resolve_groups,
    )
    return [refusal.message for refusal in verdict.refusals], verdict.notes


def all_refusals(
    workspace: Any,
    *,
    config: ProbeSuiteConfig,
    runtime_application_id: str,
    verifier_application_id: str,
    acknowledgement: str,
    list_agents: Callable[[], list[dict[str, Any]]],
    resolve_groups: Callable[..., Mapping[str, str]],
    load_exposure: Callable[..., tuple[PrestopExposure | None, list[PrestopRefusal]]] = (
        load_prestop_exposure
    ),
) -> tuple[list[PrestopRefusal], list[str]]:
    """Every probe, in one read-only pass; a probe that cannot finish refuses."""

    exposure, exposure_refusals = load_exposure(
        verifier_application_id=verifier_application_id,
        governed_title=config.genie_space_name,
    )
    try:
        base = base_verdict(
            workspace,
            app_name=config.app_name,
            runtime_application_id=runtime_application_id,
            verifier_application_id=verifier_application_id,
            canonical_name=config.canonical_name,
            acknowledgement=acknowledgement,
            list_agents=list_agents,
            resolve_groups=resolve_groups,
            exposure=exposure,
        )
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - an unfinished read never passes
        return [*exposure_refusals, _probe_error(exc)], []
    refusals, notes = [*exposure_refusals, *base.refusals], list(base.notes)
    try:
        found, said = suite_refusals(
            workspace,
            config=config,
            app_client_id=base.principals.app_client_id,
            app_scim_id=base.principals.app_scim_id,
            app_groups=base.app_groups,
            agents=base.agents,
            acls=base.acls,
        )
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - keep what the clauses found
        return [*refusals, _probe_error(exc)], notes
    return [*refusals, *found], [*notes, *said]


def _probe_error(exc: BaseException) -> PrestopRefusal:
    return PrestopRefusal(
        PROBE_ERROR,
        f"a pre-stop probe could not complete its reads: {type(exc).__name__}: {exc}. "
        "The probes refuse rather than pass what they could not read; fix the read "
        "(identity, permissions or network), then re-run.",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--expected-inventory-principal", required=True)
    parser.add_argument("--runtime-application-id", required=True)
    parser.add_argument("--verifier-application-id", required=True)
    parser.add_argument("--supervisor-name", required=True)
    parser.add_argument("--rollback-scope", required=True)
    parser.add_argument("--lakebase-instance", required=True)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--genie-space-name", required=True)
    parser.add_argument("--rebase-unverified-app", choices=("0", "1"), required=True)
    args = parser.parse_args(argv)

    from databricks.sdk import WorkspaceClient
    from tools.databricks.audit_global_m2m_access import (
        assert_workspace_admin_inventory_identity,
    )
    from tools.databricks.m2m_access_policy import resolve_effective_groups
    from tools.databricks.provision_agentic_resources import _supervisor_agents
    from tools.databricks.uc_owner_policy import metastore_owner_from_env

    workspace = WorkspaceClient()
    assert_workspace_admin_inventory_identity(
        workspace, expected_principal=args.expected_inventory_principal
    )
    refusals, notes = all_refusals(
        workspace,
        config=ProbeSuiteConfig(
            app_name=args.app_name,
            rollback_scope=args.rollback_scope,
            lakebase_instance=args.lakebase_instance,
            catalog=args.catalog,
            genie_space_name=args.genie_space_name,
            canonical_name=args.supervisor_name,
            rebase_unverified_app=args.rebase_unverified_app == "1",
            deployer=args.expected_inventory_principal,
            # Read from the environment like the post-stop converger, never from argv:
            # both lists name principals, and deploy.sh echoes each command it runs.
            approved_owner_principals=os.environ.get(APPROVED_OWNERS_ENV, ""),
            genie_manager_principals=os.environ.get(GENIE_MANAGERS_ENV, ""),
            metastore_owner=metastore_owner_from_env(),
        ),
        runtime_application_id=args.runtime_application_id,
        verifier_application_id=args.verifier_application_id,
        acknowledgement=supervisor_retirement_acknowledgement(),
        list_agents=_supervisor_agents,
        resolve_groups=resolve_effective_groups,
    )
    for note in notes:
        print(f"[mip-prestop] {note}")
    if refusals:
        for refusal in refusals:
            print(f"[mip-prestop] REFUSED: {refusal.line()}", file=sys.stderr)
        print(
            "[mip-prestop] nothing was changed and the App was not stopped by this run.",
            file=sys.stderr,
        )
        return 2
    print("[mip-prestop] pre-stop probes passed (read-only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
