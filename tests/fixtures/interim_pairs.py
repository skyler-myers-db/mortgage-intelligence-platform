"""Interim owner pairs from their JSON text, for tests (M2-PR8).

Production reads ``MIP_UC_INTERIM_OWNER_PAIRS_JSON`` through the client
profile (tools/clients/json_fields.InterimOwnerPair, whose JSON-shape refusals
tests/unit/test_client_profile_schema.py pins) and then ``build_pairs``; this
helper takes the same two steps so a test can write pairs as JSON.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from tools.clients.json_fields import InterimOwnerPair
from tools.databricks.uc_interim_owner_pairs import InterimOwnerPairs, build_pairs


def pairs_from_json(text: str, *, now: datetime | None = None) -> InterimOwnerPairs:
    rows = json.loads(text) if text.strip() else []
    return build_pairs([InterimOwnerPair.model_validate(row) for row in rows], now=now or datetime.now(UTC))


__all__ = ["pairs_from_json"]
