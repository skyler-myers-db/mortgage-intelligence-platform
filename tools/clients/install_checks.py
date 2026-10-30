"""Read-only install checks: this deployer's install, its source share and its storage (M1-PR3).

``tools.clients.live_checks`` runs these once the host, the workspace id, the
metastore id and the deployer hold (plan §4.5, checks 3-5 and 10), still
before the signed lease and any App stop. Every call is a GET or a list.
Every refusal is collected, none short-circuits the rest, and each names the
check and the profile field, never a value.

Fail closed: only a typed not-found (``NotFound``, ``ResourceDoesNotExist``)
reads as absent. Any other read error refuses that check, never "absent".

1. **adoption.** The App ``MIP_APP_NAME`` exists, but this deployer's bundle
   state (tools/databricks/bundle_state.py) does not bind ``apps.mip_app`` to
   it: an install this deployer never adopted (adopt it first, plan M2-PR6).
   When a first-install journal file exists for that App, an interrupted
   first install may own it: the check defers to the pre-lease bundle plan
   gate, which verifies the signed journal and refuses ``not_adopted``
   otherwise, still before the lease.
2. **install marker.** The catalog ``MIP_DEFAULT_CATALOG`` carries the
   property ``mip.install_key``, which the governed deploy stamps in the very
   call that creates the catalog (tools/databricks/ensure_pipeline_namespace.py),
   so a first install interrupted anywhere after that create finds its own
   catalog marked. Present, it must equal ``MIP_CLIENT``. Absent on an
   existing catalog, this install's deploy did not create it: it is accepted
   only when this deployer's state binds the App (an install it already
   holds, from before the marker); otherwise it is another install's catalog
   or one this deployer never adopted, which a ``CREATE CATALOG IF NOT
   EXISTS`` would silently reuse. No catalog is a first install.
3. **Cotality share.** The source catalog (``MIP_COTALITY_SOURCE_*``, the
   canonical names when unset; backend/config/source_names.py) is a Delta
   Sharing catalog, mounted from ``MIP_COTALITY_SHARE_PROVIDER`` and
   ``MIP_COTALITY_SHARE_NAME`` when those are set, and each of its seven
   tables resolves.
4. **storage root.** A first install creates the catalog: the metastore must
   have a default storage root, or ``MIP_UC_CATALOG_STORAGE_ROOT`` must be
   set (the catalog is then created with that ``MANAGED LOCATION``). When
   the catalog exists and ``MIP_UC_CATALOG_STORAGE_ROOT`` is set, it must be
   the catalog's own storage root (a trailing ``/`` aside): a catalog's
   managed location never moves, so a profile naming another one is wrong.
5. **account principal.** ``MIP_IDENTITY_IDS_JSON.account_principal`` does not
   hold account admin (its account SCIM roles), read with its own
   credential; it may manage the reserved M2M identities only.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from databricks.sdk.errors import NotFound, ResourceDoesNotExist

from backend.config import source_names
from tools.clients.profile import account_host
from tools.clients.redact import emit_mask
from tools.clients.schema import ClientProfile

INSTALL_KEY_PROPERTY = "mip.install_key"
SHARE_CATALOG_TYPE = "DELTASHARING_CATALOG"
ACCOUNT_ADMIN_ROLE = "account_admin"
_ABSENT = (NotFound, ResourceDoesNotExist)


def _text(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _read_error(check: str, exc: BaseException) -> str:
    # The class only: an SDK message can carry a host, a name or an id.
    return f"{check}: could not be read ({type(exc).__name__}); refusing rather than reading it as absent"


def _get_or_none(call: Callable[..., Any], *args: Any) -> Any | None:
    try:
        return call(*args)
    except _ABSENT:
        return None


class InstallChecks:
    """One run of the install checks against one deployer workspace client."""

    def __init__(self, profile: ClientProfile, workspace: Any, account: Callable[[], Any]) -> None:
        self.profile = profile
        self.workspace = workspace
        self.account = account
        self.lines: list[str] = []
        self.app_bound = False
        self.install_catalog: Any = None

    def run(self) -> list[str]:
        self.adoption()
        catalog_exists = self.install_marker()
        self.cotality_share()
        if catalog_exists is False:
            self.storage_root()
        elif catalog_exists:
            self.existing_storage_root()
        self.account_principal()
        return self.lines

    # --- 1. adoption ---------------------------------------------------------

    def adoption(self) -> None:
        from tools.databricks.bundle_state import APP_KEY, bound_id, read_state, state_path

        name = self.profile.app_name
        try:
            app = _get_or_none(self.workspace.apps.get, name)
            document = read_state(
                self.workspace, state_path(deployer=self.profile.identity_ids.deployer, client_key=self.profile.client)
            )
            bound = bound_id(document, APP_KEY)
        except Exception as exc:  # noqa: BLE001 - every read error refuses the check
            self.lines.append(_read_error("adoption", exc))
            return
        self.app_bound = app is not None and bound == name
        if app is None or self.app_bound:
            return
        try:
            journaled = self._first_install_journal_exists(name)
        except Exception as exc:  # noqa: BLE001
            self.lines.append(_read_error("adoption", exc))
            return
        if not journaled:
            self.lines.append(
                "adoption: the App MIP_APP_NAME names exists, but this deployer's bundle state does not "
                "bind apps.mip_app to it: an existing install this deployer never adopted (adopt it into "
                "this deployer's state first, plan M2-PR6)"
            )

    def _first_install_journal_exists(self, app_name: str) -> bool:
        from tools.databricks.app_first_install_journal import _path as journal_path

        try:
            self.workspace.workspace.get_status(journal_path(app_name))
        except _ABSENT:
            return False
        return True

    # --- 2. install marker ---------------------------------------------------

    def install_marker(self) -> bool | None:
        """Whether the install catalog exists; ``None`` when it could not be read."""

        try:
            catalog = _get_or_none(self.workspace.catalogs.get, self.profile.default_catalog)
        except Exception as exc:  # noqa: BLE001
            self.lines.append(_read_error("install marker", exc))
            return None
        if catalog is None:
            return False
        self.install_catalog = catalog
        properties = getattr(catalog, "properties", None) or {}
        marker = str(properties.get(INSTALL_KEY_PROPERTY) or "").strip()
        if marker and marker != self.profile.client:
            self.lines.append(
                f"install marker: MIP_DEFAULT_CATALOG carries {INSTALL_KEY_PROPERTY} for another install "
                "than MIP_CLIENT; refusing to deploy into another install's catalog"
            )
        elif not marker and not self.app_bound:
            self.lines.append(
                f"install marker: MIP_DEFAULT_CATALOG exists without {INSTALL_KEY_PROPERTY}, and this "
                "deployer's state binds no App: a catalog this install's deploy did not create, which "
                "CREATE CATALOG IF NOT EXISTS would silently reuse (an install from before the marker: "
                "adopt it first, plan M2-PR6; any other catalog: set another MIP_DEFAULT_CATALOG)"
            )
        return True

    # --- 3. Cotality share ---------------------------------------------------

    def cotality_share(self) -> None:
        profile = self.profile
        source = source_names.resolve(
            catalog=profile.cotality_source_catalog,
            schema=profile.cotality_source_schema,
            table_prefix=profile.cotality_source_table_prefix,
        )
        try:
            catalog = _get_or_none(self.workspace.catalogs.get, source.catalog)
        except Exception as exc:  # noqa: BLE001
            self.lines.append(_read_error("Cotality share", exc))
            return
        if catalog is None:
            self.lines.append("Cotality share: the source catalog MIP_COTALITY_SOURCE_CATALOG names is absent")
            return
        if _text(getattr(catalog, "catalog_type", None)) != SHARE_CATALOG_TYPE:
            self.lines.append(
                f"Cotality share: the source catalog is not a Delta Sharing catalog ({SHARE_CATALOG_TYPE})"
            )
        for field, attribute, expected in (
            ("MIP_COTALITY_SHARE_PROVIDER", "provider_name", profile.cotality_share_provider),
            ("MIP_COTALITY_SHARE_NAME", "share_name", profile.cotality_share_name),
        ):
            if expected is not None and _text(getattr(catalog, attribute, None)) != expected:
                self.lines.append(f"Cotality share: the source catalog is not mounted from {field}")
        missing = []
        for stem in source_names.TABLE_STEMS:
            try:
                exists = getattr(self.workspace.tables.exists(source.table(stem)), "table_exists", None)
            except _ABSENT:
                exists = False
            except Exception as exc:  # noqa: BLE001
                self.lines.append(_read_error(f"Cotality share table {stem}", exc))
                continue
            if exists is not True:
                missing.append(stem)
        if missing:
            self.lines.append(
                f"Cotality share: {len(missing)} of {len(source_names.TABLE_STEMS)} source tables do not "
                f"resolve: {', '.join(missing)}"
            )

    # --- 4. storage root -----------------------------------------------------

    def storage_root(self) -> None:
        if self.profile.catalog_storage_root is not None:
            return
        try:
            summary = self.workspace.metastores.summary()
        except Exception as exc:  # noqa: BLE001
            self.lines.append(_read_error("storage root", exc))
            return
        if not _text(getattr(summary, "storage_root", None)):
            self.lines.append(
                "storage root: the metastore has no default storage root and MIP_UC_CATALOG_STORAGE_ROOT "
                "is unset, so the first install could not create MIP_DEFAULT_CATALOG; set it"
            )

    def existing_storage_root(self) -> None:
        expected = self.profile.catalog_storage_root
        if expected is None:
            return
        actual = _text(getattr(self.install_catalog, "storage_root", None))
        if actual.rstrip("/") != expected.rstrip("/"):
            self.lines.append(
                "storage root: MIP_DEFAULT_CATALOG exists with another storage root than "
                "MIP_UC_CATALOG_STORAGE_ROOT; a catalog's managed location never moves, so set the "
                "catalog's own root or unset the field"
            )

    # --- 5. account principal ------------------------------------------------

    def account_principal(self) -> None:
        application_id = self.profile.identity_ids.account_principal
        try:
            listed = list(
                self.account().service_principals.list(
                    filter=f"applicationId eq '{application_id}'", attributes="id,applicationId,roles"
                )
            )
        except Exception as exc:  # noqa: BLE001
            self.lines.append(_read_error("account principal", exc))
            return
        matches = [
            item
            for item in listed
            if _text(getattr(item, "application_id", None)).casefold() == application_id.casefold()
        ]
        if len(matches) != 1:
            self.lines.append(
                "MIP_IDENTITY_IDS_JSON.account_principal: no single account service principal has that "
                "application id"
            )
            return
        emit_mask(_text(getattr(matches[0], "id", None)))
        roles = {_text(getattr(role, "value", None)) for role in getattr(matches[0], "roles", None) or ()}
        if ACCOUNT_ADMIN_ROLE in roles:
            self.lines.append(
                "MIP_IDENTITY_IDS_JSON.account_principal: holds account admin; it may manage the reserved "
                "M2M identities only (remove the role in the account console)"
            )


def account_client(profile: ClientProfile) -> Any:
    """An account client authenticated as the profile's account principal."""

    from databricks.sdk import AccountClient

    client = AccountClient(
        host=account_host(profile),
        account_id=profile.account_id,
        client_id=profile.identity_ids.account_principal,
        client_secret=profile.account_client_secret.get_secret_value(),
        auth_type="oauth-m2m",
    )
    # A stalled account-API response must not wedge the preflight (as in
    # tools/databricks/uc_owner_policy.py).
    config = getattr(client, "config", None)
    if config is not None:
        config.http_timeout_seconds = 120
    return client


def check(profile: ClientProfile, workspace: Any, account: Callable[[], Any] | None = None) -> list[str]:
    """Every install refusal; empty means the install checks hold."""

    return InstallChecks(profile, workspace, account or (lambda: account_client(profile))).run()
