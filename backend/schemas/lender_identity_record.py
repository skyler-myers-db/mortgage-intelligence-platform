"""The reviewed lender identity: the code registry or one digest-pinned record.

``backend/schemas/lender_identity.py`` registers only the Summit Mortgage
demo pair. A customer lender is configuration, not a code change: its
reviewed record is the JSON object in ``MIP_LENDER_IDENTITY_JSON`` (an
identifying profile value: a GitHub Environment secret, or ``.env.local``),
and ``MIP_LENDER_IDENTITY_SHA256`` (a public value) pins the sha256 of the
record's canonical JSON. Review approves the digest: the record reaches a
deploy only through the client's GitHub Environment approval (review mode
``single_maintainer_audited``), and any edit to it, one character included,
changes the digest the deploy and the App recompute.

``reviewed_lender_identity_from_env`` is the one resolver every lender call
site uses (settings, the App payload builder, bundle_env, the deploy
preflight, the Lakebase disclosure seed, the lender-mark tool and the SQL
render):

* Both names blank or absent: exactly the legacy path.
  ``MIP_LENDER_NAME`` / ``MIP_LENDER_NMLS_ID`` / ``MIP_TENANT_ID`` are
  validated against the code registry by the same functions as before, and
  nothing else is read.
* Either set: the record must parse under the reviewed shapes (the code
  registry's own name, NMLS, tenant and SMS-budget validators, plus the
  lender-mark rules), must not be any part of the Summit demo identity, and
  its canonical digest must equal the pin. A legacy name that is also set
  must equal the record. Anything else raises ``LenderIdentityRefused``.

The canonical JSON is the normalized record serialized with sorted keys and
no whitespace (``json.dumps(..., sort_keys=True, separators=(",", ":"))``),
the form ``tools/clients/profile.py`` emits for the same field, so a profile
and the App compute one digest. Every refusal is a fixed sentence naming the
field and the rule, never a value: a refusal prints in public CI logs.

Stdlib only, like its two siblings, so the deploy preflight can import it
before any dependency install.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any, cast

from backend.schemas.lender_branding import (
    MediaType,
    ReviewedLenderMark,
    reviewed_lender_mark,
    reviewed_mark_problems,
)
from backend.schemas.lender_identity import (
    _PUBLIC_TENANT_ID_RE,
    _SUMMIT_DEMO_NMLS_ID,
    _SUMMIT_LENDER_NAME,
    _SUMMIT_TENANT_ID,
    effective_public_tenant_id,
    require_sms_disclosure_budget,
    validate_public_lender_identity,
    validate_public_lender_name,
    validate_public_lender_nmls_id,
)

LENDER_NAME_ENV = "MIP_LENDER_NAME"
LENDER_NMLS_ENV = "MIP_LENDER_NMLS_ID"
TENANT_ID_ENV = "MIP_TENANT_ID"
LENDER_IDENTITY_JSON_ENV = "MIP_LENDER_IDENTITY_JSON"
LENDER_IDENTITY_SHA256_ENV = "MIP_LENDER_IDENTITY_SHA256"
LEGACY_ENV_NAMES = (LENDER_NAME_ENV, LENDER_NMLS_ENV, TENANT_ID_ENV)
PINNED_ENV_NAMES = (LENDER_IDENTITY_JSON_ENV, LENDER_IDENTITY_SHA256_ENV)

_MAX_RECORD_CHARS = 16384
_MAX_ALIASES = 32
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_CALENDAR_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
# The profile's shapes (tools/clients/shapes.py: TITLE, EMAIL, ISO_DATE).
# tests/unit/test_lender_identity_record.py holds the two to one verdict.
_ALIAS_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9 &'._()-]{0,98}[A-Za-z0-9)])?")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_ISO_DATE_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
    r"(?:T[0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]+)?)?(?:Z|[+-][0-9]{2}:[0-9]{2})?)?"
)
_RECORD_KEYS = frozenset(
    {"version", "legal_name", "nmls_id", "tenant_id", "servicer_aliases", "reviewed_by", "reviewed_at", "mark"}
)
_REQUIRED_KEYS = _RECORD_KEYS - {"mark"}
_MARK_KEYS = frozenset({"sha256", "media_type", "width", "height", "authorization_ref", "reviewed_on"})
_FIELD = LENDER_IDENTITY_JSON_ENV


class LenderIdentityRefused(ValueError):
    """A configured lender record or pin was refused; the message names no value."""


@dataclass(frozen=True)
class ReviewedLenderIdentity:
    """The lender identity one deployment serves, and how it was reviewed.

    ``digest`` is ``None`` on the code-registry path and the record's
    canonical sha256 on the pinned path. ``tenant_aliases`` are the raw
    Cotality servicer keys (upper case) that name the tenant itself.
    """

    lender_name: str
    nmls_id: str
    tenant_id: str
    tenant_aliases: tuple[str, ...] = ()
    mark: ReviewedLenderMark | None = None
    digest: str | None = None
    canonical_json: str | None = field(default=None, repr=False)

    @property
    def pinned(self) -> bool:
        return self.digest is not None


def _text(env: Mapping[str, object], name: str) -> str:
    return str(env.get(name) or "").strip()


def pinned_identity_configured(env: Mapping[str, object]) -> bool:
    """True when either pinned-record name holds a value (blank counts as absent)."""

    return any(_text(env, name) for name in PINNED_ENV_NAMES)


def _refuse(rule: str) -> LenderIdentityRefused:
    return LenderIdentityRefused(f"{_FIELD}: {rule}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise _refuse("repeats a key")
    return dict(pairs)


def _string(record: Mapping[str, Any], key: str, *, prefix: str = "") -> str:
    value = record.get(key)
    if not isinstance(value, str):
        raise _refuse(f"{prefix}{key} must be a string")
    return value.strip()


def _shaped(value: str, pattern: re.Pattern[str], *, key: str, expected: str) -> str:
    if pattern.fullmatch(value) is None:
        raise _refuse(f"{key} must be {expected}")
    return value


def _no_constant(name: str) -> None:
    raise _refuse("must not hold NaN or Infinity")


def _validated(validator: Callable[[str], str], value: str, *, key: str) -> str:
    try:
        return validator(value)
    except ValueError as exc:
        # The validators' messages are fixed sentences that carry no value.
        raise _refuse(f"{key}: {exc}") from None


def _aliases(record: Mapping[str, Any]) -> tuple[list[str], tuple[str, ...]]:
    """The record's aliases as given (stripped) and as upper-case raw keys."""

    values = record.get("servicer_aliases")
    if not isinstance(values, list) or not values or len(values) > _MAX_ALIASES:
        raise _refuse(f"servicer_aliases must list 1-{_MAX_ALIASES} servicer names")
    given: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise _refuse("servicer_aliases must hold strings")
        given.append(
            _shaped(value.strip(), _ALIAS_RE, key="servicer_aliases[]", expected="servicer names of at most 100 characters")
        )
    raw_keys = tuple(" ".join(alias.split()).upper() for alias in given)
    if len(set(raw_keys)) != len(raw_keys):
        raise _refuse("servicer_aliases must not repeat a servicer name in any case")
    return given, raw_keys


def _mark(record: Mapping[str, Any], nmls_id: str) -> tuple[dict[str, Any], ReviewedLenderMark] | None:
    raw = record.get("mark")
    if raw is None:
        if "mark" in record:
            raise _refuse("mark must be an object when present")
        return None
    if not isinstance(raw, dict) or set(raw) != _MARK_KEYS:
        raise _refuse("mark must hold exactly sha256, media_type, width, height, authorization_ref and reviewed_on")
    width, height = raw["width"], raw["height"]
    if type(width) is not int or type(height) is not int:
        raise _refuse("mark.width and mark.height must be whole numbers")
    reviewed_on_text = _string(raw, "reviewed_on", prefix="mark.")
    try:
        if _CALENDAR_DATE_RE.fullmatch(reviewed_on_text) is None:
            raise ValueError(reviewed_on_text)
        reviewed_on = date.fromisoformat(reviewed_on_text)
    except ValueError:
        raise _refuse("mark.reviewed_on must be an ISO-8601 calendar date") from None
    sha256 = _string(raw, "sha256", prefix="mark.")
    media_type = _string(raw, "media_type", prefix="mark.")
    authorization_ref = _string(raw, "authorization_ref", prefix="mark.")
    mark = ReviewedLenderMark(
        nmls_id=nmls_id,
        sha256=sha256,
        # reviewed_mark_problems refuses any other media type just below.
        media_type=cast(MediaType, media_type),
        width=width,
        height=height,
        authorization_ref=authorization_ref,
        reviewed_on=reviewed_on,
    )
    normalized = {
        "sha256": sha256,
        "media_type": media_type,
        "width": width,
        "height": height,
        "authorization_ref": authorization_ref,
        "reviewed_on": reviewed_on_text,
    }
    problems = reviewed_mark_problems(f"{_FIELD}: mark", mark)
    if problems:
        raise LenderIdentityRefused("; ".join(problems))
    return normalized, mark


def _is_summit(lender: str, nmls_id: str, tenant_id: str) -> bool:
    return (
        lender.casefold() == _SUMMIT_LENDER_NAME.casefold()
        or nmls_id == _SUMMIT_DEMO_NMLS_ID
        or tenant_id == _SUMMIT_TENANT_ID
    )


def parse_lender_identity_record(text: str) -> ReviewedLenderIdentity:
    """Parse and normalize one record; the result's digest is its canonical sha256.

    This checks shape only. ``reviewed_lender_identity_from_env`` also holds
    the digest to the reviewed pin.
    """

    if len(text) > _MAX_RECORD_CHARS:
        raise _refuse(f"must be at most {_MAX_RECORD_CHARS} characters")
    try:
        record = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_no_constant)
    except LenderIdentityRefused:
        raise
    except ValueError:
        raise _refuse("must be one JSON object") from None
    if not isinstance(record, dict):
        raise _refuse("must be one JSON object")
    unknown, missing = set(record) - _RECORD_KEYS, _REQUIRED_KEYS - set(record)
    if unknown or missing:
        raise _refuse(
            "must hold exactly version, legal_name, nmls_id, tenant_id, servicer_aliases, "
            "reviewed_by, reviewed_at and an optional mark"
        )
    if type(record["version"]) is not int or record["version"] != 1:
        raise _refuse("version must be 1")
    lender = _validated(validate_public_lender_name, _string(record, "legal_name"), key="legal_name")
    nmls_id = _validated(validate_public_lender_nmls_id, _string(record, "nmls_id"), key="nmls_id")
    tenant_id = _shaped(
        _string(record, "tenant_id"), _PUBLIC_TENANT_ID_RE, key="tenant_id",
        expected="a lowercase identifier of at most 64 characters",
    )
    if _is_summit(lender, nmls_id, tenant_id):
        raise _refuse("the Summit Mortgage demo identity, or any part of it, is never a pinned record")
    try:
        require_sms_disclosure_budget(lender, nmls_id)
    except ValueError as exc:
        raise _refuse(f"legal_name and nmls_id: {exc}") from None
    aliases, raw_keys = _aliases(record)
    reviewed_by = _shaped(_string(record, "reviewed_by"), _EMAIL_RE, key="reviewed_by", expected="the reviewer's email address")
    reviewed_at = _shaped(_string(record, "reviewed_at"), _ISO_DATE_RE, key="reviewed_at", expected="an ISO-8601 date")
    normalized: dict[str, Any] = {
        "version": 1,
        "legal_name": lender,
        "nmls_id": nmls_id,
        "tenant_id": tenant_id,
        "servicer_aliases": aliases,
        "reviewed_by": reviewed_by,
        "reviewed_at": reviewed_at,
    }
    parsed_mark = _mark(record, nmls_id)
    if parsed_mark is not None:
        normalized["mark"] = parsed_mark[0]
    canonical = canonical_lender_identity_json(normalized)
    return ReviewedLenderIdentity(
        lender_name=lender,
        nmls_id=nmls_id,
        tenant_id=tenant_id,
        tenant_aliases=raw_keys,
        mark=None if parsed_mark is None else parsed_mark[1],
        digest=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        canonical_json=canonical,
    )


def canonical_lender_identity_json(record: Mapping[str, Any]) -> str:
    """Sorted keys, no whitespace: the text the pinned sha256 is taken over."""

    return json.dumps(record, sort_keys=True, separators=(",", ":"))


def lender_identity_digest(text: str) -> str:
    """The sha256 a reviewer pins for this record (``python -m`` prints it)."""

    digest = parse_lender_identity_record(text).digest
    assert digest is not None
    return digest


def _code_registry_identity(env: Mapping[str, object]) -> ReviewedLenderIdentity:
    """The legacy path, unchanged: a missing name is Summit, a present one is validated as given."""

    lender, nmls_id = validate_public_lender_identity(
        env.get(LENDER_NAME_ENV, _SUMMIT_LENDER_NAME),
        env.get(LENDER_NMLS_ENV),
    )
    tenant_id = effective_public_tenant_id(env.get(TENANT_ID_ENV), lender_name=lender)
    return ReviewedLenderIdentity(
        lender_name=lender,
        nmls_id=nmls_id,
        tenant_id=tenant_id,
        mark=reviewed_lender_mark(lender),
    )


def _require_legacy_agreement(env: Mapping[str, object], identity: ReviewedLenderIdentity) -> None:
    expected = {
        LENDER_NAME_ENV: identity.lender_name,
        LENDER_NMLS_ENV: identity.nmls_id,
        TENANT_ID_ENV: identity.tenant_id,
    }
    for name, value in expected.items():
        configured = _text(env, name)
        if not configured:
            continue
        if name == LENDER_NAME_ENV:
            configured = " ".join(configured.split())
        if configured != value:
            raise LenderIdentityRefused(f"{name}: must equal the pinned {_FIELD} record when both are set")


def reviewed_lender_identity_from_env(env: Mapping[str, object]) -> ReviewedLenderIdentity:
    """Resolve the one reviewed lender identity from these configuration values.

    ``env`` maps the five names in ``LEGACY_ENV_NAMES`` and
    ``PINNED_ENV_NAMES`` to values; other keys are ignored. A caller passes
    only the legacy names its own legacy semantics treat as set, so the
    code-registry path stays byte-identical at every call site. Raises
    ``ValueError`` on the legacy path (the registry's own messages) and
    ``LenderIdentityRefused`` on the pinned path.
    """

    if not pinned_identity_configured(env):
        return _code_registry_identity(env)
    text, pin = _text(env, LENDER_IDENTITY_JSON_ENV), _text(env, LENDER_IDENTITY_SHA256_ENV)
    if not text:
        raise LenderIdentityRefused(f"{LENDER_IDENTITY_SHA256_ENV}: pins a record, but {_FIELD} is unset")
    if not pin:
        raise LenderIdentityRefused(f"{LENDER_IDENTITY_SHA256_ENV}: is required to pin {_FIELD}")
    if _SHA256_RE.fullmatch(pin) is None:
        raise LenderIdentityRefused(f"{LENDER_IDENTITY_SHA256_ENV}: must be 64 lowercase hex characters")
    identity = parse_lender_identity_record(text)
    assert identity.digest is not None
    if not hmac.compare_digest(identity.digest, pin):
        raise LenderIdentityRefused(
            f"{_FIELD}: its canonical sha256 does not equal {LENDER_IDENTITY_SHA256_ENV}; "
            "the record changed after review, or the pin is stale"
        )
    _require_legacy_agreement(env, identity)
    return identity


def present_env(values: Mapping[str, object]) -> dict[str, str]:
    """The five lender names whose value is not blank, for callers where blank means unset."""

    names = (*LEGACY_ENV_NAMES, *PINNED_ENV_NAMES)
    return {name: str(values[name]) for name in names if str(values.get(name) or "").strip()}


def as_configured(values: Mapping[str, object]) -> dict[str, object]:
    """The five lender names for a caller whose legacy values were never stripped.

    bundle_env and the deploy preflight passed a legacy value to the registry
    as given, so only a missing or empty one was unset: a whitespace-only
    ``MIP_LENDER_NAME`` reached the name validator and was refused there.
    This keeps that for the legacy names (a whitespace-only NMLS or tenant id
    resolves as unset in the registry itself, as before) and applies
    ``present_env``'s blank-is-unset rule to the two pinned names only.
    """

    legacy = {name: values[name] for name in LEGACY_ENV_NAMES if values.get(name) not in (None, "")}
    return {**legacy, **{name: str(values[name]) for name in PINNED_ENV_NAMES if _text(values, name)}}


def tenant_lender_dictionary(identity: ReviewedLenderIdentity) -> dict[str, str]:
    """Raw servicer key -> display name for the tenant's own rows (pinned path only)."""

    return {raw_key: identity.lender_name for raw_key in identity.tenant_aliases}


if __name__ == "__main__":  # pragma: no cover - operator helper
    import os
    import sys

    try:
        print(lender_identity_digest(os.environ.get(LENDER_IDENTITY_JSON_ENV, "")))
    except LenderIdentityRefused as refusal:
        print(refusal, file=sys.stderr)
        raise SystemExit(2) from None
