"""Execute an acked bootstrap plan: the only module that mutates.

Every call here is additive, with one reviewed exception:

* create a service principal, a federation policy (or move the one policy
  an adopted identity already has to the client's subject), a workspace
  assignment, a group, a group member, a rule-set grant or a UC grant;
* ``--rotate-adopted`` alone deletes: the secrets its plan listed for a
  pinned M2M identity before it minted the new one, or the new one itself
  when its handoff fails. The agent proxy's older credentials are never
  deleted here: the signed deploy retires them.

Never: a metastore or securable ownership transfer, a UC revoke, a SCIM
``remove`` or ``replace``, a rule-set update that drops a grant, a change to
an identity's roles (so never account admin), or a delete of anything else.
tests/unit/test_bootstrap_account_safety.py pins this with a call spy and an
AST allowlist.

A failure stops the run at that step and raises ``StepFailure``, whose text
names the step and the exception class only: an SDK or sink message could
carry an identifier or, after a mint, the credential itself. A re-run plans
only what is left, except that a service principal created before its pin
reached the sink is refused by name until ``--adopt`` names it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, NoReturn

from databricks.sdk.errors import NotFound
from databricks.sdk.service.catalog import PermissionsChange, Privilege
from databricks.sdk.service.iam import (
    ComplexValue,
    GrantRule,
    Patch,
    PatchOp,
    PatchSchema,
    RuleSetUpdateRequest,
    WorkspacePermission,
)
from databricks.sdk.service.oauth2 import FederationPolicy, OidcFederationPolicy
from pydantic import SecretStr

from tools.clients.bootstrap_observe import (
    SP_MANAGER,
    SP_USER,
    Observation,
    Principal,
    rule_set_name,
    sp_reference,
    text,
)
from tools.clients.bootstrap_plan import Plan, Step
from tools.clients.bootstrap_roster import GITHUB_OIDC_ISSUER, RoleSpec
from tools.clients.credential_sink import CredentialProbe, CredentialSink
from tools.clients.errors import BootstrapRefusal
from tools.databricks.agent_proxy_credential_bundle import canonical_agent_proxy_credential_bundle

_SP_ROLE = {"user": SP_USER, "manager": SP_MANAGER}
PROBE_ATTEMPTS = 6
PROBE_INTERVAL_SECONDS = 5.0
REVOKE_READS = 6


class StepFailure(RuntimeError):
    """A mutation failed; the message is value-free."""


def field_value(spec: RoleSpec, application_id: str, credential_id: str, secret: SecretStr) -> SecretStr:
    """What the sink stores for ``spec``'s field: the secret, or the proxy's bundle."""

    if not spec.bundled:
        return secret
    return SecretStr(
        canonical_agent_proxy_credential_bundle(
            client_id=application_id, credential_id=credential_id, client_secret=secret.get_secret_value()
        )
    )


class Executor:
    """Runs one plan's steps in order against the session's clients."""

    def __init__(
        self,
        clients: Any,
        obs: Observation,
        sink: CredentialSink,
        *,
        probe: CredentialProbe | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.account = clients.account
        self.workspace = clients.workspace
        self.obs = obs
        self.inputs = obs.request.inputs
        self.sink = sink
        self.probe = probe
        self.sleep = sleep
        self.principals: dict[str, Principal] = {
            role: state.principal for role, state in obs.roles.items() if state.principal is not None
        }
        self.pins: dict[str, str] = dict(obs.request.pins)
        self.group_id = obs.group.group_id
        self.mutations = 0

    # --- driver -------------------------------------------------------------

    def run(self, plan: Plan) -> int:
        if plan.needs_sink:
            self.sink.ready()
        handlers: dict[str, Callable[[Step], None]] = {
            "create_service_principal": self._create_service_principal,
            "pin_service_principal": self._pin_service_principal,
            "set_federation_policy": self._set_federation_policy,
            "assign_workspace": self._assign_workspace,
            "grant_sp_role": lambda item: self._grant_sp_role(item, plan),
            "create_group": self._create_group,
            "add_group_member": self._add_group_member,
            "grant_uc": self._grant_uc,
            "rotate_secret": self._rotate_secret,
        }
        for index, item in enumerate(plan.steps, start=1):
            try:
                handlers[item.op](item)
            except BootstrapRefusal:
                raise
            except Exception as exc:  # the class only: the message could carry a value
                raise StepFailure(
                    f"step {index} ({item.op}) failed: {type(exc).__name__}; "
                    f"{self.mutations} mutation(s) applied before it. Re-run --plan. If it refuses a "
                    "service principal this run created, confirm it and name it with --adopt "
                    "<role>=<application id> (docs/clients/bootstrap.md)."
                ) from None
        return self.mutations

    def _mutated(self) -> None:
        self.mutations += 1

    def _principal(self, role: str) -> Principal:
        principal = self.principals.get(role)
        if principal is None:
            raise BootstrapRefusal([f"{role}: no identity is resolved for this step"])
        return principal

    # --- identities -----------------------------------------------------------

    def _create_service_principal(self, item: Step) -> None:
        role, name = item.get("role"), item.get("name")
        record = self.account.service_principals.create(display_name=name, active=True)
        self._mutated()
        principal = Principal(
            scim_id=text(getattr(record, "id", None)),
            application_id=text(getattr(record, "application_id", None)).lower(),
            display_name=text(getattr(record, "display_name", None)),
        )
        if not principal.scim_id or not principal.application_id or principal.display_name != name:
            raise BootstrapRefusal([f"{role}: the created service principal came back incomplete"])
        self.principals[role] = principal
        self.pins[role] = principal.application_id
        self.sink.put_identity_ids(dict(self.pins))

    def _pin_service_principal(self, item: Step) -> None:
        self._principal(item.get("role"))
        self.sink.put_identity_ids(dict(self.pins))

    def _set_federation_policy(self, item: Step) -> None:
        role = item.get("role")
        principal = self._principal(role)
        policy = FederationPolicy(
            oidc_policy=OidcFederationPolicy(
                issuer=GITHUB_OIDC_ISSUER,
                subject=item.get("subject"),
                audiences=[self.obs.audience],
            )
        )
        if item.get("change") == "create":
            self.account.service_principal_federation_policy.create(int(principal.scim_id), policy=policy)
        else:
            existing = self.obs.roles[role].policies
            if len(existing) != 1:
                raise BootstrapRefusal([f"{role}: a policy update needs exactly one existing policy"])
            self.account.service_principal_federation_policy.update(
                int(principal.scim_id), existing[0].policy_id, policy=policy, update_mask="oidc_policy"
            )
        self._mutated()

    def _assign_workspace(self, item: Step) -> None:
        principal = self._principal(item.get("role"))
        self.account.workspace_assignment.update(
            int(self.inputs.workspace_id),
            int(principal.scim_id),
            permissions=[WorkspacePermission(item.get("permission"))],
        )
        self._mutated()

    def _grant_sp_role(self, item: Step, plan: Plan) -> None:
        target = item.get("target")
        if target == "app":
            target_application_id = str(plan.facts["app_application_id"])
        else:
            target_application_id = self._principal(target).application_id
        grantee = sp_reference(self._principal(item.get("grantee")).application_id)
        role_name = _SP_ROLE[item.get("sp_role")]
        name = rule_set_name(self.inputs.account_id, target_application_id)
        current = self.account.access_control.get_rule_set(name, etag="")
        rules = [
            GrantRule(role=text(rule.role), principals=list(rule.principals or ()))
            for rule in getattr(current, "grant_rules", None) or ()
        ]
        for rule in rules:
            if rule.role == role_name:
                if grantee in (rule.principals or ()):
                    return
                rule.principals = [*(rule.principals or ()), grantee]
                break
        else:
            rules.append(GrantRule(role=role_name, principals=[grantee]))
        self.account.access_control.update_rule_set(
            name,
            rule_set=RuleSetUpdateRequest(name=name, etag=text(getattr(current, "etag", None)), grant_rules=rules),
        )
        self._mutated()

    # --- group and share --------------------------------------------------------

    def _member_id(self, member: str) -> str:
        member_id = self.obs.interim_user_id if member == "interim" else self._principal(member).scim_id
        if not member_id:
            raise BootstrapRefusal(["data_owners: a member is not resolved"])
        return member_id

    def _create_group(self, item: Step) -> None:
        """Create the group holding its members in one call.

        So an interrupted run never leaves an empty group, which the next
        plan could not tell from someone else's.
        """

        members = [ComplexValue(value=self._member_id(member)) for member in item.get("members").split(",")]
        record = self.account.groups.create(display_name=item.get("group"), members=members)
        self._mutated()
        self.group_id = text(getattr(record, "id", None))
        if not self.group_id:
            raise BootstrapRefusal(["data_owners: the created group came back without an id"])

    def _add_group_member(self, item: Step) -> None:
        member_id = self._member_id(item.get("member"))
        if not self.group_id:
            raise BootstrapRefusal(["data_owners: the group is not resolved"])
        self.account.groups.patch(
            self.group_id,
            operations=[Patch(op=PatchOp.ADD, path="members", value=[{"value": member_id}])],
            schemas=[PatchSchema.URN_IETF_PARAMS_SCIM_API_MESSAGES_2_0_PATCH_OP],
        )
        self._mutated()

    def _grant_uc(self, item: Step) -> None:
        self.workspace.grants.update(
            item.get("securable_type"),
            item.get("securable"),
            changes=[
                PermissionsChange(
                    principal=item.get("principal"),
                    add=[Privilege(name) for name in item.get("privileges").split(",")],
                )
            ],
        )
        self._mutated()

    # --- rotation ---------------------------------------------------------------

    def _probe(self, application_id: str, secret: SecretStr) -> bool:
        assert self.probe is not None
        for attempt in range(PROBE_ATTEMPTS):
            if self.probe(application_id, secret):
                return True
            if attempt + 1 < PROBE_ATTEMPTS:
                self.sleep(PROBE_INTERVAL_SECONDS)
        return False

    def _secret_ids(self, principal: Principal) -> set[str]:
        return {text(entry.id) for entry in self.account.service_principal_secrets.list(principal.scim_id)}

    def _hand_off(
        self, spec: RoleSpec, principal: Principal, field_name: str, new_id: str, secret: SecretStr
    ) -> str | None:
        """Sink, confirm and prove the new secret; ``None``, or why it failed (value-free)."""

        if not secret.get_secret_value():
            return "the minted credential came back without a secret"
        try:
            value = field_value(spec, principal.application_id, new_id, secret)
        except ValueError:
            return "the minted credential cannot form its field's value"
        try:
            self.sink.put_secret(field_name, principal.application_id, value)
            stored = self.sink.confirm_secret(field_name)
        except BootstrapRefusal as refusal:
            return "; ".join(refusal.lines)
        except Exception as exc:  # the class only: the message could echo the value
            return f"the credential sink failed ({type(exc).__name__})"
        if not stored:
            return f"the sink did not confirm {field_name}"
        if not self._probe(principal.application_id, secret):
            return "the new credential did not authenticate"
        return None

    def _revoke_minted(self, role: str, principal: Principal, new_id: str, problem: str) -> NoReturn:
        """Revoke the new secret no sink took, re-read until it is gone, then refuse.

        The repository's rotation practice (docs/security/m2m-oauth-setup.md):
        a credential minted for a failed handoff never outlives the run.
        """

        gone = False
        try:
            try:
                self.account.service_principal_secrets.delete(principal.scim_id, new_id)
                self._mutated()
            except NotFound:
                pass
            for attempt in range(REVOKE_READS):
                if new_id not in self._secret_ids(principal):
                    gone = True
                    break
                if attempt + 1 < REVOKE_READS:
                    self.sleep(PROBE_INTERVAL_SECONDS)
        except Exception:  # noqa: BLE001 - the outcome below is the whole answer
            gone = False
        if gone:
            outcome = (
                "the new credential was revoked and older secrets kept; the sink may hold the revoked "
                "value, so plan and rotate again"
            )
        else:
            outcome = (
                "the new credential could not be proven revoked: revoke this identity's newest secret "
                "in the account console, then run --verify; older secrets kept"
            )
        raise BootstrapRefusal([f"{role}: {problem}; {outcome}"])

    def _rotate_secret(self, item: Step) -> None:
        """Mint, hand to the sink, confirm, prove it authenticates, then revoke the older ones.

        Only an ``immediate`` identity's older secrets are revoked; the agent
        proxy's stay for the signed deploy's blue/green retirement journal. A
        new secret whose handoff fails is revoked before the refusal.
        """

        role, field_name = item.get("role"), item.get("field")
        state = self.obs.roles[role]
        principal = self._principal(role)
        older = state.secret_ids if state.spec.retirement == "immediate" else ()
        if self.probe is None:
            raise BootstrapRefusal([f"{role}: no credential probe is configured"])
        created = self.account.service_principal_secrets.create(principal.scim_id)
        self._mutated()
        new_id = text(getattr(created, "id", None))
        secret = SecretStr(text(getattr(created, "secret", None)))
        del created
        if not new_id or new_id in state.secret_ids:
            # Nothing names the new secret, so nothing here may revoke it.
            raise BootstrapRefusal(
                [
                    f"{role}: the minted credential came back without a new id; older secrets kept. "
                    "Revoke this identity's newest secret in the account console, then run --verify"
                ]
            )
        problem = self._hand_off(state.spec, principal, field_name, new_id, secret)
        if problem is not None:
            self._revoke_minted(role, principal, new_id, problem)
        for secret_id in older:
            self.account.service_principal_secrets.delete(principal.scim_id, secret_id)
            self._mutated()
        remaining = self._secret_ids(principal)
        if remaining & set(older):
            raise BootstrapRefusal([f"{role}: an older secret is still listed; re-run --verify"])
        if new_id not in remaining:
            raise BootstrapRefusal([f"{role}: the new credential is not listed after rotating; re-run --verify"])
