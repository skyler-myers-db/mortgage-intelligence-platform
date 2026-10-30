"""What the account bootstrap reads before any identity exists.

The full ``ClientProfile`` needs every identity's application id and every
M2M secret, which do not exist until the bootstrap has run. So the
bootstrap validates the subset it needs, by the same environment names and
shapes, from the same sources: the shell, then ``.env.local`` (or
``--env-file``). Secrets in those sources are never kept.

``MIP_IDENTITY_IDS_JSON`` may be absent or partial here (``IdentityPins``).
A role pinned there, or named by ``--adopt <role>=<application id>``, is the
bootstrap's own; a role pinned nowhere is created, and an existing identity
of the same name that nothing pins is refused (plan §4.10, identity
provenance).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, field_validator

from tools.clients.bootstrap_roster import DATA_OWNERS_ROLE, ROLES, Tier
from tools.clients.environment import from_local
from tools.clients.errors import BootstrapRefusal, refusal_lines
from tools.clients.json_fields import IdentityPins
from tools.clients.schema import IDENTITY_ALIASES
from tools.clients.shapes import (
    CLOUDS,
    DNS_NAME,
    EMAIL,
    UC_IDENTIFIER,
    UUID,
    WORKSPACE_ID,
    https_host,
    is_placeholder,
)

# The canonical Cotality source names, which an unset profile field means
# (deploy/clients/example.env). M3-PR1 moves them into one shared constant.
CANONICAL_SOURCE_CATALOG = "cotality_mortgage_data"
CANONICAL_SOURCE_SCHEMA = "corelogic"
_TIER_FLAG = {"off": "core", "on": "agentic"}
_CLIENT = re.compile(r"[a-z][a-z0-9-]{1,30}")
_PREFIX = re.compile(r"[a-z][a-z0-9-]{1,20}")
_GROUP_ID = re.compile(r"[1-9][0-9]{0,30}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def _require(value: str, shape: re.Pattern[str], *, expected: str) -> str:
    """Like ``shapes.require``, except that a placeholder is always refused."""

    if is_placeholder(value):
        raise ValueError("holds a <placeholder>; set a real value")
    if shape.fullmatch(value) is None:
        raise ValueError(f"must be {expected}")
    return value


class BootstrapInputs(BaseModel):
    """The profile subset the bootstrap needs, validated by environment name."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    client: str = Field(alias="MIP_CLIENT")
    cloud: str = Field(alias="MIP_CLOUD")
    identity_prefix: str = Field(default="mip", alias="MIP_IDENTITY_PREFIX")
    agentic_tier: str | None = Field(default=None, alias="MIP_AGENTIC_TIER")
    app_name: str | None = Field(default=None, alias="MIP_APP_NAME")
    source_catalog: str = Field(default=CANONICAL_SOURCE_CATALOG, alias="MIP_COTALITY_SOURCE_CATALOG")
    source_schema: str = Field(default=CANONICAL_SOURCE_SCHEMA, alias="MIP_COTALITY_SOURCE_SCHEMA")
    workspace_host: str = Field(alias="DATABRICKS_HOST")
    workspace_id: str = Field(alias="MIP_EXPECT_WORKSPACE_ID")
    metastore_id: str | None = Field(default=None, alias="MIP_EXPECT_METASTORE_ID")
    account_id: str = Field(alias="DATABRICKS_ACCOUNT_ID")
    account_host: str | None = Field(default=None, alias="DATABRICKS_ACCOUNT_HOST")
    pins: IdentityPins = Field(default_factory=IdentityPins, alias="MIP_IDENTITY_IDS_JSON")

    @field_validator("client")
    @classmethod
    def _client(cls, value: str) -> str:
        return _require(value, _CLIENT, expected="2-31 lowercase letters, digits or '-', from a letter")

    @field_validator("cloud")
    @classmethod
    def _cloud(cls, value: str) -> str:
        if value not in CLOUDS:
            raise ValueError("must be aws, azure or gcp")
        return value

    @field_validator("identity_prefix")
    @classmethod
    def _prefix(cls, value: str) -> str:
        return _require(value, _PREFIX, expected="2-21 lowercase letters, digits or '-'")

    @field_validator("agentic_tier")
    @classmethod
    def _tier(cls, value: str | None) -> str | None:
        if value is not None and value not in _TIER_FLAG:
            raise ValueError("must be off or on")
        return value

    @field_validator("app_name")
    @classmethod
    def _app_name(cls, value: str | None) -> str | None:
        return None if value is None else _require(value, DNS_NAME, expected="a lowercase DNS-style name")

    @field_validator("source_catalog", "source_schema")
    @classmethod
    def _uc_name(cls, value: str) -> str:
        return _require(value, UC_IDENTIFIER, expected="a lowercase unquoted identifier")

    @field_validator("workspace_host", "account_host")
    @classmethod
    def _origin(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if is_placeholder(value):
            raise ValueError("holds a <placeholder>; set a real value")
        return f"https://{https_host(value)}"

    @field_validator("workspace_id")
    @classmethod
    def _workspace_id(cls, value: str) -> str:
        return _require(value, WORKSPACE_ID, expected="a decimal workspace id")

    @field_validator("metastore_id", "account_id")
    @classmethod
    def _uuid(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        kind = "a metastore" if info.field_name == "metastore_id" else "an account"
        return _require(value, UUID, expected=f"{kind} id (UUID)").lower()

    @field_validator("pins", mode="before")
    @classmethod
    def _pins_json(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        try:
            parsed = json.loads(value)
        except ValueError:
            raise ValueError("must be single-line JSON") from None
        if not isinstance(parsed, dict):
            raise ValueError("must be a JSON object")
        return parsed

    @field_validator("pins")
    @classmethod
    def _real_pins(cls, value: IdentityPins) -> IdentityPins:
        # IdentityPins admits a template's <placeholder>; a live run never does.
        placeholders = [role for role, pin in value.by_role().items() if is_placeholder(pin)]
        if placeholders:
            raise ValueError(f"{', '.join(placeholders)}: holds a <placeholder>; set a real value")
        return value

    def resolved_account_host(self) -> str:
        """The account console origin: the profile's, or the cloud's."""

        return self.account_host or f"https://{CLOUDS[self.cloud][1]}"

    def host_refusals(self) -> list[str]:
        suffix, account_host, _scheme = CLOUDS[self.cloud]
        lines = []
        hostname = https_host(self.workspace_host)
        if not hostname.endswith(suffix) or hostname == account_host:
            lines.append(f"DATABRICKS_HOST: must be a workspace host on {self.cloud} (*{suffix})")
        if self.account_host is not None and self.account_host != f"https://{account_host}":
            lines.append(f"DATABRICKS_ACCOUNT_HOST: must be the account console host of {self.cloud}")
        return lines


_FIELDS = tuple(field.alias for field in BootstrapInputs.model_fields.values() if field.alias)


@dataclass(frozen=True)
class BootstrapRequest:
    """Validated inputs plus the operator's flags."""

    inputs: BootstrapInputs
    tier: Tier
    pins: Mapping[str, str]
    adopted_roles: frozenset[str]
    adopted_group_id: str | None
    interim_member: str | None
    repository: str


def load_inputs(environ: Mapping[str, str], env_file: Path | None) -> tuple[BootstrapInputs, dict[str, str]]:
    """The inputs and the identity aliases found beside them (shell beats file)."""

    raw = from_local(environ, env_file)
    values = {name: value for name, value in raw.values.items() if name in _FIELDS}
    try:
        inputs = BootstrapInputs.model_validate(values)
    except ValidationError as exc:
        raise BootstrapRefusal(refusal_lines(exc)) from None
    lines = inputs.host_refusals()
    if lines:
        raise BootstrapRefusal(lines)
    return inputs, dict(raw.identity_aliases)


def parse_adoptions(entries: Iterable[str]) -> dict[str, str]:
    """``--adopt <role>=<id>``: an application id per role, a group id for data owners."""

    adoptions: dict[str, str] = {}
    lines = []
    known = (*ROLES, DATA_OWNERS_ROLE)
    for entry in entries:
        role, sep, value = (part.strip() for part in entry.partition("="))
        if not sep or role not in known:
            lines.append(f"--adopt: expected <role>=<id> with a role among {', '.join(known)}")
        elif role in adoptions:
            lines.append(f"--adopt {role}: given more than once")
        elif role == DATA_OWNERS_ROLE:
            if _GROUP_ID.fullmatch(value):
                adoptions[role] = value
            else:
                lines.append(f"--adopt {role}: must be a decimal SCIM group id")
        elif UUID.fullmatch(value):
            adoptions[role] = value.lower()
        else:
            lines.append(f"--adopt {role}: must be an application id (UUID)")
    if lines:
        raise BootstrapRefusal(lines)
    return adoptions


def build_request(
    inputs: BootstrapInputs,
    aliases: Mapping[str, str],
    *,
    tier: Tier,
    adoptions: Mapping[str, str],
    interim_member: str | None,
    repository: str | None,
) -> BootstrapRequest:
    """Merge pins and adoptions; refuse every disagreement, value-free."""

    lines = []
    if inputs.agentic_tier is not None and _TIER_FLAG[inputs.agentic_tier] != tier:
        lines.append(f"--tier {tier}: must agree with MIP_AGENTIC_TIER={inputs.agentic_tier}")
    pins = {role: value.lower() for role, value in inputs.pins.by_role().items()}
    adopted = set()
    for role, value in adoptions.items():
        if role == DATA_OWNERS_ROLE:
            continue
        pinned = pins.get(role)
        if pinned is not None and pinned != value:
            lines.append(f"--adopt {role}: MIP_IDENTITY_IDS_JSON already pins a different identity")
        elif pinned is None:
            pins[role] = value
            adopted.add(role)
    owners: dict[str, str] = {}
    for role, value in pins.items():
        other = owners.setdefault(value, role)
        if other != role:
            lines.append(f"{other} and {role}: must be distinct identities")
    for name, value in sorted(aliases.items()):
        role = IDENTITY_ALIASES[name]
        if role not in pins:
            lines.append(f"{name}: names a {role} identity nothing pins; pin it with --adopt {role}=<application id>")
        elif pins[role] != value.strip().lower():
            lines.append(f"{name}: must equal MIP_IDENTITY_IDS_JSON.{role}")
    if interim_member is not None and not EMAIL.fullmatch(interim_member.strip()):
        lines.append("--interim-member: must be an account user's email address")
    if not repository or not REPOSITORY.fullmatch(repository):
        lines.append("--github-repository: name the owner/repository whose environments the policies trust")
    if lines:
        raise BootstrapRefusal(lines)
    assert repository is not None
    return BootstrapRequest(
        inputs=inputs,
        tier=tier,
        pins=pins,
        adopted_roles=frozenset(adopted),
        adopted_group_id=adoptions.get(DATA_OWNERS_ROLE),
        interim_member=interim_member.strip() if interim_member else None,
        repository=repository,
    )
