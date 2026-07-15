from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.main import app
from backend.schemas.activation import (
    ActivationDestination,
    ActivationOutboxItem,
)
from backend.services.activation_state import ActivationWriteResult, get_activation_state_store
from backend.services.lakebase import LakebaseError
from backend.services.repositories import get_borrower_repository
from backend.services.sales_state import get_sales_state_store
from tests.fixtures import mock_population as mock_data

client = TestClient(app)
client.headers.update({"X-Forwarded-Email": "skyler@entrada.ai"})


def _destination(status: str = "not_configured", allowed_actions: list[str] | None = None) -> ActivationDestination:
    return ActivationDestination(
        destination_key="salesforce_crm",
        destination_type="salesforce",
        display_name="Salesforce CRM",
        status=status,  # type: ignore[arg-type]
        allowed_actions=allowed_actions or ["stage_lead"],
        updated_at="2026-06-01T00:00:00Z",
    )


def _outbox_item(
    *,
    borrower_id: str,
    destination: ActivationDestination,
    request_id: str,
    approval_id: str,
    offer_code: str = "refi",
    campaign_id: str | None = None,
) -> ActivationOutboxItem:
    now = datetime.now(UTC).isoformat()
    status = "staged" if destination.status == "connected" else "dry_run"
    return ActivationOutboxItem(
        activation_id=str(uuid4()),
        destination_key=destination.destination_key,
        destination_type=destination.destination_type,
        destination_display_name=destination.display_name,
        destination_status=destination.status,
        entity_type="borrower",
        entity_id=campaign_id or borrower_id,
        borrower_id=borrower_id,
        campaign_id=campaign_id,
        approval_id=approval_id,
        offer_code=offer_code,
        channel="email",
        status=status,
        request_id=request_id,
        created_by="skyler@entrada.ai",
        created_at=now,
        updated_at=now,
    )


def _approval_decision(
    approval_id: str,
    borrower_id: str,
    *,
    offer_code: str = "refi",
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


class _ActivationStore:
    def __init__(
        self,
        destination: ActivationDestination | None = None,
        *,
        approved_decisions: dict[str, dict[str, object]] | None = None,
        fail_stage: bool = False,
        campaign_status: str | None = "active",
    ) -> None:
        self.destination = destination or _destination()
        self.outbox: list[ActivationOutboxItem] = []
        self.stage_calls = 0
        self.fail_stage = fail_stage
        self.approved_decisions = approved_decisions or {}
        self.campaign_status = campaign_status
        self.last_approved_decision: dict[str, object] | None = None
        self.delivery_guard_entered = False
        self.activation: ActivationOutboxItem | None = None
        self.should_deliver = False

    def list_destinations(self) -> list[ActivationDestination]:
        return [self.destination]

    def get_destination(self, destination_key: str) -> ActivationDestination | None:
        if destination_key == self.destination.destination_key:
            return self.destination
        return None

    def list_outbox(
        self,
        *,
        borrower_id: str | None = None,
        destination_key: str | None = None,
        limit: int = 25,
    ) -> list[ActivationOutboxItem]:
        rows = self.outbox
        if borrower_id:
            rows = [row for row in rows if row.borrower_id == borrower_id]
        if destination_key:
            rows = [row for row in rows if row.destination_key == destination_key]
        return rows[:limit]

    def approved_decision_for(self, *, approval_id: str, borrower_id: str) -> dict[str, object] | None:
        decision = self.approved_decisions.get(approval_id)
        if decision and decision.get("borrower_id") == borrower_id and decision.get("action") == "approve":
            return decision
        return None

    def campaign_status_for_approval(
        self,
        *,
        approval_id: str,
        borrower_id: str,
        campaign_id: str,
    ) -> str | None:
        decision = self.approved_decision_for(
            approval_id=approval_id,
            borrower_id=borrower_id,
        )
        if decision is None or str(decision.get("campaign_id") or "") != campaign_id:
            return None
        return self.campaign_status

    @contextmanager
    def delivery_guard(
        self,
        *,
        activation: ActivationOutboxItem,
    ) -> Iterator[_ActivationStore]:
        self.delivery_guard_entered = True
        self.activation = activation
        campaign_active = not activation.campaign_id or (
            self.campaign_status_for_approval(
                approval_id=str(activation.approval_id or ""),
                borrower_id=str(activation.borrower_id or ""),
                campaign_id=activation.campaign_id,
            )
            == "active"
        )
        self.should_deliver = campaign_active and activation.status in {"staged", "failed"}
        yield self

    def update_delivery_state(
        self,
        *,
        activation_id: str,
        status: str,
        delivery_metadata: dict[str, object],
    ) -> ActivationOutboxItem | None:
        if self.activation is None or self.activation.activation_id != activation_id:
            return None
        self.activation = self.activation.model_copy(
            update={"status": status, "delivery_metadata": delivery_metadata}
        )
        self.should_deliver = False
        return self.activation

    def stage_borrower(self, *, borrower, destination, payload, approved_decision, actor: str) -> ActivationWriteResult:
        self.stage_calls += 1
        if self.fail_stage:
            raise LakebaseError("activation staging failed")
        self.last_approved_decision = dict(approved_decision)
        offer_code = str(approved_decision.get("offer_code") or "refi")
        campaign_id = str(approved_decision["campaign_id"]) if approved_decision.get("campaign_id") else None
        item = _outbox_item(
            borrower_id=payload.borrower_id,
            destination=destination,
            request_id=payload.request_id,
            approval_id=payload.approval_id,
            offer_code=offer_code,
            campaign_id=campaign_id,
        )
        self.outbox.insert(0, item)
        return ActivationWriteResult(activation=item, audit_event_id=str(uuid4()))


class _Borrowers:
    def __init__(self, borrower=None) -> None:
        self.borrower = borrower or mock_data.BORROWERS[0]

    def get(self, borrower_id: str):
        if borrower_id == self.borrower.borrower_id:
            return self.borrower
        return None


class _SalesState:
    def __init__(self, approval_status: str = "approved", approval_id: str | None = None) -> None:
        self.approval_status = approval_status
        self.approval_id = approval_id or str(uuid4())

    def lifecycle_for(self, borrower_id: str) -> dict[str, object]:
        return {
            "borrower_id": borrower_id,
            "approval_status": self.approval_status,
            "outreach_status": "queued" if self.approval_status == "approved" else "none",
            "approval_id": self.approval_id if self.approval_status == "approved" else None,
            "approved_at": "2026-06-01T00:00:00Z" if self.approval_status == "approved" else None,
            "outreach_at": None,
            "synced_at": "2026-06-01T00:00:00Z",
        }


@contextmanager
def _activation_overrides(
    *,
    store: _ActivationStore | None = None,
    borrowers: _Borrowers | None = None,
    sales_state: _SalesState | None = None,
) -> Iterator[_ActivationStore]:
    borrowers = borrowers or _Borrowers()
    sales_state = sales_state or _SalesState()
    store = store or _ActivationStore()
    if sales_state.approval_status == "approved" and sales_state.approval_id:
        store.approved_decisions.setdefault(
            sales_state.approval_id,
            _approval_decision(sales_state.approval_id, borrowers.borrower.borrower_id),
        )
    deps = {
        get_activation_state_store: lambda: store,
        get_borrower_repository: lambda: borrowers,
        get_sales_state_store: lambda: sales_state,
    }
    previous = {dep: app.dependency_overrides.get(dep) for dep in deps}
    app.dependency_overrides.update(deps)
    try:
        yield store
    finally:
        for dep, original in previous.items():
            if original is None:
                app.dependency_overrides.pop(dep, None)
            else:
                app.dependency_overrides[dep] = original


def test_activation_summary_lists_destinations_without_secrets() -> None:
    with _activation_overrides() as store:
        destinations = client.get("/api/activation/destinations")
        outbox = client.get("/api/activation/outbox")
        response = client.get("/api/activation/summary")

    assert destinations.status_code == 200
    assert outbox.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["destinations"][0]["destination_key"] == "salesforce_crm"
    assert "secret" not in str(body).lower()
    assert store.stage_calls == 0


def test_stage_activation_requires_approved_eligible_borrower_and_writes_outbox() -> None:
    borrower_id = mock_data.BORROWERS[0].borrower_id
    request_id = str(uuid4())
    approval_id = str(uuid4())

    with _activation_overrides(sales_state=_SalesState(approval_id=approval_id)) as store:
        response = client.post(
            "/api/activation/stage",
            json={
                "borrower_id": borrower_id,
                "destination_key": "salesforce_crm",
                "offer_code": "refi",
                "channel": "email",
                "approval_id": approval_id,
                "request_id": request_id,
            },
        )

    assert response.status_code == 202
    body = response.json()
    assert body["staged"] is True
    assert body["activation"]["borrower_id"] == borrower_id
    assert body["activation"]["status"] == "dry_run"
    assert body["activation"]["approval_id"] == approval_id
    assert body["activation"]["request_id"] == request_id
    assert store.last_approved_decision
    assert store.last_approved_decision["approval_id"] == approval_id
    assert store.stage_calls == 1


def test_stage_campaign_activation_requires_current_active_campaign() -> None:
    borrower_id = mock_data.BORROWERS[0].borrower_id
    approval_id = str(uuid4())
    campaign_id = str(uuid4())
    store = _ActivationStore(
        approved_decisions={
            approval_id: _approval_decision(
                approval_id,
                borrower_id,
                campaign_id=campaign_id,
            )
        },
        campaign_status="archived",
    )

    with _activation_overrides(store=store, sales_state=_SalesState(approval_id=approval_id)):
        response = client.post(
            "/api/activation/stage",
            json={
                "borrower_id": borrower_id,
                "destination_key": "salesforce_crm",
                "campaign_id": campaign_id,
                "approval_id": approval_id,
                "request_id": str(uuid4()),
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "campaign must be active at activation staging time"
    assert store.stage_calls == 0


def test_stage_campaign_activation_accepts_current_active_campaign() -> None:
    borrower_id = mock_data.BORROWERS[0].borrower_id
    approval_id = str(uuid4())
    campaign_id = str(uuid4())
    store = _ActivationStore(
        approved_decisions={
            approval_id: _approval_decision(
                approval_id,
                borrower_id,
                campaign_id=campaign_id,
            )
        },
        campaign_status="active",
    )

    with _activation_overrides(store=store, sales_state=_SalesState(approval_id=approval_id)):
        response = client.post(
            "/api/activation/stage",
            json={
                "borrower_id": borrower_id,
                "destination_key": "salesforce_crm",
                "campaign_id": campaign_id,
                "approval_id": approval_id,
                "request_id": str(uuid4()),
            },
        )

    assert response.status_code == 202, response.text
    assert response.json()["activation"]["campaign_id"] == campaign_id


def test_stage_activation_requires_request_id_and_approval_id() -> None:
    borrower_id = mock_data.BORROWERS[0].borrower_id

    with _activation_overrides() as store:
        response = client.post(
            "/api/activation/stage",
            json={"borrower_id": borrower_id, "destination_key": "salesforce_crm"},
        )

    assert response.status_code == 422
    assert store.stage_calls == 0


def test_stage_activation_rejects_stale_approval_id() -> None:
    borrower_id = mock_data.BORROWERS[0].borrower_id
    current_approval_id = str(uuid4())
    stale_approval_id = str(uuid4())
    sales_state = _SalesState(approval_id=current_approval_id)
    store = _ActivationStore(
        approved_decisions={
            current_approval_id: _approval_decision(current_approval_id, borrower_id),
            stale_approval_id: _approval_decision(stale_approval_id, borrower_id),
        },
    )

    with _activation_overrides(store=store, sales_state=sales_state):
        response = client.post(
            "/api/activation/stage",
            json={
                "borrower_id": borrower_id,
                "destination_key": "salesforce_crm",
                "approval_id": stale_approval_id,
                "request_id": str(uuid4()),
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "approval_id is not the current approved decision for this borrower"
    assert store.stage_calls == 0


def test_stage_activation_rejects_unknown_current_approval_id() -> None:
    borrower_id = mock_data.BORROWERS[0].borrower_id
    approval_id = str(uuid4())

    with _activation_overrides(sales_state=_SalesState(approval_id=approval_id)) as store:
        store.approved_decisions.clear()
        response = client.post(
            "/api/activation/stage",
            json={
                "borrower_id": borrower_id,
                "destination_key": "salesforce_crm",
                "approval_id": approval_id,
                "request_id": str(uuid4()),
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "approval_id is not an approved decision for this borrower"
    assert store.stage_calls == 0


def test_stage_activation_rejects_destination_without_stage_lead_action() -> None:
    borrower_id = mock_data.BORROWERS[0].borrower_id
    approval_id = str(uuid4())
    store = _ActivationStore(destination=_destination(allowed_actions=["stage_campaign"]))

    with _activation_overrides(store=store, sales_state=_SalesState(approval_id=approval_id)):
        response = client.post(
            "/api/activation/stage",
            json={
                "borrower_id": borrower_id,
                "destination_key": "salesforce_crm",
                "approval_id": approval_id,
                "request_id": str(uuid4()),
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "destination does not allow lead staging"
    assert store.stage_calls == 0


def test_stage_activation_rejects_unapproved_leads() -> None:
    borrower_id = mock_data.BORROWERS[0].borrower_id

    with _activation_overrides(sales_state=_SalesState("pending")) as store:
        response = client.post(
            "/api/activation/stage",
            json={
                "borrower_id": borrower_id,
                "destination_key": "salesforce_crm",
                "approval_id": str(uuid4()),
                "request_id": str(uuid4()),
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "lead must be approved before activation staging"
    assert store.stage_calls == 0


def test_stage_activation_rejects_non_marketable_leads() -> None:
    borrower = mock_data.BORROWERS[0].model_copy(
        update={"marketing_eligible": False, "suppression_reason": "do_not_contact"},
    )

    with _activation_overrides(borrowers=_Borrowers(borrower)) as store:
        response = client.post(
            "/api/activation/stage",
            json={
                "borrower_id": borrower.borrower_id,
                "destination_key": "salesforce_crm",
                "approval_id": str(uuid4()),
                "request_id": str(uuid4()),
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "lead is not marketing eligible"
    assert store.stage_calls == 0


def test_stage_activation_rejects_opt_out_and_suppressed_leads() -> None:
    opt_out = mock_data.BORROWERS[0].model_copy(update={"consent_status": "opt_out"})
    suppressed = mock_data.BORROWERS[0].model_copy(update={"suppression_reason": "do_not_contact"})

    with _activation_overrides(borrowers=_Borrowers(opt_out)) as store:
        opt_out_response = client.post(
            "/api/activation/stage",
            json={
                "borrower_id": opt_out.borrower_id,
                "destination_key": "salesforce_crm",
                "approval_id": str(uuid4()),
                "request_id": str(uuid4()),
            },
        )
    assert opt_out_response.status_code == 409
    assert opt_out_response.json()["detail"] == "lead does not have opt-in consent"
    assert store.stage_calls == 0

    with _activation_overrides(borrowers=_Borrowers(suppressed)) as store:
        suppressed_response = client.post(
            "/api/activation/stage",
            json={
                "borrower_id": suppressed.borrower_id,
                "destination_key": "salesforce_crm",
                "approval_id": str(uuid4()),
                "request_id": str(uuid4()),
            },
        )
    assert suppressed_response.status_code == 409
    assert suppressed_response.json()["detail"] == "lead is suppressed"
    assert store.stage_calls == 0


def test_stage_activation_rejects_disabled_destination() -> None:
    borrower_id = mock_data.BORROWERS[0].borrower_id
    approval_id = str(uuid4())
    store = _ActivationStore(destination=_destination("disabled"))

    with _activation_overrides(store=store, sales_state=_SalesState(approval_id=approval_id)):
        response = client.post(
            "/api/activation/stage",
            json={
                "borrower_id": borrower_id,
                "destination_key": "salesforce_crm",
                "approval_id": approval_id,
                "request_id": str(uuid4()),
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "activation destination is disabled"
    assert store.stage_calls == 0


def test_stage_activation_surfaces_lakebase_failures_as_503() -> None:
    borrower_id = mock_data.BORROWERS[0].borrower_id
    approval_id = str(uuid4())
    store = _ActivationStore(fail_stage=True)

    with _activation_overrides(store=store, sales_state=_SalesState(approval_id=approval_id)):
        response = client.post(
            "/api/activation/stage",
            json={
                "borrower_id": borrower_id,
                "destination_key": "salesforce_crm",
                "approval_id": approval_id,
                "request_id": str(uuid4()),
            },
        )

    assert response.status_code == 503
