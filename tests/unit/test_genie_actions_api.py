from __future__ import annotations

import hashlib
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.api.genie import (
    _CAMPAIGN_INSERT_SQL,
    _borrower_ids,
    _cohort_route_filters,
    _outside_footprint_match,
)
from backend.main import app
from backend.services.genie_answers import (
    GenieActionRequest,
    GenieActionSuggestion,
    GenieMessageResponse,
)
from backend.services.genie_client import GenieClientError
from backend.services.lakebase import LakebaseError, get_lakebase_client
from backend.services.repositories import get_genie_answer_repository
from backend.services.state_footprint import (
    FootprintState,
    StateFootprintResolver,
    _reset_state_footprint_resolver_for_tests,
)
from backend.services.workspace_store import InMemoryWorkspaceStore, get_workspace_store

client = TestClient(app)
ACTOR_HEADERS = {"X-Forwarded-Email": "lo@example.com"}
_TEST_COVERAGE = [
    FootprintState("IL", "Illinois", 1, True),
    FootprintState("CA", "California", 2, False),
    FootprintState("FL", "Florida", 3, False),
    FootprintState("WA", "Washington", 4, False),
]


def _install_footprint(rows: list[FootprintState]) -> None:
    resolver = StateFootprintResolver(ttl_s=60.0)
    resolver._load_from_uc = lambda: rows  # type: ignore[method-assign]
    _reset_state_footprint_resolver_for_tests(resolver)


def setup_function(_func: object) -> None:
    _install_footprint(_TEST_COVERAGE)


def teardown_function(_func: object) -> None:
    _reset_state_footprint_resolver_for_tests(None)


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
    assert body["sample_questions"]
    assert all(isinstance(q, str) and q.strip() for q in body["sample_questions"])


def test_genie_message_honors_conversation_id() -> None:
    class _OwnedSessionLakebase:
        def __init__(self) -> None:
            self.executes: list[tuple[str, dict[str, object]]] = []

        def fetchone(
            self,
            sql: str,
            params: dict[str, object] | None = None,
        ) -> dict[str, object] | None:
            if "FROM mip_app.genie_sessions" in sql and "conversation_id =" in sql:
                return {"conversation_id": (params or {}).get("conversation_id")}
            return None

        def execute(self, sql: str, params: dict[str, object] | None = None) -> None:
            self.executes.append((sql, params or {}))

    class _Repo:
        def respond(
            self,
            question: str,
            conversation_id: str | None = None,
        ) -> GenieMessageResponse:
            return GenieMessageResponse(
                conversation_id=conversation_id or "conv-new",
                message_id="msg-follow-up",
                question=question,
                question_hash="hash-follow-up",
                answer="Follow-up answer.",
                source="genie",
                trusted_assets=["mip.gold.borrower_360"],
                row_count=0,
                table_rows=[],
            )

    prior_repo = app.dependency_overrides.get(get_genie_answer_repository)
    prior_lakebase = app.dependency_overrides.get(get_lakebase_client)
    app.dependency_overrides[get_genie_answer_repository] = _Repo
    app.dependency_overrides[get_lakebase_client] = _OwnedSessionLakebase
    try:
        res = client.post(
            "/api/genie/message",
            json={"question": "follow up by ZIP", "conversation_id": "conv-test"},
            headers={"X-Forwarded-Email": "lo@example.com"},
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
    assert res.json()["conversation_id"] == "conv-test"


def test_genie_message_rejects_unowned_conversation_id() -> None:
    class _UnownedSessionLakebase:
        def fetchone(
            self,
            sql: str,
            params: dict[str, object] | None = None,
        ) -> dict[str, object] | None:
            _ = sql, params
            return None

    prior_lakebase = app.dependency_overrides.get(get_lakebase_client)
    app.dependency_overrides[get_lakebase_client] = _UnownedSessionLakebase
    try:
        res = client.post(
            "/api/genie/message",
            json={"question": "follow up by ZIP", "conversation_id": "conv-other"},
            headers={"X-Forwarded-Email": "lo@example.com"},
        )
    finally:
        if prior_lakebase is None:
            app.dependency_overrides.pop(get_lakebase_client, None)
        else:
            app.dependency_overrides[get_lakebase_client] = prior_lakebase

    assert res.status_code == 403
    assert res.json()["detail"] == "conversation_id is not owned by the current actor"


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


def test_genie_client_error_returns_sanitized_retryable_503() -> None:
    _install_footprint(_TEST_COVERAGE)

    class _FailingRepo:
        def respond(
            self,
            question: str,
            conversation_id: str | None = None,
        ) -> GenieMessageResponse:
            _ = question, conversation_id
            raise GenieClientError("403 raw upstream state=abc statement_id=secret")

    prior = app.dependency_overrides.get(get_genie_answer_repository)
    app.dependency_overrides[get_genie_answer_repository] = _FailingRepo
    try:
        res = client.post(
            "/api/genie/message",
            json={"question": "How many borrowers are in the money?"},
            headers={"X-Forwarded-Email": "lo@example.com"},
        )
    finally:
        _reset_state_footprint_resolver_for_tests(None)
        if prior is None:
            app.dependency_overrides.pop(get_genie_answer_repository, None)
        else:
            app.dependency_overrides[get_genie_answer_repository] = prior

    assert res.status_code == 503
    body = res.json()
    assert body["dependency"] == "genie"
    assert body["retryable"] is True
    assert body["reason"] == "retries_exhausted"
    assert "raw upstream" not in body["detail"]
    assert "statement_id" not in body["detail"]


def test_genie_message_flags_outside_footprint_geography() -> None:
    _install_footprint(_TEST_COVERAGE)
    try:
        res = client.post(
            "/api/genie/message",
            json={"question": "How many borrowers do we have in Massachusetts?"},
            headers={"X-Forwarded-Email": "lo@example.com"},
        )
    finally:
        _reset_state_footprint_resolver_for_tests(None)

    assert res.status_code == 200
    body = res.json()
    assert body["source"] == "out_of_footprint"
    assert "outside the current refreshed data coverage" in body["answer"]
    assert "will not treat that coverage gap as zero borrower demand" in body["answer"]
    assert body["row_count"] == 0
    assert body["table_rows"] == []


def test_genie_state_question_blocks_when_only_generic_state_metadata_is_available() -> None:
    resolver = StateFootprintResolver(ttl_s=60.0)
    resolver._load_from_uc = lambda: None  # type: ignore[method-assign]
    _reset_state_footprint_resolver_for_tests(resolver)
    try:
        res = client.post(
            "/api/genie/message",
            json={"question": "How many borrowers do we have in Massachusetts?"},
            headers={"X-Forwarded-Email": "lo@example.com"},
        )
    finally:
        _reset_state_footprint_resolver_for_tests(None)

    assert res.status_code == 200
    body = res.json()
    assert body["source"] == "data_gap"
    assert body["table_rows"] == []
    assert "current gold geography coverage is temporarily unavailable" in body["answer"]
    assert "generic US-state metadata" in body["answer"]


def test_genie_outside_footprint_uses_dynamic_state_footprint() -> None:
    resolver = StateFootprintResolver(ttl_s=60.0)
    resolver._load_from_uc = lambda: [  # type: ignore[method-assign]
        FootprintState("NY", "New York", 1, True),
        FootprintState("NJ", "New Jersey", 2, False),
        FootprintState("PA", "Pennsylvania", 3, False),
    ]
    _reset_state_footprint_resolver_for_tests(resolver)
    try:
        assert _outside_footprint_match("How many borrowers in New York?") is None
        assert _outside_footprint_match("How many borrowers in Massachusetts?") == (
            "Massachusetts",
            "MA",
            ["NY", "NJ", "PA"],
        )

        res = client.post(
            "/api/genie/message",
            json={"question": "How many borrowers do we have in Massachusetts?"},
            headers={"X-Forwarded-Email": "lo@example.com"},
        )
    finally:
        _reset_state_footprint_resolver_for_tests(None)

    assert res.status_code == 200
    body = res.json()
    assert body["source"] == "out_of_footprint"
    assert "(NY, NJ, PA)" in body["answer"]
    assert "full 3-state coverage view" in body["answer"]


def test_genie_outside_footprint_matches_lowercase_state_codes() -> None:
    resolver = StateFootprintResolver(ttl_s=60.0)
    resolver._load_from_uc = lambda: [  # type: ignore[method-assign]
        FootprintState("NY", "New York", 1, True),
    ]
    _reset_state_footprint_resolver_for_tests(resolver)
    try:
        assert _outside_footprint_match("How many borrowers in ma?") == (
            "Massachusetts",
            "MA",
            ["NY"],
        )
        assert _outside_footprint_match("How many borrowers in ok?") == (
            "Oklahoma",
            "OK",
            ["NY"],
        )
        assert _outside_footprint_match("How many borrowers in oh?") == (
            "Ohio",
            "OH",
            ["NY"],
        )
        assert _outside_footprint_match("How many borrowers in hi?") == (
            "Hawaii",
            "HI",
            ["NY"],
        )
    finally:
        _reset_state_footprint_resolver_for_tests(None)


def test_genie_outside_footprint_does_not_treat_uppercase_in_as_indiana() -> None:
    resolver = StateFootprintResolver(ttl_s=60.0)
    resolver._load_from_uc = lambda: [  # type: ignore[method-assign]
        FootprintState("NY", "New York", 1, True),
    ]
    _reset_state_footprint_resolver_for_tests(resolver)
    try:
        assert _outside_footprint_match("How many borrowers IN the money?") is None
        assert _outside_footprint_match("How many borrowers in IN?") == (
            "Indiana",
            "IN",
            ["NY"],
        )
    finally:
        _reset_state_footprint_resolver_for_tests(None)


def test_genie_outside_footprint_does_not_treat_common_words_as_state_codes() -> None:
    resolver = StateFootprintResolver(ttl_s=60.0)
    resolver._load_from_uc = lambda: [  # type: ignore[method-assign]
        FootprintState("NY", "New York", 1, True),
    ]
    _reset_state_footprint_resolver_for_tests(resolver)
    try:
        assert _outside_footprint_match("Hi, how many borrowers are in the money?") is None
        assert _outside_footprint_match("OK, show the top refinance ZIPs.") is None
        assert _outside_footprint_match("Oh, show retention risk by ZIP.") is None
    finally:
        _reset_state_footprint_resolver_for_tests(None)


def test_genie_current_footprint_questions_reach_genie_repository() -> None:
    _install_footprint(_TEST_COVERAGE)
    questions = [
        "How many borrowers do we have in IL?",
        "How many borrowers do we have in California?",
        "Break down borrowers in Florida.",
        "How many leads are in Washington?",
    ]

    class _RecordingRepo:
        def __init__(self) -> None:
            self.questions: list[str] = []

        def respond(
            self,
            question: str,
            conversation_id: str | None = None,
        ) -> GenieMessageResponse:
            self.questions.append(question)
            return GenieMessageResponse(
                conversation_id=conversation_id or "conv-footprint",
                message_id="msg-footprint",
                question=question,
                question_hash="hash-footprint",
                answer="Footprint-scoped answer.",
                source="genie",
                trusted_assets=["mip.gold.lead_population"],
                row_count=0,
                table_rows=[],
            )

    repo = _RecordingRepo()
    prior = app.dependency_overrides.get(get_genie_answer_repository)
    app.dependency_overrides[get_genie_answer_repository] = lambda: repo
    try:
        for question in questions:
            res = client.post(
                "/api/genie/message",
                json={"question": question},
                headers={"X-Forwarded-Email": "lo@example.com"},
            )
            assert res.status_code == 200
            assert res.json()["source"] == "genie"
    finally:
        _reset_state_footprint_resolver_for_tests(None)
        if prior is None:
            app.dependency_overrides.pop(get_genie_answer_repository, None)
        else:
            app.dependency_overrides[get_genie_answer_repository] = prior

    assert repo.questions == questions


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
        json=_confirmed_payload(),
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


def test_genie_actions_reject_removed_demo_export_action() -> None:
    res = client.post(
        "/api/genie/actions",
        json=_confirmed_payload(action_type="export_insight"),
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
                        "source": "trusted_sql",
                        "source_assets": ["mip.gold.borrower_360"],
                        "visualization_kind": "bar",
                        "row_count": 2,
                        "result_filters": {
                            "zips": ["60617", "60628"],
                            "segment_codes": ["itm"],
                            "segment_mode": "any",
                            "portfolio_criteria": {
                                "occupancy": "Owner-occupied",
                                "min_equity_pct_label": "≥ 25%",
                            },
                        },
                        "sql_hash": "abc123",
                    },
                )
            ],
        )


class _RecordingLakebase:
    def __init__(self) -> None:
        self.executes: list[tuple[str, dict[str, object]]] = []
        self.executemany_calls: list[tuple[str, list[dict[str, object]]]] = []
        self.fetchones: list[tuple[str, dict[str, object]]] = []

    def execute(self, sql: str, params: dict[str, object] | None = None) -> None:
        self.executes.append((sql, params or {}))

    def executemany(self, sql: str, params_list: list[dict[str, object]]) -> None:
        self.executemany_calls.append((sql, params_list))

    def fetchone(
        self,
        sql: str,
        params: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        self.fetchones.append((sql, params or {}))
        if "INSERT INTO mip_app.campaigns" in sql:
            return {"campaign_id": "campaign-1", "audit_id": "audit-1"}
        if "INSERT INTO mip_app.genie_cohorts" in sql:
            return {"cohort_id": "11111111-1111-1111-1111-111111111111"}
        if "INSERT INTO mip_app.action_audit" in sql:
            return {
                "audit_id": "audit-1",
                "entity_id": str((params or {}).get("entity_id") or ""),
                "metadata": (params or {}).get("metadata") or "{}",
            }
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
    assert criteria["source"] == "trusted_sql"
    assert criteria["result_filters"]["zips"] == ["60617", "60628"]
    assert criteria["result_filters"]["segment_codes"] == ["itm"]
    assert criteria["sql_hash"] == "abc123"


def test_genie_campaign_sql_keeps_action_audit_append_only() -> None:
    sql = _CAMPAIGN_INSERT_SQL.lower()

    assert "update mip_app.action_audit" not in sql
    assert "delete from mip_app.action_audit" not in sql
    assert "insert into mip_app.action_audit" in sql


class _OpenCohortRepo:
    def respond(
        self,
        question: str,
        conversation_id: str | None = None,
    ) -> GenieMessageResponse:
        _ = question
        return GenieMessageResponse(
            conversation_id=conversation_id or "conv-open",
            message_id="msg-open",
            question="Which ZIPs have the most in-the-money refinance candidates?",
            question_hash="hash-open",
            answer="Top ZIPs returned.",
            source="genie",
            trusted_assets=["mip.gold.borrower_360"],
            row_count=2,
            table_rows=[
                {"zip": "60617", "state": "IL", "borrowers": 1503},
                {"zip": "60628", "state": "IL", "borrowers": 1482},
            ],
            actions=[
                GenieActionSuggestion(
                    id="open-cohort",
                    label="Open this cohort in Lead Queue",
                    action_type="open_cohort",
                    description="Navigate into the lead queue with this Genie result audited.",
                    route="/lead-queue?zips=60617%2C60628&segment_codes=itm&segment_mode=any",
                    borrower_ids=["B-102FL7THC6Q3L"],
                    criteria={
                        "source": "genie",
                        "source_assets": ["mip.gold.borrower_360"],
                        "visualization_kind": "bar",
                        "row_count": 2,
                        "result_filters": {
                            "zips": ["60617", "60628"],
                            "segment_codes": ["itm"],
                            "segment_mode": "any",
                            "portfolio_criteria": {
                                "occupancy": "Owner-occupied",
                                "min_equity_pct_label": "≥ 25%",
                            },
                        },
                        "sql_hash": "abc123",
                    },
                )
            ],
        )


class _OpenEmptyCohortRepo:
    def respond(
        self,
        question: str,
        conversation_id: str | None = None,
    ) -> GenieMessageResponse:
        _ = question
        return GenieMessageResponse(
            conversation_id=conversation_id or "conv-open-empty",
            message_id="msg-open-empty",
            question="How many borrowers are in the population?",
            question_hash="hash-open-empty",
            answer="Population returned.",
            source="genie",
            trusted_assets=["mip.gold.borrower_360"],
            row_count=1,
            table_rows=[{"borrowers": 5_156_184}],
            actions=[
                GenieActionSuggestion(
                    id="open-cohort",
                    label="Open this cohort in Lead Queue",
                    action_type="open_cohort",
                    description="Navigate into the lead queue with this Genie result audited.",
                    route="/lead-queue",
                    borrower_ids=[],
                    criteria={
                        "source": "genie",
                        "source_assets": ["mip.gold.borrower_360"],
                        "visualization_kind": "metric",
                        "row_count": 1,
                        "sql_hash": "aggregate",
                    },
                )
            ],
        )


class _OpenLongRouteCohortRepo:
    def respond(
        self,
        question: str,
        conversation_id: str | None = None,
    ) -> GenieMessageResponse:
        _ = question
        zips = [f"{60600 + i:05d}" for i in range(101)]
        route = "/lead-queue?zips=" + "%2C".join(zips) + "&segment_codes=itm&segment_mode=any"
        return GenieMessageResponse(
            conversation_id=conversation_id or "conv-open-long",
            message_id="msg-open-long",
            question="Which ZIPs have the most in-the-money refinance candidates?",
            question_hash="hash-open-long",
            answer="Top ZIPs returned.",
            source="genie",
            trusted_assets=["mip.gold.borrower_360"],
            row_count=len(zips),
            table_rows=[{"zip": zip_code, "state": "IL", "borrowers": 10} for zip_code in zips],
            actions=[
                GenieActionSuggestion(
                    id="open-cohort",
                    label="Open this cohort in Lead Queue",
                    action_type="open_cohort",
                    description="Navigate into the lead queue with this Genie result audited.",
                    route=route,
                    borrower_ids=[],
                    criteria={
                        "source": "trusted_sql",
                        "source_assets": ["mip.gold.borrower_360"],
                        "visualization_kind": "bar",
                        "row_count": len(zips),
                        "result_filters": {
                            "zips": zips,
                            "segment_codes": ["itm"],
                            "segment_mode": "any",
                        },
                        "sql_hash": "long-route",
                    },
                )
            ],
        )


class _OpenOverLimitZipCohortRepo:
    def respond(
        self,
        question: str,
        conversation_id: str | None = None,
    ) -> GenieMessageResponse:
        _ = question
        zips = [f"{60000 + i:05d}" for i in range(501)]
        route = "/lead-queue?zips=" + "%2C".join(zips) + "&segment_codes=itm&segment_mode=any"
        return GenieMessageResponse(
            conversation_id=conversation_id or "conv-open-over-limit",
            message_id="msg-open-over-limit",
            question="Which ZIPs have the most in-the-money refinance candidates?",
            question_hash="hash-open-over-limit",
            answer="Top ZIPs returned.",
            source="genie",
            trusted_assets=["mip.gold.borrower_360"],
            row_count=len(zips),
            table_rows=[{"zip": zip_code, "state": "IL", "borrowers": 10} for zip_code in zips],
            actions=[
                GenieActionSuggestion(
                    id="open-cohort",
                    label="Open this cohort in Lead Queue",
                    action_type="open_cohort",
                    description="Navigate into the lead queue with this Genie result audited.",
                    route=route,
                    borrower_ids=[],
                    criteria={
                        "source": "trusted_sql",
                        "source_assets": ["mip.gold.borrower_360"],
                        "visualization_kind": "bar",
                        "row_count": len(zips),
                        "result_filters": {
                            "zips": zips,
                            "segment_codes": ["itm"],
                            "segment_mode": "any",
                        },
                        "sql_hash": "over-limit-route",
                    },
                )
            ],
        )


class _OpenScalarRootFiltersRepo:
    def respond(
        self,
        question: str,
        conversation_id: str | None = None,
    ) -> GenieMessageResponse:
        _ = question
        return GenieMessageResponse(
            conversation_id=conversation_id or "conv-open-root-scalar",
            message_id="msg-open-root-scalar",
            question="Open a cohort with malformed filters.",
            question_hash="hash-open-root-scalar",
            answer="Malformed root filters.",
            source="genie",
            trusted_assets=["mip.gold.borrower_360"],
            row_count=1,
            table_rows=[{"borrower_id": "B-102FL7THC6Q3L", "score": 80}],
            actions=[
                GenieActionSuggestion(
                    id="open-cohort",
                    label="Open this cohort in Lead Queue",
                    action_type="open_cohort",
                    description="Navigate into the lead queue with this Genie result audited.",
                    route="/lead-queue",
                    borrower_ids=["B-102FL7THC6Q3L"],
                    criteria={
                        "source": "genie",
                        "source_assets": ["mip.gold.borrower_360"],
                        "visualization_kind": "table",
                        "row_count": 1,
                        "result_filters": "60617",
                    },
                )
            ],
        )


class _DraftScalarRootFiltersRepo:
    def respond(
        self,
        question: str,
        conversation_id: str | None = None,
    ) -> GenieMessageResponse:
        _ = question
        return GenieMessageResponse(
            conversation_id=conversation_id or "conv-draft-root-scalar",
            message_id="msg-draft-root-scalar",
            question="Create a draft with malformed filters.",
            question_hash="hash-draft-root-scalar",
            answer="Malformed root filters.",
            source="genie",
            trusted_assets=["mip.gold.borrower_360"],
            row_count=1,
            table_rows=[{"borrower_id": "B-102FL7THC6Q3L", "score": 80}],
            actions=[
                GenieActionSuggestion(
                    id="create-campaign-draft",
                    label="Create draft campaign",
                    action_type="create_draft_campaign",
                    description="Create a Lakebase draft campaign from this governed Genie result.",
                    route="/lead-queue",
                    borrower_ids=["B-102FL7THC6Q3L"],
                    criteria={
                        "source": "genie",
                        "source_assets": ["mip.gold.borrower_360"],
                        "visualization_kind": "table",
                        "row_count": 1,
                        "result_filters": ["60617"],
                    },
                )
            ],
        )


def _bad_portfolio_criteria_repo(
    *,
    action_type: str,
    portfolio_criteria: object,
) -> type[object]:
    class _BadPortfolioCriteriaRepo:
        def respond(
            self,
            question: str,
            conversation_id: str | None = None,
        ) -> GenieMessageResponse:
            _ = question
            return GenieMessageResponse(
                conversation_id=conversation_id or "conv-bad-portfolio",
                message_id="msg-bad-portfolio",
                question="Replay a cohort with malformed portfolio criteria.",
                question_hash="hash-bad-portfolio",
                answer="Malformed portfolio criteria.",
                source="genie",
                trusted_assets=["mip.gold.borrower_360"],
                row_count=1,
                table_rows=[{"zip": "60617", "borrowers": 1}],
                actions=[
                    GenieActionSuggestion(
                        id="bad-portfolio-action",
                        label="Replay cohort",
                        action_type=action_type,
                        description="Replay this governed result.",
                        route="/lead-queue?zips=60617",
                        borrower_ids=[],
                        criteria={
                            "source": "trusted_sql",
                            "source_assets": ["mip.gold.borrower_360"],
                            "visualization_kind": "table",
                            "row_count": 1,
                            "result_filters": {
                                "zips": ["60617"],
                                "portfolio_criteria": portfolio_criteria,
                            },
                        },
                    )
                ],
            )

    return _BadPortfolioCriteriaRepo


class _LargeBorrowerActionRepo:
    def respond(
        self,
        question: str,
        conversation_id: str | None = None,
    ) -> GenieMessageResponse:
        _ = question
        borrower_ids = [f"B-{10000 + i:05d}" for i in range(100)]
        return GenieMessageResponse(
            conversation_id=conversation_id or "conv-large-borrowers",
            message_id="msg-large-borrowers",
            question="Show the top 100 borrowers.",
            question_hash="hash-large-borrowers",
            answer="Top borrowers returned.",
            source="genie",
            trusted_assets=["mip.gold.borrower_360"],
            row_count=len(borrower_ids),
            table_rows=[{"borrower_id": borrower_id, "score": 80} for borrower_id in borrower_ids],
            actions=[
                GenieActionSuggestion(
                    id="save-borrowers",
                    label="Save 100 borrowers",
                    action_type="save_borrowers",
                    description="Save returned borrowers.",
                    route="/lead-queue?zips="
                    + ",".join(f"{60000 + i:05d}" for i in range(150)),
                    borrower_ids=borrower_ids,
                    criteria={
                        "source": "genie",
                        "source_assets": ["mip.gold.borrower_360"],
                        "visualization_kind": "borrower_list",
                        "row_count": len(borrower_ids),
                        "result_filters": {"borrower_ids": borrower_ids},
                        "sql_hash": "large-borrowers",
                        "audit_explanation": "trusted cohort proof " * 120,
                    },
                )
            ],
        )


def test_genie_open_cohort_materializes_lakebase_cohort_and_returns_filtered_route() -> None:
    lakebase = _RecordingLakebase()
    prior_repo = app.dependency_overrides.get(get_genie_answer_repository)
    prior_lakebase = app.dependency_overrides.get(get_lakebase_client)
    app.dependency_overrides[get_genie_answer_repository] = _OpenCohortRepo
    app.dependency_overrides[get_lakebase_client] = lambda: lakebase
    try:
        payload = _confirmed_payload_for_action("open_cohort")
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
    assert body["route"].startswith("/lead-queue?")
    assert "cohort_id=11111111-1111-1111-1111-111111111111" in body["route"]
    assert "zips=60617%2C60628" in body["route"]
    cohort_params = next(
        params for sql, params in lakebase.fetchones
        if "INSERT INTO mip_app.genie_cohorts" in sql
    )
    assert json.loads(str(cohort_params["route_filters"])) == {
        "borrower_ids": ["B-102FL7THC6Q3L"],
        "portfolio_criteria": {
            "marketing_eligibility": "Eligible only",
            "min_equity_pct_label": "≥ 25%",
            "occupancy": "Owner-occupied",
        },
        "segment_codes": ["itm"],
        "segment_mode": "any",
        "source": "genie",
        "zips": ["60617", "60628"],
    }
    assert lakebase.executemany_calls


def test_genie_open_cohort_accepts_broad_zip_route_without_truncating_criteria() -> None:
    lakebase = _RecordingLakebase()
    prior_repo = app.dependency_overrides.get(get_genie_answer_repository)
    prior_lakebase = app.dependency_overrides.get(get_lakebase_client)
    app.dependency_overrides[get_genie_answer_repository] = _OpenLongRouteCohortRepo
    app.dependency_overrides[get_lakebase_client] = lambda: lakebase
    try:
        payload = _confirmed_payload_for_action("open_cohort")
        assert len(str(payload["route"])) > 256
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
    cohort_params = next(
        params for sql, params in lakebase.fetchones
        if "INSERT INTO mip_app.genie_cohorts" in sql
    )
    filters = json.loads(str(cohort_params["route_filters"]))
    assert filters["source"] == "trusted_sql"
    assert len(filters["zips"]) == 101


def test_genie_open_cohort_rejects_over_limit_zip_filter_without_truncating() -> None:
    lakebase = _RecordingLakebase()
    prior_repo = app.dependency_overrides.get(get_genie_answer_repository)
    prior_lakebase = app.dependency_overrides.get(get_lakebase_client)
    app.dependency_overrides[get_genie_answer_repository] = _OpenOverLimitZipCohortRepo
    app.dependency_overrides[get_lakebase_client] = lambda: lakebase
    try:
        payload = _confirmed_payload_for_action("open_cohort")
        assert len(payload["criteria"]["result_filters"]["zips"]) == 501
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

    assert res.status_code == 400
    assert "too many replay filters" in res.text
    assert not any("INSERT INTO mip_app.genie_cohorts" in sql for sql, _ in lakebase.fetchones)


def test_genie_cohort_filters_reject_invalid_scalar_values() -> None:
    with pytest.raises(HTTPException) as scalar_root:
        _cohort_route_filters(
            GenieActionRequest(
                action_type="open_cohort",
                criteria={
                    "source": "genie",
                    "result_filters": "60617",
                },
                borrower_ids=["B-102FL7THC6Q3L"],
                confirmed=True,
            ),
            ["B-102FL7THC6Q3L"],
        )
    assert "result_filters must be a reviewed object" in str(scalar_root.value)

    with pytest.raises(HTTPException) as scalar_zips:
        _cohort_route_filters(
            GenieActionRequest(
                action_type="open_cohort",
                criteria={
                    "source": "genie",
                    "result_filters": {
                        "zips": "60617",
                        "segment_codes": ["itm"],
                        "segment_mode": "any",
                    },
                },
                confirmed=True,
            ),
            [],
        )
    assert "zips filter must be a reviewed list" in str(scalar_zips.value)

    with pytest.raises(HTTPException) as invalid_county:
        _cohort_route_filters(
            GenieActionRequest(
                action_type="open_cohort",
                criteria={
                    "source": "genie",
                    "result_filters": {"county": "abcde", "segment_codes": ["itm"]},
                },
                confirmed=True,
            ),
            [],
        )
    assert "invalid county filter" in str(invalid_county.value)

    with pytest.raises(HTTPException) as invalid_mode:
        _cohort_route_filters(
            GenieActionRequest(
                action_type="open_cohort",
                criteria={
                    "source": "genie",
                    "result_filters": {
                        "zips": ["60617"],
                        "segment_codes": ["itm", "equity"],
                        "segment_mode": "allx",
                    },
                },
                confirmed=True,
            ),
            [],
        )
    assert "invalid segment mode" in str(invalid_mode.value)


@pytest.mark.parametrize(
    ("repo_cls", "action_type"),
    [
        (_OpenScalarRootFiltersRepo, "open_cohort"),
        (_DraftScalarRootFiltersRepo, "create_draft_campaign"),
    ],
)
def test_genie_actions_reject_non_object_root_result_filters(
    repo_cls: type[object],
    action_type: str,
) -> None:
    lakebase = _RecordingLakebase()
    prior_repo = app.dependency_overrides.get(get_genie_answer_repository)
    prior_lakebase = app.dependency_overrides.get(get_lakebase_client)
    app.dependency_overrides[get_genie_answer_repository] = repo_cls
    app.dependency_overrides[get_lakebase_client] = lambda: lakebase
    try:
        payload = _confirmed_payload_for_action(action_type)
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

    assert res.status_code == 400
    assert "result_filters must be a reviewed object" in res.text
    assert not any("INSERT INTO mip_app.genie_cohorts" in sql for sql, _ in lakebase.fetchones)
    assert not any("INSERT INTO mip_app.campaigns" in sql for sql, _ in lakebase.fetchones)


@pytest.mark.parametrize(
    "portfolio_criteria",
    [
        "Owner-occupied",
        {},
        {"geography": "", "occupancy": ""},
        {
            "occupancy": "All",
            "lien_status": "Any",
            "lender_relationship": "All",
            "product": "All products",
            "target_lender_ref": "All",
            "min_equity_pct_label": "Any",
            "owner_link": "All",
            "purchase_intent": "All",
        },
        {"occupancy": "Owner-occupied", "owner_name": "Alice Borrower"},
        {"occupancy": "Owner-occupied", "raw_lender_name": "Acme Bank"},
    ],
)
@pytest.mark.parametrize("action_type", ["open_cohort", "create_draft_campaign"])
def test_genie_actions_reject_unreviewed_or_noop_portfolio_criteria(
    portfolio_criteria: object,
    action_type: str,
) -> None:
    lakebase = _RecordingLakebase()
    prior_repo = app.dependency_overrides.get(get_genie_answer_repository)
    prior_lakebase = app.dependency_overrides.get(get_lakebase_client)
    app.dependency_overrides[get_genie_answer_repository] = _bad_portfolio_criteria_repo(
        action_type=action_type,
        portfolio_criteria=portfolio_criteria,
    )
    app.dependency_overrides[get_lakebase_client] = lambda: lakebase
    try:
        payload = _confirmed_payload_for_action(action_type)
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

    assert res.status_code == 400
    assert "unreviewed portfolio criteria" in res.text
    assert not any("INSERT INTO mip_app.genie_cohorts" in sql for sql, _ in lakebase.fetchones)
    assert not any("INSERT INTO mip_app.campaigns" in sql for sql, _ in lakebase.fetchones)


def test_genie_action_request_accepts_full_replay_cap_without_silent_borrower_truncation() -> None:
    borrower_ids = [f"B-{10000 + i:05d}" for i in range(500)]

    request = GenieActionRequest(action_type="save_borrowers", borrower_ids=borrower_ids)

    assert request.borrower_ids == borrower_ids
    with pytest.raises(ValidationError):
        GenieActionRequest(action_type="save_borrowers", borrower_ids=[*borrower_ids, "B-99999"])


def test_genie_action_borrower_ids_reject_invalid_values_without_narrowing() -> None:
    with pytest.raises(HTTPException) as invalid:
        _borrower_ids(["B-11111", "raw-clip-123"])

    assert "invalid borrower id" in str(invalid.value)


def test_genie_save_borrowers_accepts_large_signed_action_token() -> None:
    workspace = InMemoryWorkspaceStore()
    prior_repo = app.dependency_overrides.get(get_genie_answer_repository)
    prior_workspace = app.dependency_overrides.get(get_workspace_store)
    app.dependency_overrides[get_genie_answer_repository] = _LargeBorrowerActionRepo
    app.dependency_overrides[get_workspace_store] = lambda: workspace
    try:
        payload = _confirmed_payload_for_action("save_borrowers")
        assert len(str(payload["confirmation_token"])) > 2048
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
        if prior_workspace is None:
            app.dependency_overrides.pop(get_workspace_store, None)
        else:
            app.dependency_overrides[get_workspace_store] = prior_workspace

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["saved_count"] == 100


def test_genie_open_cohort_rejects_action_without_replayable_filters() -> None:
    lakebase = _RecordingLakebase()
    prior_repo = app.dependency_overrides.get(get_genie_answer_repository)
    prior_lakebase = app.dependency_overrides.get(get_lakebase_client)
    app.dependency_overrides[get_genie_answer_repository] = _OpenEmptyCohortRepo
    app.dependency_overrides[get_lakebase_client] = lambda: lakebase
    try:
        payload = _confirmed_payload_for_action("open_cohort")
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

    assert res.status_code == 400
    assert res.json()["detail"] == "Genie cohort action has no replayable lead filters"


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
