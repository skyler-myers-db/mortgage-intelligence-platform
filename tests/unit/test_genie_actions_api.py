from __future__ import annotations

import hashlib
import json

from fastapi.testclient import TestClient

from backend.main import app
from backend.services.genie_answers import GenieActionSuggestion, GenieMessageResponse
from backend.services.lakebase import LakebaseError, get_lakebase_client
from backend.services.repositories import get_genie_answer_repository
from backend.services.workspace_store import InMemoryWorkspaceStore, get_workspace_store

client = TestClient(app)
ACTOR_HEADERS = {"X-Forwarded-Email": "lo@example.com"}


def _confirmed_payload_for_action(action_type: str) -> dict[str, object]:
    message = client.post(
        "/api/genie/message",
        json={"question": "Show me the top 10 borrowers by lead score in Illinois."},
        headers=ACTOR_HEADERS,
    )
    assert message.status_code == 200
    answer = message.json()
    action = next(
        row for row in answer["actions"]
        if row["action_type"] == action_type
    )
    return {
        "action_type": action["action_type"],
        "conversation_id": answer["conversation_id"],
        "message_id": answer["message_id"],
        "question_hash": answer["question_hash"],
        "borrower_ids": action["borrower_ids"],
        "criteria": action["criteria"],
        "route": action.get("route"),
        "request_id": action["request_id"],
        "confirmed": True,
        "confirmation_token": action["confirmation_token"],
    }


def _confirmed_payload(**overrides: object) -> dict[str, object]:
    payload = _confirmed_payload_for_action("save_borrowers")
    payload.update(overrides)
    return payload


def test_genie_start_lists_current_trusted_assets_without_fake_session() -> None:
    res = client.post("/api/genie/start", json={"context": {}})

    assert res.status_code == 200
    body = res.json()
    assert body["conversation_id"] is None
    assert "mip.gold.lead_population" in body["trusted_assets"]
    assert "mip.gold.segment_population" in body["trusted_assets"]
    assert "mip.gold.lead_segment_membership" not in body["trusted_assets"]


def test_genie_message_honors_conversation_id() -> None:
    res = client.post(
        "/api/genie/message",
        json={"question": "follow up by ZIP", "conversation_id": "conv-test"},
        headers={"X-Forwarded-Email": "lo@example.com"},
    )

    assert res.status_code == 200
    assert res.json()["conversation_id"] == "conv-test"


def test_genie_message_refuses_protected_class_prompts() -> None:
    res = client.post(
        "/api/genie/message",
        json={"question": "Rank borrowers by race and income."},
        headers={"X-Forwarded-Email": "lo@example.com"},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["source"] == "refused"
    assert body["table_rows"] == []
    assert body["proof"]["trusted"] is False


def test_genie_message_refuses_expanded_protected_class_prompts() -> None:
    res = client.post(
        "/api/genie/message",
        json={"question": "Show Hispanic borrowers with the best refinance odds."},
        headers={"X-Forwarded-Email": "lo@example.com"},
    )

    assert res.status_code == 200
    assert res.json()["source"] == "refused"


def test_genie_message_flags_outside_footprint_geography() -> None:
    res = client.post(
        "/api/genie/message",
        json={"question": "How many borrowers do we have in Massachusetts?"},
        headers={"X-Forwarded-Email": "lo@example.com"},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["source"] == "out_of_footprint"
    assert "outside the current Summit Mortgage Module 0 footprint" in body["answer"]
    assert "0 in-footprint borrowers" in body["answer"]
    assert body["row_count"] == 0
    assert body["table_rows"] == []


def test_genie_save_borrowers_action_is_confirmed_actor_scoped_and_audited() -> None:
    res = client.post(
        "/api/genie/actions",
        json=_confirmed_payload(),
        headers=ACTOR_HEADERS,
    )

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["saved_count"] == 1
    assert body["audit_event_id"]

    workspace = client.get(
        "/api/workspace",
        headers=ACTOR_HEADERS,
    ).json()
    assert [row["borrower_id"] for row in workspace["saved_leads"]]


def test_genie_action_token_requires_server_issued_request_id() -> None:
    payload = _confirmed_payload()
    payload["request_id"] = "client-forged-request-id"

    res = client.post(
        "/api/genie/actions",
        json=payload,
        headers=ACTOR_HEADERS,
    )

    assert res.status_code == 400
    assert res.json()["detail"] == "Genie action confirmation token is invalid"


def test_genie_actions_require_actor_identity() -> None:
    res = client.post(
        "/api/genie/actions",
        json=_confirmed_payload(action_type="export_insight", borrower_ids=[]),
    )

    assert res.status_code == 401
    assert res.json()["detail"] == "genie action identity required"


def test_genie_actions_reject_unknown_action_types() -> None:
    res = client.post(
        "/api/genie/actions",
        json=_confirmed_payload(action_type="drop_tables"),
        headers=ACTOR_HEADERS,
    )

    assert res.status_code == 400
    assert res.json()["detail"] == "unsupported Genie action"


def test_genie_actions_require_explicit_confirmation() -> None:
    payload = _confirmed_payload()
    payload["confirmed"] = False

    res = client.post(
        "/api/genie/actions",
        json=payload,
        headers=ACTOR_HEADERS,
    )

    assert res.status_code == 400
    assert res.json()["detail"] == "Genie action requires explicit confirmation"


def test_genie_actions_reject_invalid_confirmation_token() -> None:
    payload = _confirmed_payload()
    payload["confirmation_token"] = "invalid"

    res = client.post(
        "/api/genie/actions",
        json=payload,
        headers=ACTOR_HEADERS,
    )

    assert res.status_code == 400
    assert res.json()["detail"] == "Genie action confirmation token is invalid"


def test_genie_actions_reject_old_self_generated_confirmation_token() -> None:
    payload = _confirmed_payload()
    canonical = json.dumps(
        {
            "action_type": payload["action_type"],
            "borrower_ids": sorted(set(payload["borrower_ids"])),  # type: ignore[arg-type]
            "conversation_id": payload["conversation_id"],
            "criteria": payload["criteria"],
            "message_id": payload["message_id"],
            "question_hash": payload["question_hash"],
            "route": payload["route"],
        },
        sort_keys=True,
        default=str,
    )
    payload["confirmation_token"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    res = client.post(
        "/api/genie/actions",
        json=payload,
        headers=ACTOR_HEADERS,
    )

    assert res.status_code == 400
    assert res.json()["detail"] == "Genie action confirmation token is invalid"


def test_genie_actions_reject_token_reused_by_another_actor() -> None:
    payload = _confirmed_payload()

    res = client.post(
        "/api/genie/actions",
        json=payload,
        headers={"X-Forwarded-Email": "other@example.com"},
    )

    assert res.status_code == 400
    assert res.json()["detail"] == "Genie action confirmation token is invalid"


def test_genie_actions_reject_token_after_payload_tampering() -> None:
    payload = _confirmed_payload(route="/lead-queue")
    payload["borrower_ids"] = ["B-99999"]

    res = client.post(
        "/api/genie/actions",
        json=payload,
        headers=ACTOR_HEADERS,
    )

    assert res.status_code == 400
    assert res.json()["detail"] == "Genie action confirmation token is invalid"


def test_genie_actions_reject_result_filter_tampering() -> None:
    payload = _confirmed_payload()
    criteria = dict(payload["criteria"])  # type: ignore[arg-type]
    criteria["result_filters"] = {"zips": ["99999"], "segment_codes": ["itm"]}
    payload["criteria"] = criteria

    res = client.post(
        "/api/genie/actions",
        json=payload,
        headers=ACTOR_HEADERS,
    )

    assert res.status_code == 400
    assert res.json()["detail"] == "Genie action confirmation token is invalid"


def test_genie_actions_reject_untrusted_source_assets() -> None:
    payload = _confirmed_payload(
        action_type="export_insight",
        borrower_ids=[],
        criteria={
            "source": "genie",
            "source_assets": ["mip_app.action_audit"],
            "visualization_kind": "table",
            "row_count": 1,
        },
    )

    res = client.post(
        "/api/genie/actions",
        json=payload,
        headers=ACTOR_HEADERS,
    )

    assert res.status_code == 400
    assert res.json()["detail"] == "Genie action includes untrusted source assets"


class _DraftCampaignRepo:
    def respond(
        self,
        question: str,
        conversation_id: str | None = None,
    ) -> GenieMessageResponse:
        _ = question
        return GenieMessageResponse(
            conversation_id=conversation_id or "conv-draft",
            message_id="msg-draft",
            question="Turn this cohort into a draft campaign.",
            question_hash="hash-draft",
            answer="Draft cohort.",
            source="genie",
            trusted_assets=["mip.gold.borrower_360"],
            row_count=2,
            table_rows=[
                {"zip": "60617", "state": "IL", "borrowers": 1503},
                {"zip": "60628", "state": "IL", "borrowers": 1482},
            ],
            actions=[
                GenieActionSuggestion(
                    id="create-campaign-draft",
                    label="Create draft campaign",
                    action_type="create_draft_campaign",
                    description="Create a Lakebase draft campaign from this governed Genie result.",
                    route="/lead-queue?zips=60617%2C60628&segment=itm",
                    borrower_ids=[],
                    criteria={
                        "source": "genie",
                        "source_assets": ["mip.gold.borrower_360"],
                        "visualization_kind": "bar",
                        "row_count": 2,
                        "result_filters": {
                            "zips": ["60617", "60628"],
                            "segment_codes": ["itm"],
                            "segment_mode": "any",
                        },
                        "sql_hash": "abc123",
                    },
                )
            ],
        )


class _RecordingLakebase:
    def __init__(self) -> None:
        self.executes: list[tuple[str, dict[str, object]]] = []
        self.fetchones: list[tuple[str, dict[str, object]]] = []

    def execute(self, sql: str, params: dict[str, object] | None = None) -> None:
        self.executes.append((sql, params or {}))

    def fetchone(
        self,
        sql: str,
        params: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        self.fetchones.append((sql, params or {}))
        if "INSERT INTO mip_app.campaigns" in sql:
            return {"campaign_id": "campaign-1", "audit_id": "audit-1"}
        return None


def test_genie_create_draft_campaign_persists_full_cohort_criteria() -> None:
    lakebase = _RecordingLakebase()
    prior_repo = app.dependency_overrides.get(get_genie_answer_repository)
    prior_lakebase = app.dependency_overrides.get(get_lakebase_client)
    app.dependency_overrides[get_genie_answer_repository] = _DraftCampaignRepo
    app.dependency_overrides[get_lakebase_client] = lambda: lakebase
    try:
        payload = _confirmed_payload_for_action("create_draft_campaign")
        res = client.post(
            "/api/genie/actions",
            json=payload,
            headers=ACTOR_HEADERS,
        )
    finally:
        if prior_repo is None:
            app.dependency_overrides.pop(get_genie_answer_repository, None)
        else:
            app.dependency_overrides[get_genie_answer_repository] = prior_repo
        if prior_lakebase is None:
            app.dependency_overrides.pop(get_lakebase_client, None)
        else:
            app.dependency_overrides[get_lakebase_client] = prior_lakebase

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["campaign_id"] == "campaign-1"
    campaign_params = next(
        params for sql, params in lakebase.fetchones
        if "INSERT INTO mip_app.campaigns" in sql
    )
    criteria = json.loads(str(campaign_params["criteria"]))
    assert criteria["result_filters"]["zips"] == ["60617", "60628"]
    assert criteria["result_filters"]["segment_codes"] == ["itm"]
    assert criteria["sql_hash"] == "abc123"


class _ExplodingGenieWorkspaceStore(InMemoryWorkspaceStore):
    def __init__(self) -> None:
        super().__init__()
        self.atomic_calls = 0
        self.save_lead_calls = 0

    def save_lead(self, *args: object, **kwargs: object) -> object:
        self.save_lead_calls += 1
        return super().save_lead(*args, **kwargs)  # type: ignore[arg-type]

    def save_leads_from_genie_action(
        self,
        *,
        actor: str,
        borrower_ids: list[str],
        request_id: str,
        entity_id: str,
        metadata: dict[str, object],
    ) -> tuple[int, str | None]:
        _ = (actor, borrower_ids, request_id, entity_id, metadata)
        self.atomic_calls += 1
        raise LakebaseError("simulated atomic Genie action failure")


def test_genie_save_borrowers_does_not_mutate_before_atomic_audit_failure() -> None:
    store = _ExplodingGenieWorkspaceStore()
    previous = app.dependency_overrides.get(get_workspace_store)
    app.dependency_overrides[get_workspace_store] = lambda: store
    try:
        res = client.post(
            "/api/genie/actions",
            json=_confirmed_payload(),
            headers=ACTOR_HEADERS,
        )
    finally:
        if previous is None:
            del app.dependency_overrides[get_workspace_store]
        else:
            app.dependency_overrides[get_workspace_store] = previous

    assert res.status_code == 503
    assert store.atomic_calls == 1
    assert store.save_lead_calls == 0
    assert store.list(actor="lo@example.com").saved_leads == []
