from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from backend.schemas.activation import ActivationDestination, ActivationStageRequest
from backend.services.activation_campaign_proof import CampaignActivationProof
from backend.services.activation_state import ActivationStateStore
from backend.services.campaign_targeting import campaign_treatment_fingerprint
from backend.services.lakebase import LakebaseError
from tests.fixtures import mock_population as mock_data


def _destination(status: str = "not_configured") -> ActivationDestination:
    return ActivationDestination(
        destination_key="salesforce_crm",
        destination_type="salesforce",
        display_name="Salesforce CRM",
        status=status,  # type: ignore[arg-type]
        allowed_actions=["stage_lead"],
        updated_at=datetime.now(UTC),
    )


def _approved_decision(
    approval_id: str,
    borrower_id: str,
    *,
    offer_code: str | None = "refi",
    campaign_id: str | None = None,
    channel: str = "email",
) -> dict[str, object]:
    return {
        "approval_id": approval_id,
        "borrower_id": borrower_id,
        "action": "approve",
        "actor_email": "skyler@entrada.ai",
        "offer_code": offer_code,
        "campaign_id": campaign_id,
        "channel": channel,
    }


def _campaign_proof_material(
    *,
    approval_id: str,
    borrower_id: str,
    campaign_id: str,
    offer_code: str = "refi",
    treatment_state: str = "ready",
    approval_treatment_fingerprint: str | None = None,
) -> tuple[dict[str, object], CampaignActivationProof]:
    criteria = {"states": ["IL"], "marketing_eligibility": "Eligible only"}
    suppression_policy = {"default": "eligible_only", "frequency_cap_days": 30}
    holdout = {"method": "hash_modulo", "size_pct": 10.0}
    household_dedup = {
        "enabled": True,
        "dedupe_unit": "household",
        "primary_contact_strategy": "highest_opportunity_eligible",
    }
    contract_fingerprint = campaign_treatment_fingerprint(
        json_contract_version=1,
        criteria=criteria,
        suppression_policy=suppression_policy,
        holdout=holdout,
        household_dedup=household_dedup,
    )
    source_snapshot_id = "b" * 64
    assignment_digest = "c" * 64
    fingerprint_material = (
        f"campaign-treatment-v2:{campaign_id}:{contract_fingerprint}:"
        f"{source_snapshot_id}:{assignment_digest}:12:10:9:1"
    )
    treatment_fingerprint = hashlib.sha256(fingerprint_material.encode("utf-8")).hexdigest()
    intent = {
        "action": "approve",
        "actor": "skyler@entrada.ai",
        "borrower_id": borrower_id,
        "offer_code": offer_code,
        "channel": "email",
        "campaign_id": campaign_id,
        "variant_name": "Benefit-led",
        "campaign_treatment_fingerprint": (
            approval_treatment_fingerprint or treatment_fingerprint
        ),
    }
    intent_text = json.dumps(intent, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    intent_hash = hashlib.sha256(intent_text.encode("utf-8")).hexdigest()
    materialization_id = str(uuid4())
    row: dict[str, object] = {
        "status": "active",
        "json_contract_version": 1,
        "criteria": criteria,
        "suppression_policy": suppression_policy,
        "holdout": holdout,
        "household_dedup": household_dedup,
        "treatment_state": treatment_state,
        "treatment_materialization_id": materialization_id,
        "treatment_algorithm_version": "campaign-treatment-v2",
        "treatment_contract_fingerprint": contract_fingerprint,
        "treatment_fingerprint": treatment_fingerprint,
        "treatment_source_snapshot_id": source_snapshot_id,
        "treatment_delta_version": 17,
        "treatment_assignment_digest": assignment_digest,
        "treatment_candidate_count": 12,
        "treatment_selected_primary_count": 10,
        "treatment_count": 9,
        "treatment_holdout_count": 1,
        "treatment_materialized_at": datetime.now(UTC),
        "approval_id": approval_id,
        "borrower_id": borrower_id,
        "action": "approve",
        "actor_email": "skyler@entrada.ai",
        "offer_code": offer_code,
        "campaign_id": campaign_id,
        "variant_name": "Benefit-led",
        "channel": "email",
        "decision_intent": intent_text,
        "decision_payload_hash": intent_hash,
    }
    proof = CampaignActivationProof(
        campaign_id=campaign_id,
        channel="email",
        offer_code=offer_code,
        materialization_id=materialization_id,
        delta_version=17,
        treatment_fingerprint=treatment_fingerprint,
        suppression_policy=suppression_policy,
        decision_intent=intent_text,
        decision_payload_hash=intent_hash,
    )
    return row, proof


class _Result:
    def __init__(self, row: dict[str, object] | None) -> None:
        self._row = row

    def fetchone(self) -> dict[str, object] | None:
        return self._row


class _Cursor:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn
        self._row: dict[str, object] | None = None

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def execute(self, sql: str, params: dict[str, object]) -> None:
        self.conn.executions.append((sql, params))
        if "FOR SHARE OF c" in sql:
            self._row = self.conn.campaign_row if self.conn.campaign_active else None
        elif "ORDER BY decided_at DESC, approval_id::text DESC" in sql:
            self._row = self.conn.latest_decision or {
                "approval_id": params["approval_id"],
                "action": "approve",
            }
        elif "FOR UPDATE OF o" in sql:
            self._row = self.conn._activation_row() if self.conn.insert_params else None
        elif "INSERT INTO mip_app.activation_outbox" in sql:
            self.conn.insert_params = dict(params)
            self._row = {"activation_id": params["activation_id"]}

    def fetchone(self) -> dict[str, object] | None:
        return self._row


class _Conn:
    def __init__(self, *, campaign_active: bool = True) -> None:
        self.campaign_active = campaign_active
        self.campaign_row: dict[str, object] | None = None
        self.executions: list[tuple[str, dict[str, object]]] = []
        self.insert_params: dict[str, object] | None = None
        self.audit_params: dict[str, object] | None = None
        self.latest_decision: dict[str, object] | None = None
        self.in_transaction = False

    def __enter__(self) -> _Conn:
        self.in_transaction = True
        return self

    def __exit__(self, *_exc: object) -> None:
        self.in_transaction = False
        return None

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def execute(self, sql: str, params: dict[str, object]) -> _Result:
        self.executions.append((sql, params))
        if "UPDATE mip_app.activation_outbox" in sql:
            assert self.insert_params is not None
            self.insert_params["status"] = params["status"]
            self.insert_params["delivery_metadata"] = params["delivery_metadata"]
            return _Result(None)
        if "FOR UPDATE OF o" in sql:
            return _Result(self._activation_row() if self.insert_params else None)
        if "INSERT INTO mip_app.action_audit" in sql:
            self.audit_params = dict(params)
            return _Result(
                {
                    "audit_id": uuid4(),
                    "audit_sequence": 1,
                    "event_at": datetime.now(UTC),
                }
            )
        if "FROM mip_app.activation_outbox" in sql and "activation_id" in params:
            return _Result(self._activation_row())
        return _Result(None)

    def _activation_row(self) -> dict[str, object]:
        assert self.insert_params is not None
        now = datetime.now(UTC)
        return {
            "activation_id": self.insert_params["activation_id"],
            "destination_key": self.insert_params["destination_key"],
            "destination_type": "salesforce",
            "destination_display_name": "Salesforce CRM",
            "destination_status": "connected",
            "entity_type": self.insert_params["entity_type"],
            "entity_id": self.insert_params["entity_id"],
            "borrower_id": self.insert_params["borrower_id"],
            "campaign_id": self.insert_params["campaign_id"],
            "approval_id": self.insert_params["approval_id"],
            "offer_code": self.insert_params["offer_code"],
            "channel": self.insert_params["channel"],
            "status": self.insert_params["status"],
            "request_id": self.insert_params["request_id"],
            "created_by": self.insert_params["created_by"],
            "payload_json": self.insert_params["payload_json"],
            "delivery_metadata": self.insert_params["delivery_metadata"],
            "created_at": now,
            "updated_at": now,
        }


class _Client:
    def __init__(self, *, campaign_active: bool = True) -> None:
        self.conn = _Conn(campaign_active=campaign_active)

    def fetchone(
        self,
        sql: str,
        params: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        if "FROM mip_app.activation_outbox" in sql and self.conn.insert_params:
            if "WHERE o.request_id" in sql and (
                params is None
                or params.get("request_id") != self.conn.insert_params["request_id"]
            ):
                return None
            return self.conn._activation_row()
        return None

    def fetchall(
        self, _sql: str, _params: dict[str, object] | None = None, *, limit: int | None = None
    ) -> list[dict[str, object]]:
        return []

    def transaction(self) -> _Conn:
        return self.conn


class _ProofClient(_Client):
    def __init__(self, campaign_row: dict[str, object]) -> None:
        super().__init__()
        self.campaign_row = campaign_row

    def fetchone(
        self,
        sql: str,
        _params: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        if "FROM mip_app.approvals AS a" in sql:
            return self.campaign_row
        return None


class _BrokenClient(_Client):
    def transaction(self) -> _Conn:
        raise RuntimeError("raw driver failure")


class _RequestConflictConn(_Conn):
    def __init__(self, conflict_row: dict[str, object]) -> None:
        super().__init__()
        self.conflict_row = conflict_row

    def cursor(self) -> _Cursor:
        conn = self

        class _ConflictCursor(_Cursor):
            def execute(self, sql: str, params: dict[str, object]) -> None:
                conn.executions.append((sql, params))
                if "ORDER BY decided_at DESC, approval_id::text DESC" in sql:
                    self._row = {
                        "approval_id": params["approval_id"],
                        "action": "approve",
                    }
                elif "INSERT INTO mip_app.activation_outbox" in sql:
                    conn.insert_params = dict(params)
                    self._row = None

        return _ConflictCursor(self)

    def execute(self, sql: str, params: dict[str, object]) -> _Result:
        self.executions.append((sql, params))
        if "WHERE o.request_id = %(request_id)s" in sql:
            return _Result(self.conflict_row)
        return _Result(None)


class _RequestConflictClient(_Client):
    def __init__(self, conflict_row: dict[str, object]) -> None:
        self.conn = _RequestConflictConn(conflict_row)


class _TreatmentMembership:
    def __init__(self, result: bool) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def is_campaign_treatment_member(self, **kwargs: object) -> bool:
        self.calls.append(dict(kwargs))
        return self.result


def test_campaign_activation_proof_accepts_exact_ready_manifest_and_approval_binding() -> None:
    approval_id = str(uuid4())
    campaign_id = str(uuid4())
    borrower_id = mock_data.BORROWERS[0].borrower_id
    campaign_row, expected = _campaign_proof_material(
        approval_id=approval_id,
        borrower_id=borrower_id,
        campaign_id=campaign_id,
    )
    store = ActivationStateStore(client=_ProofClient(campaign_row))  # type: ignore[arg-type]

    proof = store.campaign_activation_proof_for_approval(
        approval_id=approval_id,
        borrower_id=borrower_id,
        campaign_id=campaign_id,
    )

    assert proof == expected


@pytest.mark.parametrize(
    ("treatment_state", "approval_fingerprint"),
    [
        ("legacy_unbound", None),
        ("ready", "d" * 64),
    ],
)
def test_campaign_activation_proof_rejects_unbound_or_fingerprint_mismatch(
    treatment_state: str,
    approval_fingerprint: str | None,
) -> None:
    approval_id = str(uuid4())
    campaign_id = str(uuid4())
    borrower_id = mock_data.BORROWERS[0].borrower_id
    campaign_row, _proof = _campaign_proof_material(
        approval_id=approval_id,
        borrower_id=borrower_id,
        campaign_id=campaign_id,
        treatment_state=treatment_state,
        approval_treatment_fingerprint=approval_fingerprint,
    )
    store = ActivationStateStore(client=_ProofClient(campaign_row))  # type: ignore[arg-type]

    with pytest.raises(PermissionError, match="valid saved treatment proof"):
        store.campaign_activation_proof_for_approval(
            approval_id=approval_id,
            borrower_id=borrower_id,
            campaign_id=campaign_id,
        )


def test_campaign_activation_proof_rejects_internally_inconsistent_manifest_fingerprint() -> None:
    approval_id = str(uuid4())
    campaign_id = str(uuid4())
    borrower_id = mock_data.BORROWERS[0].borrower_id
    campaign_row, _proof = _campaign_proof_material(
        approval_id=approval_id,
        borrower_id=borrower_id,
        campaign_id=campaign_id,
    )
    campaign_row["treatment_fingerprint"] = "e" * 64
    store = ActivationStateStore(client=_ProofClient(campaign_row))  # type: ignore[arg-type]

    with pytest.raises(PermissionError, match="valid saved treatment proof"):
        store.campaign_activation_proof_for_approval(
            approval_id=approval_id,
            borrower_id=borrower_id,
            campaign_id=campaign_id,
        )


@pytest.mark.parametrize(
    ("campaign_active", "treatment_member", "expected"),
    [
        pytest.param(True, True, True, id="active-treatment-member"),
        pytest.param(False, True, False, id="archived-campaign"),
        pytest.param(True, False, False, id="holdout-or-dedup-excluded"),
    ],
)
def test_campaign_delivery_guard_holds_transaction_through_delivery_body(
    campaign_active: bool,
    treatment_member: bool,
    expected: bool,
) -> None:
    client = _Client(campaign_active=True)
    store = ActivationStateStore(client=client)  # type: ignore[arg-type]
    borrower = mock_data.BORROWERS[0]
    approval_id = str(uuid4())
    campaign_id = str(uuid4())
    campaign_row, campaign_proof = _campaign_proof_material(
        approval_id=approval_id,
        borrower_id=borrower.borrower_id,
        campaign_id=campaign_id,
    )
    client.conn.campaign_row = campaign_row
    staged = store.stage_borrower(
        borrower=borrower,
        destination=_destination(status="connected"),
        payload=ActivationStageRequest(
            borrower_id=borrower.borrower_id,
            destination_key="salesforce_crm",
            channel="email",
            approval_id=approval_id,
            request_id=str(uuid4()),
        ),
        approved_decision=_approved_decision(
            approval_id,
            borrower.borrower_id,
            campaign_id=campaign_id,
        ),
        campaign_proof=campaign_proof,
        actor="skyler@entrada.ai",
    ).activation
    client.conn.campaign_active = campaign_active

    membership = _TreatmentMembership(treatment_member)
    with store.delivery_guard(
        activation_id=staged.activation_id,
        lead_repo=membership,  # type: ignore[arg-type]
    ) as guard:
        assert guard.should_deliver is expected
        assert client.conn.in_transaction is True
        if expected:
            refreshed = guard.update_delivery_state(
                activation_id=staged.activation_id,
                status="delivered",
                delivery_metadata={"delivered": True, "salesforce_id": "00T-stable"},
            )
            assert refreshed is not None
            assert refreshed.status == "delivered"
            assert guard.should_deliver is False
        else:
            assert guard.activation.status == "cancelled"
            assert (guard.activation.delivery_metadata or {}).get(
                "cancelled_reason"
            ) in {
                "campaign_not_active_or_proof_invalid",
                "campaign_borrower_not_treatment_member",
            }

    assert client.conn.in_transaction is False
    assert any("FOR SHARE OF c" in sql for sql, _params in client.conn.executions)
    assert any("FOR UPDATE OF o" in sql for sql, _params in client.conn.executions)
    if campaign_active:
        assert len(membership.calls) == 1
    else:
        assert membership.calls == []


def test_campaign_delivery_guard_rejects_outbox_channel_substitution() -> None:
    client = _Client()
    store = ActivationStateStore(client=client)  # type: ignore[arg-type]
    borrower = mock_data.BORROWERS[0]
    approval_id = str(uuid4())
    campaign_id = str(uuid4())
    campaign_row, campaign_proof = _campaign_proof_material(
        approval_id=approval_id,
        borrower_id=borrower.borrower_id,
        campaign_id=campaign_id,
    )
    client.conn.campaign_row = campaign_row
    staged = store.stage_borrower(
        borrower=borrower,
        destination=_destination(status="connected"),
        payload=ActivationStageRequest(
            borrower_id=borrower.borrower_id,
            destination_key="salesforce_crm",
            channel="email",
            approval_id=approval_id,
            request_id=str(uuid4()),
        ),
        approved_decision=_approved_decision(
            approval_id,
            borrower.borrower_id,
            campaign_id=campaign_id,
        ),
        campaign_proof=campaign_proof,
        actor="skyler@entrada.ai",
    ).activation
    assert client.conn.insert_params is not None
    client.conn.insert_params["channel"] = "sms"
    membership = _TreatmentMembership(True)

    with store.delivery_guard(
        activation_id=staged.activation_id,
        lead_repo=membership,  # type: ignore[arg-type]
    ) as guard:
        assert guard.should_deliver is False
        assert guard.activation.status == "cancelled"
        assert (guard.activation.delivery_metadata or {}).get(
            "cancelled_reason"
        ) == "campaign_approval_channel_mismatch"

    assert membership.calls == []


def test_campaign_delivery_guard_rejects_outbox_offer_substitution() -> None:
    client = _Client()
    store = ActivationStateStore(client=client)  # type: ignore[arg-type]
    borrower = mock_data.BORROWERS[0]
    approval_id = str(uuid4())
    campaign_id = str(uuid4())
    campaign_row, campaign_proof = _campaign_proof_material(
        approval_id=approval_id,
        borrower_id=borrower.borrower_id,
        campaign_id=campaign_id,
        offer_code="refi",
    )
    client.conn.campaign_row = campaign_row
    staged = store.stage_borrower(
        borrower=borrower,
        destination=_destination(status="connected"),
        payload=ActivationStageRequest(
            borrower_id=borrower.borrower_id,
            destination_key="salesforce_crm",
            channel="email",
            approval_id=approval_id,
            request_id=str(uuid4()),
        ),
        approved_decision=_approved_decision(
            approval_id,
            borrower.borrower_id,
            campaign_id=campaign_id,
            offer_code="refi",
        ),
        campaign_proof=campaign_proof,
        actor="skyler@entrada.ai",
    ).activation
    assert client.conn.insert_params is not None
    client.conn.insert_params["offer_code"] = "heloc"
    membership = _TreatmentMembership(True)

    with store.delivery_guard(
        activation_id=staged.activation_id,
        lead_repo=membership,  # type: ignore[arg-type]
    ) as guard:
        assert guard.should_deliver is False
        assert guard.activation.status == "cancelled"
        assert (guard.activation.delivery_metadata or {}).get(
            "cancelled_reason"
        ) == "campaign_approval_offer_mismatch"

    assert membership.calls == []


def test_campaign_delivery_guard_cancels_when_newer_reject_wins_borrower_lock() -> None:
    client = _Client()
    store = ActivationStateStore(client=client)  # type: ignore[arg-type]
    borrower = mock_data.BORROWERS[0]
    approval_id = str(uuid4())
    campaign_id = str(uuid4())
    campaign_row, campaign_proof = _campaign_proof_material(
        approval_id=approval_id,
        borrower_id=borrower.borrower_id,
        campaign_id=campaign_id,
    )
    client.conn.campaign_row = campaign_row
    staged = store.stage_borrower(
        borrower=borrower,
        destination=_destination(status="connected"),
        payload=ActivationStageRequest(
            borrower_id=borrower.borrower_id,
            destination_key="salesforce_crm",
            channel="email",
            approval_id=approval_id,
            request_id=str(uuid4()),
        ),
        approved_decision=_approved_decision(
            approval_id,
            borrower.borrower_id,
            campaign_id=campaign_id,
        ),
        campaign_proof=campaign_proof,
        actor="skyler@entrada.ai",
    ).activation
    client.conn.latest_decision = {
        "approval_id": str(uuid4()),
        "action": "reject",
    }
    membership = _TreatmentMembership(True)

    with store.delivery_guard(
        activation_id=staged.activation_id,
        lead_repo=membership,  # type: ignore[arg-type]
    ) as guard:
        assert guard.should_deliver is False
        assert guard.activation.status == "cancelled"
        assert (guard.activation.delivery_metadata or {}).get(
            "cancelled_reason"
        ) == "approval_not_current"

    assert membership.calls == []
    statements = [sql for sql, _params in client.conn.executions]
    borrower_lock = next(
        index
        for index, sql in enumerate(statements)
        if "mip_outreach_decision:" in sql
    )
    latest_read = next(
        index
        for index, sql in enumerate(statements)
        if "ORDER BY decided_at DESC, approval_id::text DESC" in sql
    )
    campaign_lock = next(
        index for index, sql in enumerate(statements) if "FOR SHARE OF c" in sql
    )
    assert borrower_lock < latest_read < campaign_lock


def test_stage_borrower_writes_sanitized_outbox_payload_and_audit_metadata() -> None:
    borrower = mock_data.BORROWERS[0]
    destination = _destination()
    request_id = str(uuid4())
    approval_id = str(uuid4())
    client = _Client()
    store = ActivationStateStore(client=client)  # type: ignore[arg-type]

    result = store.stage_borrower(
        borrower=borrower,
        destination=destination,
        payload=ActivationStageRequest(
            borrower_id=borrower.borrower_id,
            destination_key=destination.destination_key,
            offer_code="refi",
            channel="email",
            approval_id=approval_id,
            request_id=request_id,
        ),
        approved_decision=_approved_decision(approval_id, borrower.borrower_id),
        actor="skyler@entrada.ai",
    )

    assert result.activation.status == "dry_run"
    assert result.activation.destination_key == "salesforce_crm"
    assert result.activation.approval_id == approval_id
    assert result.audit_event_id
    assert client.conn.insert_params is not None
    payload = json.loads(str(client.conn.insert_params["payload_json"]))
    assert payload["borrower_id"] == borrower.borrower_id
    assert payload["property_ref"].startswith("clip_demo_")
    assert payload["source"] == "mip.activation_outbox"
    assert "display_name" not in payload
    assert "owner_link_id" not in payload
    assert "subject_property" not in payload
    serialized_payload = json.dumps(payload).lower()
    assert "@" not in serialized_payload
    assert "phone" not in serialized_payload

    assert client.conn.audit_params is not None
    metadata = json.loads(str(client.conn.audit_params["metadata"]))
    assert metadata["action"] == "activation.stage"
    assert metadata["activation_status"] == "dry_run"
    assert metadata["destination_key"] == "salesforce_crm"
    assert metadata["borrower_id"] == borrower.borrower_id
    assert metadata["approval_id"] == approval_id
    assert client.conn.audit_params["request_id"] == request_id


def test_stage_borrower_derives_offer_and_campaign_from_approved_decision() -> None:
    borrower = mock_data.BORROWERS[0]
    destination = _destination()
    request_id = str(uuid4())
    approval_id = str(uuid4())
    campaign_id = str(uuid4())
    client = _Client()
    store = ActivationStateStore(client=client)  # type: ignore[arg-type]
    campaign_row, campaign_proof = _campaign_proof_material(
        approval_id=approval_id,
        borrower_id=borrower.borrower_id,
        campaign_id=campaign_id,
        offer_code="heloc",
    )
    client.conn.campaign_row = campaign_row

    result = store.stage_borrower(
        borrower=borrower,
        destination=destination,
        payload=ActivationStageRequest(
            borrower_id=borrower.borrower_id,
            destination_key=destination.destination_key,
            channel="email",
            approval_id=approval_id,
            request_id=request_id,
        ),
        approved_decision=_approved_decision(
            approval_id,
            borrower.borrower_id,
            offer_code="heloc",
            campaign_id=campaign_id,
        ),
        campaign_proof=campaign_proof,
        actor="skyler@entrada.ai",
    )

    assert result.activation.offer_code == "heloc"
    assert result.activation.campaign_id == campaign_id
    assert result.activation.entity_id == campaign_id
    assert client.conn.insert_params is not None
    assert client.conn.insert_params["offer_code"] == "heloc"
    assert client.conn.insert_params["campaign_id"] == campaign_id
    payload = json.loads(str(client.conn.insert_params["payload_json"]))
    assert payload["offer_code"] == "heloc"
    assert payload["recommended_offer"] == "Home-equity line review"
    assert payload["campaign_treatment"] == {
        "campaign_id": campaign_id,
        "materialization_id": campaign_proof.materialization_id,
        "delta_version": 17,
        "treatment_fingerprint": campaign_proof.treatment_fingerprint,
    }
    assert client.conn.audit_params is not None
    metadata = json.loads(str(client.conn.audit_params["metadata"]))
    assert (
        metadata["campaign_treatment_fingerprint"] == campaign_proof.treatment_fingerprint
    )
    statements = [sql for sql, _params in client.conn.executions]
    lock_pos = next(i for i, sql in enumerate(statements) if "FOR SHARE OF c" in sql)
    insert_pos = next(
        i for i, sql in enumerate(statements) if "INSERT INTO mip_app.activation_outbox" in sql
    )
    assert lock_pos < insert_pos


def test_stage_borrower_rejects_inactive_campaign_under_transaction_lock() -> None:
    borrower = mock_data.BORROWERS[0]
    destination = _destination()
    approval_id = str(uuid4())
    campaign_id = str(uuid4())
    client = _Client(campaign_active=False)
    store = ActivationStateStore(client=client)  # type: ignore[arg-type]
    campaign_row, campaign_proof = _campaign_proof_material(
        approval_id=approval_id,
        borrower_id=borrower.borrower_id,
        campaign_id=campaign_id,
    )
    client.conn.campaign_row = campaign_row

    with pytest.raises(PermissionError, match="campaign must be active"):
        store.stage_borrower(
            borrower=borrower,
            destination=destination,
            payload=ActivationStageRequest(
                borrower_id=borrower.borrower_id,
                destination_key=destination.destination_key,
                channel="email",
                approval_id=approval_id,
                request_id=str(uuid4()),
            ),
            approved_decision=_approved_decision(
                approval_id,
                borrower.borrower_id,
                campaign_id=campaign_id,
            ),
            campaign_proof=campaign_proof,
            actor="skyler@entrada.ai",
        )

    statements = [sql for sql, _params in client.conn.executions]
    assert any("FOR SHARE OF c" in sql for sql in statements)
    assert not any("INSERT INTO mip_app.activation_outbox" in sql for sql in statements)


def test_stage_borrower_rejects_approval_proof_changed_before_transaction_lock() -> None:
    borrower = mock_data.BORROWERS[0]
    approval_id = str(uuid4())
    campaign_id = str(uuid4())
    client = _Client()
    store = ActivationStateStore(client=client)  # type: ignore[arg-type]
    campaign_row, campaign_proof = _campaign_proof_material(
        approval_id=approval_id,
        borrower_id=borrower.borrower_id,
        campaign_id=campaign_id,
    )
    campaign_row["decision_payload_hash"] = "d" * 64
    client.conn.campaign_row = campaign_row

    with pytest.raises(PermissionError, match="valid saved treatment proof"):
        store.stage_borrower(
            borrower=borrower,
            destination=_destination(),
            payload=ActivationStageRequest(
                borrower_id=borrower.borrower_id,
                destination_key="salesforce_crm",
                channel="email",
                approval_id=approval_id,
                request_id=str(uuid4()),
            ),
            approved_decision=_approved_decision(
                approval_id,
                borrower.borrower_id,
                campaign_id=campaign_id,
            ),
            campaign_proof=campaign_proof,
            actor="skyler@entrada.ai",
        )

    statements = [sql for sql, _params in client.conn.executions]
    assert any("FOR SHARE OF c" in sql for sql in statements)
    assert not any("INSERT INTO mip_app.activation_outbox" in sql for sql in statements)


def test_stage_borrower_rejects_client_offer_that_differs_from_approval() -> None:
    borrower = mock_data.BORROWERS[0]
    destination = _destination()
    approval_id = str(uuid4())
    store = ActivationStateStore(client=_Client())  # type: ignore[arg-type]

    with pytest.raises(PermissionError, match="offer_code must match"):
        store.stage_borrower(
            borrower=borrower,
            destination=destination,
            payload=ActivationStageRequest(
                borrower_id=borrower.borrower_id,
                destination_key=destination.destination_key,
                offer_code="refi",
                channel="email",
                approval_id=approval_id,
                request_id=str(uuid4()),
            ),
            approved_decision=_approved_decision(
                approval_id, borrower.borrower_id, offer_code="heloc"
            ),
            actor="skyler@entrada.ai",
        )


def test_stage_borrower_rejects_non_campaign_channel_substitution() -> None:
    borrower = mock_data.BORROWERS[0]
    approval_id = str(uuid4())
    store = ActivationStateStore(client=_Client())  # type: ignore[arg-type]

    with pytest.raises(PermissionError, match="channel must match"):
        store.stage_borrower(
            borrower=borrower,
            destination=_destination(),
            payload=ActivationStageRequest(
                borrower_id=borrower.borrower_id,
                destination_key="salesforce_crm",
                channel="sms",
                approval_id=approval_id,
                request_id=str(uuid4()),
            ),
            approved_decision=_approved_decision(
                approval_id,
                borrower.borrower_id,
                channel="email",
            ),
            actor="skyler@entrada.ai",
        )


def test_stage_borrower_rejects_approval_superseded_before_insert() -> None:
    borrower = mock_data.BORROWERS[0]
    approval_id = str(uuid4())
    client = _Client()
    client.conn.latest_decision = {
        "approval_id": str(uuid4()),
        "action": "reject",
    }
    store = ActivationStateStore(client=client)  # type: ignore[arg-type]

    with pytest.raises(PermissionError, match="no longer.*current decision"):
        store.stage_borrower(
            borrower=borrower,
            destination=_destination(status="connected"),
            payload=ActivationStageRequest(
                borrower_id=borrower.borrower_id,
                destination_key="salesforce_crm",
                channel="email",
                approval_id=approval_id,
                request_id=str(uuid4()),
            ),
            approved_decision=_approved_decision(approval_id, borrower.borrower_id),
            actor="skyler@entrada.ai",
        )

    statements = [sql for sql, _params in client.conn.executions]
    lock_index = next(
        index
        for index, sql in enumerate(statements)
        if "mip_outreach_decision:" in sql
    )
    latest_index = next(
        index
        for index, sql in enumerate(statements)
        if "ORDER BY decided_at DESC, approval_id::text DESC" in sql
    )
    assert lock_index < latest_index
    assert not any("INSERT INTO mip_app.activation_outbox" in sql for sql in statements)


def test_stage_borrower_rejects_campaign_channel_substitution() -> None:
    borrower = mock_data.BORROWERS[0]
    approval_id = str(uuid4())
    campaign_id = str(uuid4())
    campaign_row, campaign_proof = _campaign_proof_material(
        approval_id=approval_id,
        borrower_id=borrower.borrower_id,
        campaign_id=campaign_id,
    )
    client = _Client()
    client.conn.campaign_row = campaign_row
    store = ActivationStateStore(client=client)  # type: ignore[arg-type]

    with pytest.raises(PermissionError, match="channel must match"):
        store.stage_borrower(
            borrower=borrower,
            destination=_destination(),
            payload=ActivationStageRequest(
                borrower_id=borrower.borrower_id,
                destination_key="salesforce_crm",
                channel="sms",
                approval_id=approval_id,
                request_id=str(uuid4()),
            ),
            approved_decision=_approved_decision(
                approval_id,
                borrower.borrower_id,
                campaign_id=campaign_id,
                channel="email",
            ),
            campaign_proof=campaign_proof,
            actor="skyler@entrada.ai",
        )


def test_failed_activation_reuses_business_key_across_new_request_id() -> None:
    borrower = mock_data.BORROWERS[0]
    approval_id = str(uuid4())
    client = _Client()
    store = ActivationStateStore(client=client)  # type: ignore[arg-type]
    first = store.stage_borrower(
        borrower=borrower,
        destination=_destination(status="connected"),
        payload=ActivationStageRequest(
            borrower_id=borrower.borrower_id,
            destination_key="salesforce_crm",
            channel="email",
            approval_id=approval_id,
            request_id=str(uuid4()),
        ),
        approved_decision=_approved_decision(approval_id, borrower.borrower_id),
        actor="skyler@entrada.ai",
    )
    assert client.conn.insert_params is not None
    client.conn.insert_params["status"] = "failed"

    retried = store.stage_borrower(
        borrower=borrower,
        destination=_destination(status="connected"),
        payload=ActivationStageRequest(
            borrower_id=borrower.borrower_id,
            destination_key="salesforce_crm",
            channel="email",
            approval_id=approval_id,
            request_id=str(uuid4()),
        ),
        approved_decision=_approved_decision(approval_id, borrower.borrower_id),
        actor="skyler@entrada.ai",
    )

    assert retried.activation.activation_id == first.activation.activation_id
    assert retried.activation.status == "failed"
    assert retried.audit_event_id is None
    assert sum(
        "INSERT INTO mip_app.activation_outbox" in sql
        for sql, _params in client.conn.executions
    ) == 1


def test_stage_borrower_uses_borrower_offer_when_approved_offer_is_null() -> None:
    borrower = mock_data.BORROWERS[0]
    destination = _destination()
    approval_id = str(uuid4())
    client = _Client()
    store = ActivationStateStore(client=client)  # type: ignore[arg-type]

    result = store.stage_borrower(
        borrower=borrower,
        destination=destination,
        payload=ActivationStageRequest(
            borrower_id=borrower.borrower_id,
            destination_key=destination.destination_key,
            channel="email",
            approval_id=approval_id,
            request_id=str(uuid4()),
        ),
        approved_decision=_approved_decision(approval_id, borrower.borrower_id, offer_code=None),
        actor="skyler@entrada.ai",
    )

    assert result.activation.offer_code == borrower.recommended_offer_code
    assert client.conn.insert_params is not None
    payload = json.loads(str(client.conn.insert_params["payload_json"]))
    assert payload["offer_code"] == borrower.recommended_offer_code


def test_stage_borrower_rejects_client_offer_when_approved_offer_is_null() -> None:
    borrower = mock_data.BORROWERS[0]
    destination = _destination()
    approval_id = str(uuid4())
    store = ActivationStateStore(client=_Client())  # type: ignore[arg-type]

    with pytest.raises(PermissionError, match="offer_code must match"):
        store.stage_borrower(
            borrower=borrower,
            destination=destination,
            payload=ActivationStageRequest(
                borrower_id=borrower.borrower_id,
                destination_key=destination.destination_key,
                offer_code="heloc",
                channel="email",
                approval_id=approval_id,
                request_id=str(uuid4()),
            ),
            approved_decision=_approved_decision(
                approval_id, borrower.borrower_id, offer_code=None
            ),
            actor="skyler@entrada.ai",
        )


def test_stage_borrower_rejects_mismatched_approved_decision_row() -> None:
    borrower = mock_data.BORROWERS[0]
    destination = _destination()
    approval_id = str(uuid4())
    store = ActivationStateStore(client=_Client())  # type: ignore[arg-type]

    with pytest.raises(PermissionError, match="approved decision belongs to a different borrower"):
        store.stage_borrower(
            borrower=borrower,
            destination=destination,
            payload=ActivationStageRequest(
                borrower_id=borrower.borrower_id,
                destination_key=destination.destination_key,
                channel="email",
                approval_id=approval_id,
                request_id=str(uuid4()),
            ),
            approved_decision=_approved_decision(approval_id, "B-OTHER"),
            actor="skyler@entrada.ai",
        )


def test_stage_borrower_normalizes_raw_transaction_errors() -> None:
    borrower = mock_data.BORROWERS[0]
    destination = _destination()
    approval_id = str(uuid4())
    store = ActivationStateStore(client=_BrokenClient())  # type: ignore[arg-type]

    with pytest.raises(LakebaseError, match="activation staging failed"):
        store.stage_borrower(
            borrower=borrower,
            destination=destination,
            payload=ActivationStageRequest(
                borrower_id=borrower.borrower_id,
                destination_key=destination.destination_key,
                offer_code="refi",
                channel="email",
                approval_id=approval_id,
                request_id=str(uuid4()),
            ),
            approved_decision=_approved_decision(approval_id, borrower.borrower_id),
            actor="skyler@entrada.ai",
        )


def test_stage_borrower_rechecks_conflicting_request_id_after_insert_race() -> None:
    borrower = mock_data.BORROWERS[0]
    destination = _destination()
    request_id = str(uuid4())
    approval_id = str(uuid4())
    now = datetime.now(UTC)
    conflict_row = {
        "activation_id": uuid4(),
        "destination_key": destination.destination_key,
        "destination_type": destination.destination_type,
        "destination_display_name": destination.display_name,
        "destination_status": destination.status,
        "entity_type": "borrower",
        "entity_id": "B-OTHER",
        "borrower_id": "B-OTHER",
        "campaign_id": None,
        "approval_id": uuid4(),
        "offer_code": "refi",
        "channel": "email",
        "status": "dry_run",
        "request_id": request_id,
        "created_by": "skyler@entrada.ai",
        "payload_json": "{}",
        "delivery_metadata": "{}",
        "created_at": now,
        "updated_at": now,
    }
    store = ActivationStateStore(client=_RequestConflictClient(conflict_row))  # type: ignore[arg-type]

    with pytest.raises(PermissionError, match="different activation"):
        store.stage_borrower(
            borrower=borrower,
            destination=destination,
            payload=ActivationStageRequest(
                borrower_id=borrower.borrower_id,
                destination_key=destination.destination_key,
                offer_code="refi",
                channel="email",
                approval_id=approval_id,
                request_id=request_id,
            ),
            approved_decision=_approved_decision(approval_id, borrower.borrower_id),
            actor="skyler@entrada.ai",
        )


def test_stage_borrower_rechecks_conflicting_request_id_content_after_insert_race() -> None:
    borrower = mock_data.BORROWERS[0]
    destination = _destination()
    request_id = str(uuid4())
    approval_id = str(uuid4())
    now = datetime.now(UTC)
    conflict_row = {
        "activation_id": uuid4(),
        "destination_key": destination.destination_key,
        "destination_type": destination.destination_type,
        "destination_display_name": destination.display_name,
        "destination_status": destination.status,
        "entity_type": "borrower",
        "entity_id": borrower.borrower_id,
        "borrower_id": borrower.borrower_id,
        "campaign_id": None,
        "approval_id": approval_id,
        "offer_code": "heloc",
        "channel": "email",
        "status": "dry_run",
        "request_id": request_id,
        "created_by": "skyler@entrada.ai",
        "payload_json": "{}",
        "delivery_metadata": "{}",
        "created_at": now,
        "updated_at": now,
    }
    store = ActivationStateStore(client=_RequestConflictClient(conflict_row))  # type: ignore[arg-type]

    with pytest.raises(PermissionError, match="different activation"):
        store.stage_borrower(
            borrower=borrower,
            destination=destination,
            payload=ActivationStageRequest(
                borrower_id=borrower.borrower_id,
                destination_key=destination.destination_key,
                offer_code="refi",
                channel="email",
                approval_id=approval_id,
                request_id=request_id,
            ),
            approved_decision=_approved_decision(
                approval_id, borrower.borrower_id, offer_code="refi"
            ),
            actor="skyler@entrada.ai",
        )
