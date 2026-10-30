"""Value shapes, closed vocabulary and placeholder detection for client profiles.

Every validator here raises ``ValueError`` with a message that names the
field and the expected shape, never the value: a refusal line is printed in
public CI logs, and the value it refused may be an identifier.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

# A template value such as ``<workspace-host>``; the deploy tooling's legacy
# placeholder is the bundle default ``00000000PLACEHOLDER``.
_PLACEHOLDER = re.compile(r"<[^<>\n]+>")
LEGACY_PLACEHOLDER = "00000000PLACEHOLDER"

UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
WORKSPACE_ID = re.compile(r"[1-9][0-9]{5,18}")
ISO_DATE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
    r"(?:T[0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]+)?)?(?:Z|[+-][0-9]{2}:[0-9]{2})?)?"
)
# The deploy tooling's existing name contracts (tools/databricks/bundle_env.py,
# lakebase_instance_contract.py and the secret-scope helpers).
DNS_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
UC_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]{0,254}")
SECRET_SCOPE = re.compile(r"[A-Za-z0-9._-]{1,128}")
TITLE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9 &'._()-]{0,98}[A-Za-z0-9)])?")
GROUP_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9 _.-]{0,254}[A-Za-z0-9])?")
SHARE_OBJECT_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_. -]{0,254}[A-Za-z0-9])?")
# A Cotality source table prefix as configured: ``-`` spells "no prefix",
# since a blank value means unset (backend/config/source_names.py).
TABLE_PREFIX = re.compile(r"[a-z0-9_]{1,64}|-")
WORKSPACE_PATH = re.compile(r"/Workspace(?:/[^/\n\r\0<>]+)+")
STORAGE_ROOT = re.compile(r"(?P<scheme>s3|abfss|gs)://[^\s'\"<>]+")
PRINCIPAL = re.compile(r"[^\s,<>](?:[^,\n\r<>]{0,253}[^\s,<>])?")

# Each cloud's workspace-host suffix, account-console host and storage scheme.
# Sovereign and government clouds are deliberately absent: the profile refuses
# what it has not been reviewed against.
CLOUDS: dict[str, tuple[str, str, str]] = {
    "aws": (".cloud.databricks.com", "accounts.cloud.databricks.com", "s3"),
    "azure": (".azuredatabricks.net", "accounts.azuredatabricks.net", "abfss"),
    "gcp": (".gcp.databricks.com", "accounts.gcp.databricks.com", "gs"),
}
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
# The numeric workspace id inside an Azure or GCP workspace host.
_HOST_WORKSPACE_ID = re.compile(r"(?:adb-)?(?P<id>[0-9]{6,19})\.[0-9]{1,2}\.")

# Schema vocabulary stays readable in every log: masking it would blank the
# words a refusal line or an exposure decision is made of (attack G17).
PRINCIPAL_VOCABULARY = frozenset({"users", "account users", "admins"})
EXPOSURE_KINDS = frozenset({"uc_grant", "uc_ownership", "workspace_permission"})
SECURABLE_TYPES = frozenset(
    {
        "APP", "CATALOG", "CONNECTION", "DASHBOARD", "EXPERIMENT", "EXTERNAL_LOCATION",
        "FUNCTION", "GENIE_SPACE", "JOB", "MATERIALIZED_VIEW", "METASTORE", "MODEL",
        "PIPELINE", "PROVIDER", "RECIPIENT", "SCHEMA", "SERVICE_CREDENTIAL",
        "SERVING_ENDPOINT", "SHARE", "STORAGE_CREDENTIAL", "TABLE", "VIEW", "VOLUME",
        "WAREHOUSE",
    }
)  # fmt: skip
PRIVILEGES = frozenset(
    {
        # Unity Catalog privileges.
        "ACCESS", "ALL_PRIVILEGES", "APPLY_TAG", "BROWSE", "CREATE_CATALOG",
        "CREATE_CONNECTION", "CREATE_EXTERNAL_LOCATION", "CREATE_EXTERNAL_TABLE",
        "CREATE_EXTERNAL_VOLUME", "CREATE_FOREIGN_CATALOG", "CREATE_FOREIGN_SECURABLE",
        "CREATE_FUNCTION", "CREATE_MANAGED_STORAGE", "CREATE_MATERIALIZED_VIEW",
        "CREATE_MODEL", "CREATE_PROVIDER", "CREATE_RECIPIENT", "CREATE_SCHEMA",
        "CREATE_SERVICE_CREDENTIAL", "CREATE_SHARE", "CREATE_STORAGE_CREDENTIAL",
        "CREATE_TABLE", "CREATE_VOLUME", "EXECUTE", "EXTERNAL_USE_LOCATION",
        "EXTERNAL_USE_SCHEMA", "MANAGE", "MANAGE_ALLOWLIST", "MODIFY", "OWNERSHIP",
        "READ_FILES", "READ_VOLUME", "REFRESH", "SELECT", "SET_SHARE_PERMISSION",
        "USE_CATALOG", "USE_CONNECTION", "USE_MARKETPLACE_ASSETS", "USE_PROVIDER",
        "USE_RECIPIENT", "USE_SCHEMA", "USE_SHARE", "WRITE_FILES", "WRITE_VOLUME",
        # Workspace object permission levels (Genie spaces, jobs, endpoints, ...).
        "CAN_ATTACH_TO", "CAN_BIND", "CAN_EDIT", "CAN_MANAGE", "CAN_MANAGE_RUN",
        "CAN_QUERY", "CAN_READ", "CAN_RESTART", "CAN_RUN", "CAN_USE", "CAN_VIEW",
        "IS_OWNER",
    }
)  # fmt: skip
ISOLATION_MODES = frozenset({"OPEN", "ISOLATED"})
EXPOSURE_IDENTITIES = frozenset({"app", "runtime", "proxy"})
PRIVILEGE_CLASSES = frozenset({"read", "elevated"})
DIRECT_SOURCE = "direct"
INHERITED_SOURCE_PREFIX = "inherited:"
_VOCABULARY = frozenset(
    term.casefold()
    for term in (
        *PRINCIPAL_VOCABULARY,
        *EXPOSURE_KINDS,
        *SECURABLE_TYPES,
        *PRIVILEGES,
        *ISOLATION_MODES,
        *EXPOSURE_IDENTITIES,
        *PRIVILEGE_CLASSES,
        DIRECT_SOURCE,
    )
)
VOCABULARY_TERMS = _VOCABULARY
# Names every Databricks workspace shares, and its standard folder roots. They
# identify no client, and as masks they would blank unrelated log text
# (``main`` inside ``origin/main``), so the profile never masks them.
PLATFORM_NAMES = frozenset({"main", "samples", "system", "hive_metastore"})
_PLATFORM_FOLDERS = frozenset({"/workspace", "/workspace/shared", "/workspace/users", "/workspace/repos"})


def is_placeholder(value: str) -> bool:
    """A template value: anything holding ``<...>`` or the legacy bundle placeholder."""

    return _PLACEHOLDER.search(value) is not None or value.strip() == LEGACY_PLACEHOLDER


def is_vocabulary(value: str) -> bool:
    """A closed schema term (principal, kind, type, privilege, identity, class)."""

    return " ".join(value.split()).casefold() in _VOCABULARY


def is_platform_name(value: str) -> bool:
    """A platform-default catalog name or a standard workspace folder root."""

    folded = value.strip().casefold()
    return folded in PLATFORM_NAMES or (folded.rstrip("/") or "/") in _PLATFORM_FOLDERS


def is_iso_date(value: str) -> bool:
    return ISO_DATE.fullmatch(value) is not None


def normalize_principal(value: str) -> str:
    """Whitespace-collapsed, case-folded principal, for equality checks only."""

    return " ".join(value.split()).casefold()


def require(value: str, shape: re.Pattern[str], *, expected: str) -> str:
    """Return ``value`` when it is a placeholder or has ``shape``; else refuse.

    The message says what was expected; the caller's location names the field.
    """

    if is_placeholder(value) or shape.fullmatch(value) is not None:
        return value
    raise ValueError(f"must be {expected}")


def split_list(value: str) -> tuple[str, ...]:
    """A comma-separated list with blanks dropped and order kept (first wins)."""

    items: list[str] = []
    for part in value.split(","):
        item = part.strip()
        if item and item not in items:
            items.append(item)
    return tuple(items)


def https_host(value: str) -> str:
    """The lower-cased hostname of a bare ``https://`` origin, or refuse."""

    message = "must be a bare https:// origin with no path, port or credentials"
    raw = value.strip()
    if any(ord(character) <= 32 or ord(character) == 127 for character in raw):
        raise ValueError(message)
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(message) from exc
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or not hostname
        or len(hostname) > 253
        or any(_HOST_LABEL.fullmatch(label) is None for label in hostname.split("."))
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(message)
    return hostname


def host_workspace_id(hostname: str) -> str | None:
    """The workspace id an Azure or GCP workspace host embeds; AWS hosts embed none."""

    match = _HOST_WORKSPACE_ID.match(hostname)
    return match.group("id") if match else None

