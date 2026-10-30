"""Closed models for the profile's single-line JSON fields.

Each model forbids unknown keys and accepts a ``<placeholder>`` in any string
leaf, so ``deploy/clients/example.env`` can carry the full structure; the
profile refuses placeholders afterwards unless the caller allows them.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from backend.schemas.lender_identity import (
    validate_public_lender_name,
    validate_public_lender_nmls_id,
)
from tools.clients.shapes import (
    DIRECT_SOURCE,
    EMAIL,
    EXPOSURE_IDENTITIES,
    EXPOSURE_KINDS,
    INHERITED_SOURCE_PREFIX,
    ISO_DATE,
    ISOLATION_MODES,
    PRINCIPAL,
    PRIVILEGE_CLASSES,
    PRIVILEGES,
    SECURABLE_TYPES,
    TITLE,
    UUID,
    WORKSPACE_ID,
    is_placeholder,
    require,
)

_TENANT_ID = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SINGLE_LINE = re.compile(r"[^\n\r\0]{1,500}")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


def _one_of(value: str, allowed: frozenset[str], *, expected: str) -> str:
    if is_placeholder(value) or value in allowed:
        return value
    raise ValueError(f"must be {expected}")


def _distinct_subset(
    values: tuple[str, ...], allowed: frozenset[str], *, expected: str
) -> tuple[str, ...]:
    if not values:
        raise ValueError(f"must list at least one of {expected}")
    if len(set(values)) != len(values):
        raise ValueError("must not repeat an entry")
    for value in values:
        _one_of(value, allowed, expected=expected)
    return values


def _application_id(value: str | None) -> str | None:
    if value is None:
        return None
    return require(value, UUID, expected="a service-principal application id (UUID)")


def _assert_distinct(by_role: dict[str, str]) -> None:
    seen: dict[str, str] = {}
    for role, value in by_role.items():
        other = seen.setdefault(value.casefold(), role)
        if other != role:
            raise ValueError(f"{other} and {role} must be distinct identities")


class IdentityIds(StrictModel):
    """The install's service-principal application ids, by role (plan §4.3).

    ``verifier``, ``runtime`` and ``proxy`` serve only the agentic tier; the
    profile requires them when ``MIP_AGENTIC_TIER=on``.
    """

    deployer: str
    jobs: str
    validator: str
    normal: str
    operator2: str
    admin: str
    release_probe: str
    verifier: str | None = None
    runtime: str | None = None
    proxy: str | None = None
    account_principal: str

    @field_validator("*")
    @classmethod
    def _application_id(cls, value: str | None) -> str | None:
        return _application_id(value)

    @model_validator(mode="after")
    def _pairwise_distinct(self) -> IdentityIds:
        _assert_distinct(self.by_role())
        return self

    def by_role(self) -> dict[str, str]:
        """Every configured role and its application id, in declaration order."""

        return {
            role: value
            for role, value in self.model_dump().items()
            if isinstance(value, str)
        }


class IdentityPins(StrictModel):
    """``MIP_IDENTITY_IDS_JSON`` before every identity exists: each role optional.

    The account bootstrap (tools/clients/bootstrap_account.py) reads the
    roles it already pinned from here, creates the rest, and hands the grown
    map to the credential sink. Same roles, shape and distinctness rule as
    ``IdentityIds``; the full profile still requires every core role.
    """

    deployer: str | None = None
    jobs: str | None = None
    validator: str | None = None
    normal: str | None = None
    operator2: str | None = None
    admin: str | None = None
    release_probe: str | None = None
    verifier: str | None = None
    runtime: str | None = None
    proxy: str | None = None
    account_principal: str | None = None

    @field_validator("*")
    @classmethod
    def _application_id(cls, value: str | None) -> str | None:
        return _application_id(value)

    @model_validator(mode="after")
    def _pairwise_distinct(self) -> IdentityPins:
        _assert_distinct(self.by_role())
        return self

    def by_role(self) -> dict[str, str]:
        """Every pinned role and its application id, in declaration order."""

        return {
            role: value
            for role, value in self.model_dump().items()
            if isinstance(value, str)
        }


class LenderIdentity(StrictModel):
    """A customer install's reviewed lender identity (customer class only)."""

    version: Literal[1]
    legal_name: str
    nmls_id: str
    tenant_id: str
    servicer_aliases: tuple[str, ...] = ()
    reviewed_by: str
    reviewed_at: str

    @field_validator("legal_name")
    @classmethod
    def _legal_name(cls, value: str) -> str:
        return value if is_placeholder(value) else validate_public_lender_name(value)

    @field_validator("nmls_id")
    @classmethod
    def _nmls_id(cls, value: str) -> str:
        return value if is_placeholder(value) else validate_public_lender_nmls_id(value)

    @field_validator("tenant_id")
    @classmethod
    def _tenant_id(cls, value: str) -> str:
        return require(value, _TENANT_ID, expected="a lowercase identifier of at most 64 characters")

    @field_validator("servicer_aliases")
    @classmethod
    def _aliases(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            require(value, TITLE, expected="servicer names of at most 100 characters")
        return values

    @field_validator("reviewed_by")
    @classmethod
    def _reviewed_by(cls, value: str) -> str:
        return require(value, EMAIL, expected="the reviewer's email address")

    @field_validator("reviewed_at")
    @classmethod
    def _reviewed_at(cls, value: str) -> str:
        return require(value, ISO_DATE, expected="an ISO-8601 date")


class ExposureSecurable(StrictModel):
    type: str
    id: str
    name: str

    @field_validator("type")
    @classmethod
    def _type(cls, value: str) -> str:
        return _one_of(value, SECURABLE_TYPES, expected="a reviewed securable type")

    @field_validator("id", "name")
    @classmethod
    def _single_line(cls, value: str) -> str:
        return require(value, _SINGLE_LINE, expected="one line of at most 500 characters")


class ExposureBinding(StrictModel):
    isolation_mode: str
    workspace_ids: tuple[str, ...] = ()

    @field_validator("isolation_mode")
    @classmethod
    def _mode(cls, value: str) -> str:
        return _one_of(value, ISOLATION_MODES, expected="OPEN or ISOLATED")

    @field_validator("workspace_ids")
    @classmethod
    def _workspace_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            require(value, WORKSPACE_ID, expected="decimal workspace ids")
        return values


class ExposureEntry(StrictModel):
    """One accepted shared-estate exposure: the plan §4.12 tuple.

    This slice fixes the tuple's shape and the class of every leaf, so the
    profile can mask it. The privilege ceiling, expiry bounds and per-identity
    matching are the accepted-exposure policy's (M2-PR4).
    """

    kind: str
    securable: ExposureSecurable
    grantee: str
    privileges: tuple[str, ...]
    source: str
    binding: ExposureBinding
    identities: tuple[str, ...]
    privilege_class: str
    reason: str
    expires_at: str

    @field_validator("kind")
    @classmethod
    def _kind(cls, value: str) -> str:
        return _one_of(value, EXPOSURE_KINDS, expected="uc_grant, uc_ownership or workspace_permission")

    @field_validator("grantee")
    @classmethod
    def _grantee(cls, value: str) -> str:
        return require(value, PRINCIPAL, expected="one principal")

    @field_validator("privileges")
    @classmethod
    def _privileges(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _distinct_subset(values, PRIVILEGES, expected="the reviewed privilege names")

    @field_validator("source")
    @classmethod
    def _source(cls, value: str) -> str:
        if is_placeholder(value) or value == DIRECT_SOURCE:
            return value
        path = value.removeprefix(INHERITED_SOURCE_PREFIX)
        if path != value and _SINGLE_LINE.fullmatch(path):
            return value
        raise ValueError("must be direct or inherited:<securable path>")

    @field_validator("identities")
    @classmethod
    def _identities(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _distinct_subset(values, EXPOSURE_IDENTITIES, expected="app, runtime or proxy")

    @field_validator("privilege_class")
    @classmethod
    def _privilege_class(cls, value: str) -> str:
        return _one_of(value, PRIVILEGE_CLASSES, expected="read or elevated")

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str) -> str:
        return require(value, _SINGLE_LINE, expected="one line of at most 500 characters")

    @field_validator("expires_at")
    @classmethod
    def _expires_at(cls, value: str) -> str:
        return require(value, ISO_DATE, expected="an ISO-8601 date")
