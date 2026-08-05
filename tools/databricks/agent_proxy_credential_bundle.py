"""Canonical atomic GitHub secret for the hosted agent-proxy OAuth credential."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass

AGENT_PROXY_CREDENTIAL_BUNDLE_VERSION = 1
AGENT_PROXY_CREDENTIAL_BUNDLE_SECRET = "DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE"
_FIELDS = frozenset({"client_id", "client_secret", "credential_id", "version"})


@dataclass(frozen=True)
class AgentProxyCredential:
    client_id: str
    credential_id: str
    client_secret: str


def canonical_agent_proxy_credential_bundle(
    *,
    client_id: str,
    credential_id: str,
    client_secret: str,
) -> str:
    """Return the one-write credential bundle without normalizing secret bytes."""

    values = {
        "client_id": client_id,
        "client_secret": client_secret,
        "credential_id": credential_id,
    }
    if any(
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(character) < 32 for character in value)
        for value in values.values()
    ):
        raise ValueError("agent-proxy credential bundle values are invalid")
    return json.dumps(
        {
            **values,
            "version": AGENT_PROXY_CREDENTIAL_BUNDLE_VERSION,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def parse_agent_proxy_credential_bundle(value: str) -> AgentProxyCredential:
    """Parse only the exact canonical bundle generated at credential mint time."""

    try:
        payload = json.loads(value)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("agent-proxy credential bundle is not valid JSON") from exc
    if not isinstance(payload, Mapping) or set(payload) != _FIELDS:
        raise ValueError("agent-proxy credential bundle fields are invalid")
    if payload.get("version") != AGENT_PROXY_CREDENTIAL_BUNDLE_VERSION:
        raise ValueError("agent-proxy credential bundle version is invalid")
    try:
        canonical = canonical_agent_proxy_credential_bundle(
            client_id=payload["client_id"],
            credential_id=payload["credential_id"],
            client_secret=payload["client_secret"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("agent-proxy credential bundle values are invalid") from exc
    if value != canonical:
        raise ValueError("agent-proxy credential bundle is not canonical")
    return AgentProxyCredential(
        client_id=payload["client_id"],
        credential_id=payload["credential_id"],
        client_secret=payload["client_secret"],
    )


def main(argv: list[str] | None = None) -> int:
    """Emit bounded fields from the canonical bundle without accepting secrets in argv."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "fields",
        choices=("public-fields", "all-fields"),
        help="Emit client/credential IDs, optionally followed by the client secret.",
    )
    args = parser.parse_args(argv)
    credential = parse_agent_proxy_credential_bundle(
        os.environ.get(AGENT_PROXY_CREDENTIAL_BUNDLE_SECRET, "")
    )
    values = [credential.client_id, credential.credential_id]
    if args.fields == "all-fields":
        values.append(credential.client_secret)
    print("\t".join(values))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
