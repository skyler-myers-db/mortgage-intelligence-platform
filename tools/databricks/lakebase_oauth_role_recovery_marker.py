"""Deterministic signed-marker identity for Lakebase bootstrap recovery."""

from __future__ import annotations

import hashlib
import os

_BOOTSTRAP_DISPLAY_PREFIX = "mip-lakebase-role-bootstrap-"
_BOOTSTRAP_EXTERNAL_ID_PREFIX = "mip:lb:b:v1:"
_MARKER_SIGNING_KEY_ENV = "MIP_AI_GATEWAY_PROOF_SIGNING_KEY"


def bootstrap_identity_contract(
    *,
    instance_name: str,
    database_name: str,
    application_id: str,
) -> tuple[str, str]:
    digest = hashlib.sha256(
        f"{instance_name}\0{database_name}\0{application_id}".encode()
    ).hexdigest()
    return _BOOTSTRAP_DISPLAY_PREFIX + digest[:24], _BOOTSTRAP_EXTERNAL_ID_PREFIX + digest[:48]


def marker_signing_key() -> str | None:
    value = os.environ.get(_MARKER_SIGNING_KEY_ENV, "").strip()
    return value or None
