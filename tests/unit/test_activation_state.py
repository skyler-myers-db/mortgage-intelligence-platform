from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from backend.schemas.activation import ActivationDestination, ActivationStageRequest
from backend.services.activation_state import ActivationStateStore
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
) -> dict[str, object]:
    return {
        "approval_id": approval_id,
        "borrower_id": borrower_id,
        "action": "approve",
        "actor_email": "skyler@entrada.ai",
        "offer_code": offer_code,
        "campaign_id": campaign_id,
    }


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
        if "INSERT INTO mip_app.activation_outbox" in sql:
            self.conn.insert_params = dict(params)
            self._row = {"activation_id": params["activation_id"]}

    def fetchone(self) -> dict[str, object] | None:
        return self._row


class _Conn:
    def __init__(self) -> None:
        self.executions: list[tuple[str, dict[str, object]]] = []
        self.insert_params: dict[str, object] | None = None
        self.audit_params: dict[str, object] | None = None

    def __enter__(self) -> _Conn:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def execute(self, sql: str, params: dict[str, object]) -> _Result:
        self.executions.append((sql, params))
        if "INSERT INTO mip_app.action_audit" in sql:
            self.audit_params = dict(params)
            return _Result({"audit_id": uuid4(), "event_at": datetime.now(UTC)})
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
            "destination_status": "not_configured",
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
    def __init__(self) -> None:
        self.conn = _Conn()

    def fetchone(self, _sql: str, _params: dict[str, object] | None = None) -> None:
        return None

    def fetchall(self, _sql: str, _params: dict[str, object] | None = None, *, limit: int | None = None) -> list[dict[str, object]]:
        return []

    def transaction(self) -> _Conn:
        return self.conn


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
                if "INSERT INTO mip_app.activation_outbox" in sql:
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
    assert payload["recommended_offer"] == "HELOC"


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
            approved_decision=_approved_decision(approval_id, borrower.borrower_id, offer_code="heloc"),
            actor="skyler@entrada.ai",
        )


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
            approved_decision=_approved_decision(approval_id, borrower.borrower_id, offer_code=None),
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
            approved_decision=_approved_decision(approval_id, borrower.borrower_id, offer_code="refi"),
            actor="skyler@entrada.ai",
        )
