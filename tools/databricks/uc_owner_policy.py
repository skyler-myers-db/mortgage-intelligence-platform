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

    raw_values = {
        "host": os.environ.get("DATABRICKS_ACCOUNT_HOST", ""),
        "account_id": os.environ.get("DATABRICKS_ACCOUNT_ID", ""),
        "client_id": os.environ.get("DATABRICKS_ACCOUNT_CLIENT_ID", ""),
        "client_secret": os.environ.get("DATABRICKS_ACCOUNT_CLIENT_SECRET", ""),
    }
    missing = sorted(name for name, value in raw_values.items() if not value)
    if missing:
        raise RuntimeError(
            "Approved UC owners require dedicated account OAuth configuration: "
            + ", ".join(missing)
        )
    if any(value != value.strip() for value in raw_values.values()):
        raise RuntimeError("Dedicated account OAuth configuration is not canonical")
    values = raw_values
    return AccountClient(
        host=values["host"],
        account_id=values["account_id"],
        client_id=values["client_id"],
        client_secret=values["client_secret"],
        auth_type="oauth-m2m",
        # Without a client-side timeout, one stalled account-API response
        # wedges the deploy inside PySSL_select indefinitely — observed twice
        # on 2026-08-09, ~60 minutes each, stack-sampled both times inside the
        # step-4 identity probe's credential mint. Same idiom as the Lakebase
        # bootstrap account client (_SDK_HTTP_TIMEOUT_SECONDS).
        http_timeout_seconds=120,
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

    if not application_id or application_id != application_id.strip():
        raise RuntimeError("Target App application id is not canonical")
    escaped = _escaped_filter(application_id)
    principals: list[object] = []
    for item in account.service_principals.list(
        filter=f'applicationId eq "{escaped}"'
    ):
        raw_application_id = getattr(item, "application_id", None)
        if (
            not isinstance(raw_application_id, str)
            or not raw_application_id
            or raw_application_id != raw_application_id.strip()
        ):
            raise RuntimeError(
                "Target App account identity returned a noncanonical application id"
            )
        if (
            raw_application_id != application_id
            and raw_application_id.casefold() == application_id.casefold()
        ):
            raise RuntimeError(
                "Target App account identity returned a case-variant application id"
            )
        if raw_application_id == application_id:
            principals.append(item)
    if len(principals) != 1:
        raise RuntimeError("Target App identity did not resolve exactly once in account SCIM")
    principal = principals[0]
    if getattr(principal, "active", None) is not True:
        raise RuntimeError("Target App account principal is inactive")
    principal_id = getattr(principal, "id", None)
    if (
        not isinstance(principal_id, str)
        or not principal_id
        or principal_id != principal_id.strip()
    ):
        raise RuntimeError("Target App account principal has no canonical immutable id")

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
    _expected_group_names: dict[str, str] = field(default_factory=dict, init=False)
    _resolved: dict[str, tuple[str, str, str]] = field(default_factory=dict, init=False)
    _current_name: str = field(default="", init=False)
    _current_id: str = field(default="", init=False)
    _group_membership_results: dict[str, bool] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        current = self.workspace.current_user.me()
        raw_current_name = getattr(current, "user_name", None)
        raw_current_id = getattr(current, "id", None)
        raw_current_application_id = getattr(current, "application_id", "") or ""
        if (
            not isinstance(raw_current_name, str)
            or not raw_current_name
            or raw_current_name != raw_current_name.strip()
            or not isinstance(raw_current_id, str)
            or not raw_current_id
            or raw_current_id != raw_current_id.strip()
            or not isinstance(raw_current_application_id, str)
            or raw_current_application_id != raw_current_application_id.strip()
        ):
            raise RuntimeError(
                "Deploying identity must expose canonical user_name and immutable id"
            )
        current_name = raw_current_name.casefold()
        current_id = raw_current_id
        current_application_id = raw_current_application_id.casefold()
        if {
            current_name,
            current_id.casefold(),
            current_application_id,
        }.intersection(self.target.aliases):
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

    @staticmethod
    def _exact(
        items: Iterable[object],
        attribute: str,
        expected: str,
    ) -> list[object]:
        matches: list[object] = []
        for item in items:
            raw = getattr(item, attribute, None)
            if not isinstance(raw, str) or not raw or raw != raw.strip():
                raise RuntimeError(
                    "Approved UC owner inventory returned a noncanonical identity"
                )
            if raw != expected and raw.casefold() == expected.casefold():
                raise RuntimeError(
                    "Approved UC owner inventory returned a case-variant identity"
                )
            if raw == expected:
                matches.append(item)
        return matches

    def _dedicated_account_client(self) -> AccountClient:
        if self._account_client is not None:
            return self._account_client
        try:
            account = self.account_factory()
        except Exception as exc:
            raise RuntimeError(
                "Dedicated account OAuth client could not be constructed"
            ) from exc
        raw_account_client_id = (
            getattr(getattr(account, "config", None), "client_id", "")
            or os.environ.get("DATABRICKS_ACCOUNT_CLIENT_ID", "")
        )
        if (
            not isinstance(raw_account_client_id, str)
            or not raw_account_client_id
            or raw_account_client_id != raw_account_client_id.strip()
        ):
            raise RuntimeError("Dedicated account OAuth client has no canonical client id")
        account_client_id = raw_account_client_id.casefold()
        separated_ids: set[str] = set()
        for name in (
            "DATABRICKS_CLIENT_ID",
            "DATABRICKS_OPERATOR2_CLIENT_ID",
            "DATABRICKS_ADMIN_CLIENT_ID",
            "DATABRICKS_RELEASE_PROBE_CLIENT_ID",
            "DATABRICKS_VERIFIER_CLIENT_ID",
            "DATABRICKS_AGENT_RUNTIME_CLIENT_ID",
        ):
            raw = os.environ.get(name, "")
            if not raw:
                continue
            if raw != raw.strip():
                raise RuntimeError(f"{name} is not canonical")
            separated_ids.add(raw.casefold())
        if account_client_id in self.target.aliases.union(separated_ids):
            raise RuntimeError(
                "Dedicated account OAuth client must be distinct from the "
                "target App and every app-facing M2M identity"
            )
        try:
            account_target_sp_id = _account_principal_id(
                account,
                application_id=self.target.application_id,
            )
        except Exception as exc:
            raise RuntimeError(
                "Target App account identity could not be resolved authoritatively"
            ) from exc
        self._account_client = account
        self._account_target_sp_id = account_target_sp_id
        return account

    @staticmethod
    def _principal_id(item: object, *, owner: str) -> str:
        raw = getattr(item, "id", None)
        if not isinstance(raw, str) or not raw or raw != raw.strip():
            raise RuntimeError(f"Approved UC owner {owner!r} has no canonical immutable id")
        return raw

    def _resolve(self, owner: str, *, query_name: str) -> tuple[str, str]:
        cached = self._resolved.get(owner)
        if cached is not None:
            cached_name, kind, principal_id = cached
            if query_name != cached_name:
                raise RuntimeError(
                    "Governed UC owner identity changed within the inventory snapshot"
                )
            return kind, principal_id
        escaped = _escaped_filter(query_name)
        workspace_users = self._exact(
            self.workspace.users.list(filter=f'userName eq "{escaped}"'),
            "user_name",
            query_name,
        )
        workspace_service_principals = self._exact(
            self.workspace.service_principals.list(filter=f'applicationId eq "{escaped}"'),
            "application_id",
            query_name,
        )
        workspace_groups = self._exact(
            self.workspace.groups.list(filter=f'displayName eq "{escaped}"'),
            "display_name",
            query_name,
        )
        workspace_candidates: list[tuple[str, object]] = [
            *(("user", item) for item in workspace_users),
            *(("service_principal", item) for item in workspace_service_principals),
            *(("group", item) for item in workspace_groups),
        ]
        if len(workspace_candidates) > 1:
            raise RuntimeError(
                f"Approved UC owner {owner!r} did not resolve to exactly one "
                "principal in workspace SCIM"
            )

        account = self._dedicated_account_client()
        try:
            account_users = self._exact(
                account.users.list(filter=f'userName eq "{escaped}"'),
                "user_name",
                query_name,
            )
            account_service_principals = self._exact(
                account.service_principals.list(
                    filter=f'applicationId eq "{escaped}"'
                ),
                "application_id",
                query_name,
            )
            account_groups = self._exact(
                account.groups.list(filter=f'displayName eq "{escaped}"'),
                "display_name",
                query_name,
            )
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(
                "Approved UC owner account inventory failed closed"
            ) from exc
        account_candidates: list[tuple[str, object]] = [
            *(("user", item) for item in account_users),
            *(("service_principal", item) for item in account_service_principals),
            *(("group", item) for item in account_groups),
        ]
        if len(account_candidates) != 1:
            raise RuntimeError(
                f"Approved UC owner {owner!r} did not resolve to exactly one "
                "principal in account SCIM"
            )
        kind, item = account_candidates[0]
        principal_id = self._principal_id(item, owner=owner)
        if kind in {"user", "service_principal"} and getattr(item, "active", None) is not True:
            raise RuntimeError(f"Approved UC owner {owner!r} is inactive in account SCIM")
        if workspace_candidates:
            workspace_kind, workspace_item = workspace_candidates[0]
            workspace_id = self._principal_id(workspace_item, owner=owner)
            if workspace_kind != kind or workspace_id != principal_id:
                raise RuntimeError(
                    f"Approved UC owner {owner!r} did not resolve identically "
                    "across workspace and account SCIM"
                )
        if owner == self._current_name and principal_id != self._current_id:
            raise RuntimeError(
                "Current deployer owner name resolved to a different immutable principal"
            )
        resolved = (kind, principal_id)
        self._resolved[owner] = (query_name, *resolved)
        if kind == "group":
            self._expected_group_names[principal_id] = query_name
        return resolved

    def _assert_group_excludes_target(self, *, owner: str, group_id: str) -> None:
        if not self._account_target_sp_id:
            self._dedicated_account_client()
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
            hydrated_id = getattr(account_group, "id", None)
            if (
                not isinstance(hydrated_id, str)
                or not hydrated_id
                or hydrated_id != hydrated_id.strip()
            ):
                raise RuntimeError("Account SCIM hydrated group id is not canonical")
            if hydrated_id != group_id:
                raise RuntimeError("Account SCIM hydrated group id mismatch")
            hydrated_account_name = getattr(account_group, "display_name", None)
            expected_name = self._expected_group_names.get(group_id, "")
            if (
                not isinstance(hydrated_account_name, str)
                or not hydrated_account_name
                or hydrated_account_name != hydrated_account_name.strip()
                or not expected_name
                or hydrated_account_name != expected_name
            ):
                raise RuntimeError(
                    f"Account SCIM group {group_id!r} has no exact display name"
                )
            self._account_group_names[group_id] = hydrated_account_name
            raw_account_name = hydrated_account_name
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
            if type(target_is_member) is not bool:
                raise RuntimeError(
                    "Credential-backed target identity membership proof returned "
                    f"malformed evidence for approved group owner {owner!r}"
                )
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
            owner_text = getattr(item, "owner", None)
            if (
                not isinstance(owner_text, str)
                or not owner_text
                or owner_text != owner_text.strip()
            ):
                raise RuntimeError(
                    "Governed UC object inventory returned a noncanonical owner"
                )
            owner = _canonical(owner_text)
            if owner in self.target.aliases:
                raise RuntimeError("Target App service principal cannot own governed UC objects")
            if not owner or owner not in self.configured_principals:
                raise RuntimeError(
                    "Governed UC owner is outside the explicit approved-owner contract"
                )
            kind, principal_id = self._resolve(owner, query_name=owner_text)
            if _canonical(principal_id) in self.target.aliases or (
                self._account_target_sp_id
                and principal_id == self._account_target_sp_id
            ):
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
