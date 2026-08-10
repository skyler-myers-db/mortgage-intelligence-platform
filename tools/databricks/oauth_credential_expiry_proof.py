"""Expiry proof for unmaterialized temporary-probe credential creates.

A create the provider acknowledged but never listed ("phantom create",
2026-08-09) can strand an intent with no observation and no inventory
delta. Quarantining forever is wrong for the temporary-probe class: the
secret was never delivered anywhere, any anomalously late commit
self-expires after the request's bounded TTL, and every future mutation
session re-diffs inventory and quarantines on durable drift. The proof
below enumerates exactly that class; everything else still quarantines.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, cast

from tools.databricks import oauth_credential_records as records
from tools.databricks.oauth_credential_quarantine import (
    raise_credential_quarantine,
)

_UNMATERIALIZED_CREATE_SETTLE_HORIZON_SECONDS = 3600.0
_MAX_PROBE_CREDENTIAL_LIFETIME_SECONDS = 3600


def prove_unmaterialized_probe_create_harmless(
    workspace: Any,
    *,
    intent_path: str,
    intent: dict[str, object],
    sink: dict[str, object] | None,
    delivery_ack: dict[str, object] | None,
    principal_id: str,
    before_ids: frozenset[str],
    fence: Callable[[], None],
) -> None:
    """Allow a restored resolution for an expired, undelivered probe create.

    Callers reach this only when the stabilized inventory equals the intent's
    prior inventory and no observation exists. Every guard failure keeps the
    original fail-closed quarantine.
    """

    def _quarantine(detail: str, cause: BaseException | None = None) -> None:
        raise_credential_quarantine(
            message=(
                "OAuth credential recovery has no attributable credential "
                f"and no provider proof of harmlessness: {detail}"
            ),
            label=records.field(intent, "label"),
            principal_id=principal_id,
            before_ids=before_ids,
            candidate_ids=frozenset(),
            fence=fence,
            cause=cause,
        )

    if records.field(intent, "operation_mode") != "temporary_probe":
        _quarantine("only temporary-probe creates qualify for expiry proof")
    if sink is not None or delivery_ack is not None:
        _quarantine("the credential reached a sink or acknowledgement")
    lifetime = intent.get("credential_lifetime_seconds")
    if (
        not isinstance(lifetime, int)
        or isinstance(lifetime, bool)
        or not 0 < lifetime <= _MAX_PROBE_CREDENTIAL_LIFETIME_SECONDS
    ):
        _quarantine("the create request carried no bounded credential TTL")
    try:
        modified_ms = workspace.workspace.get_status(intent_path).modified_at
        intent_age_seconds = time.time() - float(modified_ms) / 1000.0
    except BaseException as status_error:  # noqa: BLE001 - fail closed
        _quarantine("the intent age is unavailable", cause=status_error)
        raise  # unreachable; _quarantine always raises
    if intent_age_seconds <= (
        float(cast(int, lifetime)) + _UNMATERIALIZED_CREATE_SETTLE_HORIZON_SECONDS
    ):
        _quarantine(
            "a delayed create could still commit inside the TTL plus "
            "settle horizon"
        )
    fence()
