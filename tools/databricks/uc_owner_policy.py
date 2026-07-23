"""Authoritative trusted-owner policy for governed Unity Catalog objects."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from databricks.sdk import AccountClient, WorkspaceClient


def _canonical(value: object) -> str:
    return str(value or "").strip().casefold()


def _escaped_filter(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def account_client_from_env() -> AccountClient:
    """Build account SCIM auth without inheriting workspace PAT configuration."""

    values = {
        "host": os.environ.get("DATABRICKS_ACCOUNT_HOST", "").strip(),
        "account_id": os.environ.get("DATABRICKS_ACCOUNT_ID", "").strip(),
        "client_id": os.environ.get("DATABRICKS_ACCOUNT_CLIENT_ID", "").strip(),
        "client_secret": os.environ.get("DATABRICKS_ACCOUNT_CLIENT_SECRET", "").strip(),
    }
    missing = sorted(name for name, value in values.items() if not value)
    if missing:
        raise RuntimeError(
            "Approved group owners require dedicated account OAuth configuration: "
            + ", ".join(missing)
        )
    return AccountClient(
        host=values["host"],
        account_id=values["account_id"],
        client_id=values["client_id"],
        client_secret=values["client_secret"],
        auth_type="oauth-m2m",
    )


@dataclass(frozen=True)
class TargetServicePrincipal:
    application_id: str
    scim_id: str
    display_name: str = ""
    additional_aliases: frozenset[str] = field(default_factory=frozenset)

    @property
    def aliases(self) -> set[str]:
        return {
            value
            for value in (
                _canonical(self.application_id),
                _canonical(self.scim_id),
                _canonical(self.display_name),
                *(_canonical(alias) for alias in self.additional_aliases),
            )
            if value
        }


def _account_principal_id(account: AccountClient, *, application_id: str) -> str:
    """Resolve the immutable target id without inferring group membership.

    Automatic Identity Management can omit effective members from SCIM group
    resources. Membership is therefore proven separately under the target
    service principal's own credentials.
    """

    escaped = _escaped_filter(application_id)
    principals = [
        item
        for item in account.service_principals.list(filter=f'applicationId eq "{escaped}"')
        if _canonical(getattr(item, "application_id", "")) == _canonical(application_id)
    ]
    if len(principals) != 1:
        raise RuntimeError("Target App identity did not resolve exactly once in account SCIM")
    principal_id = str(getattr(principals[0], "id", "") or "").strip()
    if not principal_id:
        raise RuntimeError("Target App account principal has no immutable id")

    return principal_id


@dataclass
class ApprovedOwnerPolicy:
    """Resolve configured owner names and exclude the target App identity."""

    workspace: WorkspaceClient
    target: TargetServicePrincipal
    configured_principals: set[str] = field(default_factory=set)
    account_factory: Callable[[], AccountClient] = account_client_from_env
    group_membership_probe: Callable[[AccountClient, str, str, str, str], bool] | None = None
    _account_client: AccountClient | None = field(default=None, init=False)
    _account_target_sp_id: str = field(default="", init=False)
    _account_group_names: dict[str, str] = field(default_factory=dict, init=False)
    _resolved: dict[str, tuple[str, str]] = field(default_factory=dict, init=False)
    _current_name: str = field(default="", init=False)
    _current_id: str = field(default="", init=False)
    _group_membership_results: dict[str, bool] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        current = self.workspace.current_user.me()
        current_name = _canonical(getattr(current, "user_name", ""))
        current_id = _canonical(getattr(current, "id", ""))
        current_application_id = _canonical(getattr(current, "application_id", ""))
        if not current_name or not current_id:
            raise RuntimeError(
                "Deploying identity must expose canonical user_name and immutable id"
            )
        if {current_name, current_id, current_application_id}.intersection(self.target.aliases):
            raise RuntimeError(
                "Deploying identity must be distinct from the target App service principal"
            )
        configured = {
            _canonical(value) for value in self.configured_principals if _canonical(value)
        }
        configured.add(current_name)
        self.configured_principals = configured
        self._current_name = current_name
        self._current_id = current_id

    def _exact(self, items: Iterable[object], attribute: str, expected: str) -> list[object]:
        return [item for item in items if _canonical(getattr(item, attribute, "")) == expected]

    def _resolve(self, owner: str, *, query_name: str) -> tuple[str, str]:
        cached = self._resolved.get(owner)
        if cached is not None:
            return cached
        escaped = _escaped_filter(query_name)
        users = self._exact(
            self.workspace.users.list(filter=f'userName eq "{escaped}"'),
            "user_name",
            owner,
        )
        service_principals = self._exact(
            self.workspace.service_principals.list(filter=f'applicationId eq "{escaped}"'),
            "application_id",
            owner,
        )
        groups = self._exact(
            self.workspace.groups.list(filter=f'displayName eq "{escaped}"'),
            "display_name",
            owner,
        )
        candidates: list[tuple[str, object]] = [
            *(("user", item) for item in users),
            *(("service_principal", item) for item in service_principals),
            *(("group", item) for item in groups),
        ]
        if len(candidates) != 1:
            raise RuntimeError(
                f"Approved UC owner {owner!r} did not resolve to exactly one principal"
            )
        kind, item = candidates[0]
        principal_id = str(getattr(item, "id", "") or "").strip()
        if not principal_id:
            raise RuntimeError(f"Approved UC owner {owner!r} has no immutable id")
        if owner == self._current_name and _canonical(principal_id) != self._current_id:
            raise RuntimeError(
                "Current deployer owner name resolved to a different immutable principal"
            )
        resolved = (kind, principal_id)
        self._resolved[owner] = resolved
        return resolved

    def _assert_group_excludes_target(self, *, owner: str, group_id: str) -> None:
        if not self._account_target_sp_id:
            try:
                self._account_client = self.account_factory()
            except Exception as exc:
                raise RuntimeError(
                    "Dedicated account OAuth client could not be constructed"
                ) from exc
            account_client_id = _canonical(
                getattr(
                    getattr(self._account_client, "config", None),
                    "client_id",
                    "",
                )
                or os.environ.get("DATABRICKS_ACCOUNT_CLIENT_ID", "")
            )
            if not account_client_id:
                raise RuntimeError("Dedicated account OAuth client has no canonical client id")
            separated_ids = {
                _canonical(os.environ.get(name, ""))
                for name in (
                    "DATABRICKS_CLIENT_ID",
                    "DATABRICKS_OPERATOR2_CLIENT_ID",
                    "DATABRICKS_ADMIN_CLIENT_ID",
                    "DATABRICKS_VERIFIER_CLIENT_ID",
                )
            }
            separated_ids.discard("")
            if account_client_id in self.target.aliases.union(separated_ids):
                raise RuntimeError(
                    "Dedicated account OAuth client must be distinct from the "
                    "target App and every app-facing M2M identity"
                )
            try:
                assert self._account_client is not None
                self._account_target_sp_id = _account_principal_id(
                    self._account_client,
                    application_id=self.target.application_id,
                )
            except Exception as exc:
                raise RuntimeError(
                    "Authoritative account-level group membership is required "
                    f"for approved group owner {owner!r}"
                ) from exc
        account_sp_id = self._account_target_sp_id
        raw_account_name = self._account_group_names.get(group_id, "")
        if not raw_account_name:
            try:
                assert self._account_client is not None
                account_group = self._account_client.groups.get(group_id)
            except Exception as exc:
                raise RuntimeError(
                    f"Approved group owner {owner!r} did not resolve in account SCIM"
                ) from exc
            hydrated_id = str(getattr(account_group, "id", "") or "").strip()
            if hydrated_id != group_id:
                raise RuntimeError("Account SCIM hydrated group id mismatch")
            raw_account_name = str(getattr(account_group, "display_name", "") or "").strip()
            if not raw_account_name:
                raise RuntimeError(f"Account SCIM group {group_id!r} has no display name")
            self._account_group_names[group_id] = raw_account_name
        account_name = _canonical(raw_account_name)
        if not account_name or account_name != owner:
            raise RuntimeError(
                f"Approved group owner {owner!r} did not resolve identically in account SCIM"
            )
        if self.group_membership_probe is None or self._account_client is None:
            raise RuntimeError(
                "Credential-backed target identity membership proof is required "
                f"for approved group owner {owner!r}"
            )
        cached_membership = self._group_membership_results.get(owner)
        if cached_membership is None:
            try:
                target_is_member = self.group_membership_probe(
                    self._account_client,
                    account_sp_id,
                    self.target.application_id,
                    group_id,
                    raw_account_name,
                )
            except Exception as exc:
                raise RuntimeError(
                    "Credential-backed target identity membership proof failed "
                    f"for approved group owner {owner!r}"
                ) from exc
            self._group_membership_results[owner] = target_is_member
        else:
            target_is_member = cached_membership
        if target_is_member:
            raise RuntimeError(
                f"Target App service principal is a member of approved owner group {owner!r}"
            )

    def assert_objects(self, objects: Iterable[object | None]) -> None:
        for item in objects:
            if item is None:
                continue
            owner_text = str(getattr(item, "owner", "") or "").strip()
            owner = _canonical(owner_text)
            if owner in self.target.aliases:
                raise RuntimeError("Target App service principal cannot own governed UC objects")
            if not owner or owner not in self.configured_principals:
                raise RuntimeError(
                    "Governed UC owner is outside the explicit approved-owner contract"
                )
            kind, principal_id = self._resolve(owner, query_name=owner_text)
            if _canonical(principal_id) in self.target.aliases:
                raise RuntimeError("Target App service principal cannot own governed UC objects")
            if kind == "group":
                self._assert_group_excludes_target(owner=owner, group_id=principal_id)


def parse_approved_owner_principals(value: str) -> set[str]:
    return {part.strip() for part in value.split(",") if part.strip()}


__all__ = [
    "ApprovedOwnerPolicy",
    "TargetServicePrincipal",
    "account_client_from_env",
    "parse_approved_owner_principals",
]
