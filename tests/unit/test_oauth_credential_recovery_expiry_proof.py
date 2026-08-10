"""Expiry proof for unmaterialized temporary-probe creates.

A phantom create (acknowledged, never listed, observation lost with the
killed process) must not quarantine forever when the secret was never
delivered, carried a bounded TTL, and the intent has aged past any commit
horizon. Every guard failure keeps the fail-closed quarantine.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from tools.databricks.oauth_credential_expiry_proof import (
    prove_unmaterialized_probe_create_harmless,
)
from tools.databricks.oauth_credential_quarantine import (
    CredentialMutationQuarantineError,
)

_INTENT_PATH = "/.mip-deployment-leases/app.mutation.mutation.oauth-credential-intent.json"


def _intent(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "label": "temporary target identity",
        "operation_mode": "temporary_probe",
        "credential_lifetime_seconds": 300,
    }
    record.update(overrides)
    return record


def _workspace(age_seconds: float) -> SimpleNamespace:
    modified_ms = (time.time() - age_seconds) * 1000.0
    return SimpleNamespace(
        workspace=SimpleNamespace(
            get_status=lambda path: SimpleNamespace(modified_at=modified_ms)
        )
    )


def _prove(
    *,
    intent: dict[str, object],
    age_seconds: float = 22 * 3600.0,
    sink: dict[str, object] | None = None,
    delivery_ack: dict[str, object] | None = None,
    fence_calls: list[str] | None = None,
) -> None:
    calls = fence_calls if fence_calls is not None else []
    prove_unmaterialized_probe_create_harmless(
        _workspace(age_seconds),
        intent_path=_INTENT_PATH,
        intent=intent,
        sink=sink,
        delivery_ack=delivery_ack,
        principal_id="75100133948918",
        before_ids=frozenset({"a", "b"}),
        fence=lambda: calls.append("fence"),
    )


def test_expired_undelivered_probe_create_is_proven_harmless() -> None:
    fence_calls: list[str] = []
    _prove(intent=_intent(), fence_calls=fence_calls)
    assert fence_calls == ["fence"]


def test_persistent_delivery_intents_always_quarantine() -> None:
    with pytest.raises(
        CredentialMutationQuarantineError,
        match="only temporary-probe creates qualify",
    ):
        _prove(intent=_intent(operation_mode="persistent_delivery"))


def test_sink_or_acknowledgement_evidence_quarantines() -> None:
    with pytest.raises(
        CredentialMutationQuarantineError, match="reached a sink"
    ):
        _prove(intent=_intent(), sink={"repository": "gh"})
    with pytest.raises(
        CredentialMutationQuarantineError, match="reached a sink"
    ):
        _prove(intent=_intent(), delivery_ack={"acknowledged": True})


@pytest.mark.parametrize("lifetime", [0, -1, 3601, None, True, "300"])
def test_unbounded_or_missing_ttl_quarantines(lifetime: object) -> None:
    with pytest.raises(
        CredentialMutationQuarantineError, match="no bounded credential TTL"
    ):
        _prove(intent=_intent(credential_lifetime_seconds=lifetime))


def test_intent_younger_than_ttl_plus_horizon_quarantines() -> None:
    with pytest.raises(
        CredentialMutationQuarantineError, match="could still commit"
    ):
        _prove(intent=_intent(), age_seconds=1800.0)


def test_unavailable_intent_age_quarantines() -> None:
    def _broken_status(path: str) -> SimpleNamespace:
        raise RuntimeError("workspace metadata unavailable")

    workspace = SimpleNamespace(
        workspace=SimpleNamespace(get_status=_broken_status)
    )
    with pytest.raises(
        CredentialMutationQuarantineError, match="intent age is unavailable"
    ):
        prove_unmaterialized_probe_create_harmless(
            workspace,
            intent_path=_INTENT_PATH,
            intent=_intent(),
            sink=None,
            delivery_ack=None,
            principal_id="75100133948918",
            before_ids=frozenset(),
            fence=lambda: None,
        )


def test_quarantine_message_keeps_the_no_attribution_marker() -> None:
    with pytest.raises(
        CredentialMutationQuarantineError,
        match="no attributable credential",
    ):
        _prove(intent=_intent(), age_seconds=60.0)
