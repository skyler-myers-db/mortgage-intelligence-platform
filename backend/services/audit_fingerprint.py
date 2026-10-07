"""Keyed filter fingerprints for the STORED audit copies (W5c, audit 12.3).

A plain ``sha256`` of a filter set is a dictionary target: the Lead Queue and
ledger filters come from small vocabularies (states, segments, statuses, a few
hundred staff emails), so anyone holding a ledger export could hash candidate
filter sets and learn which assignee or cohort a reader looked at. The copies
the ledger STORES (VIEW_AUDIT_LEDGER, AUDIT_EXPORT and VIEW_LEADS
``filter_fingerprint``) are therefore an HMAC-SHA256 of that plain digest under
a server key, so a stored value can be compared with another stored value but
never reproduced offline.

The key derives the way ``audit_pagination._cursor_secret`` does: the
``MIP_GENIE_ACTION_SECRET_CURRENT`` secret (then the legacy secret), separated
per domain, and a process-random key under local/test. Outside local/test with
no secret the helper answers ``None`` and never raises: the caller omits the
stored value (or, where the value is the point of the row, writes no row).

Not keyed, on purpose: ``audit_pagination.audit_filter_fingerprint`` inside the
HMAC-authenticated ledger cursor (only ledger readers ever receive it), and
``lead_export_filter_fingerprint``, which an auditor recomputes from the CSV's
``# filters=`` line.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

from backend.config.settings import settings

AUDIT_LEDGER_FINGERPRINT_DOMAIN = b"mip.audit.filter_fingerprint.v1\x00"
LEAD_VIEW_FINGERPRINT_DOMAIN = b"mip.leads.filter_fingerprint.v1\x00"

_LOCAL_KEY = secrets.token_bytes(32)
_LOCAL_APP_ENVS = frozenset({"local", "test", "testing", "pytest"})
_RAW_HEX = re.compile(r"^[0-9a-f]{64}$")


def _secret_text(*, previous: bool) -> str | None:
    if previous:
        configured = settings.mip_genie_action_secret_previous
    else:
        configured = settings.mip_genie_action_secret_current or settings.mip_genie_action_secret
    if configured is None:
        return None
    value = configured.get_secret_value().strip()
    return value or None


def _is_local_env() -> bool:
    return (settings.app_env or "").strip().lower() in _LOCAL_APP_ENVS


def fingerprint_keys(domain: bytes) -> list[bytes]:
    """The keys a stored value may have been made under: current first, then previous.

    Empty outside local/test when no secret is configured.
    """

    keys: list[bytes] = []
    for previous in (False, True):
        secret = _secret_text(previous=previous)
        if secret is not None:
            keys.append(hashlib.sha256(domain + secret.encode("utf-8")).digest())
    if not keys and _is_local_env():
        keys.append(hmac.new(_LOCAL_KEY, domain, hashlib.sha256).digest())
    return keys


def _keyed(key: bytes, raw_hex: str, domain: bytes) -> str:
    return hmac.new(key, domain + raw_hex.encode("ascii"), hashlib.sha256).hexdigest()


def keyed_filter_fingerprint(raw_hex: str, *, domain: bytes) -> str | None:
    """HMAC-SHA256 (64 lowercase hex) of a plain sha256 filter digest, or None without a key."""

    if not _RAW_HEX.fullmatch(raw_hex):
        raise ValueError("raw_hex must be a lowercase sha256 hex digest")
    keys = fingerprint_keys(domain)
    return _keyed(keys[0], raw_hex, domain) if keys else None


def keyed_filter_fingerprint_candidates(raw_hex: str, *, domain: bytes) -> list[str]:
    """Every value ``raw_hex`` keys to under the current and the previous secret.

    A cursor minted before a secret rotation carries a value keyed under what
    is now the previous secret; its verification compares against both.
    """

    if not _RAW_HEX.fullmatch(raw_hex):
        raise ValueError("raw_hex must be a lowercase sha256 hex digest")
    return [_keyed(key, raw_hex, domain) for key in fingerprint_keys(domain)]
