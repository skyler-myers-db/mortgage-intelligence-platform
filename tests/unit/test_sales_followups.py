"""Follow-ups due: GET /api/sales/followups/summary and GET /api/sales/followups.

D-shell-deviations-a (audit 2026-09-21 shell-09, flow-08, wow-power-3 phase 1).
The SQL semantics of the shared open-item CTE (every kind, precedence,
closures, the snooze join) run against real PostgreSQL in
tests/integration/test_sales_followups_postgres.py. This suite pins the
service and route layer over a fake Lakebase that answers the CTE's rows:

- the summary is Lakebase-only, audit-free, cached per actor under an opaque
  key, ``eligible: false`` without an identity or for an ineligible caller;
- the list is 401 without an identity, 403 when ineligible, and checks EVERY
  row of every kind with the canonical evaluate + predicate pair, failing
  closed in both disagreement directions; a missing gold id is withheld as
  not_in_coverage and never dropped; a snooze never hides a suppression;
- neither route writes an audit row or bumps the identity-fallback counter;
- the response carries no consent, suppression or do-not-contact field and
  no email.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.config.settings import settings
from backend.main import app
from backend.services import sales_followups
from backend.services.audit_store import get_audit_store, get_fallback_identity_count
from backend.services.databricks_sql import DatabricksSqlError
from backend.services.lakebase import LakebaseError
from backend.services.repositories import get_borrower_repository
from backend.services.repositories.databricks_borrower_contactability import (
    BorrowerContactabilityRow,
)
from backend.services.resilience_cache import TTLCache
from backend.services.sales_followups import (
    FollowupService,
    contact_verdict,
    get_followup_service,
)
from backend.services.sales_followups_sql import LIST_SQL, SUMMARY_SQL
from backend.services.sales_state_mappers import _SALES_STATE_CACHE

SUMMARY = "/api/v1/sales/followups/summary"
LIST = "/api/v1/sales/followups"
LO = "lo01@summit.example"
APPROVER = "approver@summit-mortgage.example"
STRANGER = "someone@summit-mortgage.example"
NOW = datetime.now(UTC)
FORBIDDEN_KEYS = {"suppression_reason", "consent_status", "dnc", "marketing_eligible", "email", "lo_email"}


def _member(email: str) -> dict[str, Any]:
    return {
        "email": email,
        "display_label": "Summit LO 01",
        "role": "loan_officer",
        "region": "IL",
        "manager_email": None,
        "capacity_per_day": 35,
        "active": True,
    }


def _row(borrower_id: str, kind: str = "callback", *, rank: int = 1, snoozed: bool = False,
         woke: bool = False, due: datetime | None = None) -> dict[str, Any]:
    return {
        "borrower_id": borrower_id,
        "kind": kind,
        "due_at": None if kind == "new_assignment" else (due or NOW + timedelta(hours=2)),
        "since_at": NOW - timedelta(days=1),
        "attempt_number": 2 if kind == "callback" else None,
        "owner_label": "Summit LO 02" if kind == "requested_follow_up" else None,
        "owner_state": "assigned" if kind == "requested_follow_up" else None,
        "snoozed": snoozed,
        "woke_on_signal": woke,
        "kind_rank": rank,
    }


class _Lakebase:
    """Answers the team lookup, the summary and the list; records every read."""

    def __init__(self, *, members: tuple[str, ...] = (LO,), rows: list[dict[str, Any]] | None = None,
                 next_due_at: datetime | None = None, error: BaseException | None = None) -> None:
        self.members = members
        self.rows = rows or []
        self.next_due_at = next_due_at
        self.error = error
        self.calls: list[str] = []

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if self.error is not None:
            raise self.error
        if "FROM mip_app.sales_team" in sql:
            self.calls.append("team")
            email = (params or {}).get("email")
            return _member(str(email)) if email in self.members else None
        assert sql == SUMMARY_SQL, "the summary reads only the shared CTE"
        assert params == {"actor": (params or {}).get("actor")}
        self.calls.append("summary")
        return {"next_due_at": self.next_due_at}

    def fetchall(self, sql: str, params: dict[str, Any] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if self.error is not None:
            raise self.error
        assert sql == LIST_SQL
        self.calls.append("list")
        return [dict(row) for row in self.rows]

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        raise AssertionError("follow-ups reads never write")


class _NoAudit:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"follow-ups reads never touch the audit store ({name})")


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "trust_forwarded_headers", True)
    monkeypatch.setattr(settings, "app_env", "sandbox")
    monkeypatch.setattr(settings, "admin_emails", "")
    monkeypatch.setattr(settings, "admin_identities", "")
    monkeypatch.setattr(settings, "approver_emails", APPROVER)
    monkeypatch.setattr(settings, "approver_identities", "")
    _SALES_STATE_CACHE.clear()


@pytest.fixture
def borrower_repo() -> Any:
    repo = app.dependency_overrides[get_borrower_repository]()
    repo.contactability_overrides = {}
    yield repo
    repo.contactability_overrides = {}


def _install(lakebase: _Lakebase) -> TTLCache:
    cache = TTLCache()
    app.dependency_overrides[get_followup_service] = lambda: FollowupService(lambda: lakebase, cache=cache)
    app.dependency_overrides[get_audit_store] = lambda: _NoAudit()
    return cache


def _get(path: str, actor: str | None, **params: Any) -> Any:
    headers = {"X-Forwarded-Groups": ""}
    if actor is not None:
        headers["X-Forwarded-Email"] = actor
    return TestClient(app).get(path, headers=headers, params=params)


# --- summary ------------------------------------------------------------------


@pytest.mark.parametrize("trust", [True, False])
def test_the_summary_without_an_identity_is_ineligible_and_reads_nothing(
    monkeypatch: pytest.MonkeyPatch, trust: bool
) -> None:
    monkeypatch.setattr(settings, "trust_forwarded_headers", trust)
    lakebase = _Lakebase()
    _install(lakebase)
    before = get_fallback_identity_count()

    response = _get(SUMMARY, None if trust else LO)

    assert response.status_code == 200
    assert response.json() == {"eligible": False, "next_due_at": None}
    assert response.headers["cache-control"] == "private, no-store"
    assert lakebase.calls == []
    assert get_fallback_identity_count() == before


def test_an_ineligible_caller_reads_eligible_false() -> None:
    lakebase = _Lakebase(next_due_at=NOW)
    _install(lakebase)

    response = _get(SUMMARY, STRANGER)

    assert response.status_code == 200
    assert response.json() == {"eligible": False, "next_due_at": None}
    assert lakebase.calls == ["team"], "never reads the open items of a caller with no tray"


@pytest.mark.parametrize("actor", [LO, f"  {LO.upper()} ", APPROVER])
def test_a_team_member_or_an_approver_reads_the_next_due_time(actor: str) -> None:
    due = NOW + timedelta(hours=3)
    lakebase = _Lakebase(next_due_at=due)
    _install(lakebase)

    response = _get(SUMMARY, actor)

    assert response.status_code == 200
    body = response.json()
    assert body["eligible"] is True
    assert datetime.fromisoformat(body["next_due_at"]) == due
    assert lakebase.calls[-1] == "summary"


def test_the_summary_is_lakebase_only_even_when_the_warehouse_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.services import databricks_sql

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("the summary never constructs or calls a warehouse client")

    monkeypatch.setattr(databricks_sql, "get_sql_client", _boom)
    app.dependency_overrides[get_borrower_repository] = _boom
    lakebase = _Lakebase(next_due_at=NOW)
    _install(lakebase)

    response = _get(SUMMARY, LO)

    assert response.status_code == 200 and response.json()["eligible"] is True


def test_neither_read_writes_audit_or_bumps_the_fallback_counter(borrower_repo: Any) -> None:
    lakebase = _Lakebase(rows=[_row("B-48291")], next_due_at=NOW)
    _install(lakebase)
    before = get_fallback_identity_count()

    for actor in (LO, None, STRANGER):
        assert _get(SUMMARY, actor).status_code == 200
    assert _get(LIST, LO).status_code == 200
    assert _get(LIST, None).status_code == 401

    assert get_fallback_identity_count() == before
    assert "list" in lakebase.calls, "non-vacuity: the list really read"


def test_the_summary_is_cached_per_actor_under_an_opaque_key() -> None:
    lakebase = _Lakebase(next_due_at=NOW)
    cache = _install(lakebase)

    _get(SUMMARY, LO)
    _get(SUMMARY, LO.upper())
    _get(SUMMARY, APPROVER)

    assert lakebase.calls.count("summary") == 2, "one read per actor inside the 30 s TTL"
    keys = list(cache._entries)
    assert len(keys) == 2
    assert all(key.startswith("followups_summary:actor_") and "@" not in key for key in keys)

    sales_followups._SUMMARY_CACHE.set(sales_followups.summary_cache_key(LO), object(), 30)
    sales_followups.invalidate_followup_summary(f" {LO.upper()} ")
    assert sales_followups._SUMMARY_CACHE.get(sales_followups.summary_cache_key(LO)) is None


def test_a_lakebase_failure_is_a_safe_503() -> None:
    _install(_Lakebase(error=LakebaseError("connection to 10.0.0.1 failed")))

    response = _get(SUMMARY, LO)

    assert response.status_code == 503
    assert response.json()["detail"] == "lakebase is temporarily unavailable"


# --- list -----------------------------------------------------------------------


def test_the_list_is_401_without_identity_and_403_for_an_ineligible_caller(borrower_repo: Any) -> None:
    lakebase = _Lakebase(rows=[_row("B-48291")])
    _install(lakebase)

    assert _get(LIST, None).status_code == 401
    forbidden = _get(LIST, STRANGER)
    assert forbidden.status_code == 403
    assert "list" not in lakebase.calls


def test_window_days_is_bounded() -> None:
    _install(_Lakebase())

    assert _get(LIST, LO, window_days=0).status_code == 422
    assert _get(LIST, LO, window_days=15).status_code == 422


def test_a_contactable_row_carries_only_public_fields(borrower_repo: Any) -> None:
    rows = [_row("B-48291"), _row("B-48294", "requested_follow_up"), _row("B-48295", "new_assignment")]
    _install(_Lakebase(rows=rows))

    response = _get(LIST, LO)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    body = response.json()
    assert body["truncated"] is False
    by_id = {item["borrower_id"]: item for item in body["items"]}
    assert by_id["B-48291"]["kind"] == "callback" and by_id["B-48291"]["attempt_number"] == 2
    assert by_id["B-48294"]["owner_label"] == "Summit LO 02"
    assert by_id["B-48295"]["due_at"] is None
    for item in body["items"]:
        assert item["contactable"] is True and item["contact_block_reason"] is None
        assert FORBIDDEN_KEYS.isdisjoint(item)
    assert "@" not in response.text and "Rodriguez" not in response.text


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"consent_status": "opt_out"}, "consent_not_opt_in"),
        ({"suppression_reason": "bankruptcy"}, "suppressed"),
        ({"dnc": True, "suppression_reason": None}, "suppressed"),
        ({"marketing_eligible": False}, "eligibility_not_proven"),
        ({"last_touch_at": NOW - timedelta(days=3)}, "frequency_cap"),
    ],
)
def test_every_eligibility_reason_lands_withheld(
    borrower_repo: Any, overrides: dict[str, Any], reason: str
) -> None:
    borrower_repo.contactability_overrides = {"B-48291": overrides}
    _install(_Lakebase(rows=[_row("B-48291")]))

    [item] = _get(LIST, LO).json()["items"]

    assert item["contactable"] is False
    assert item["contact_block_reason"] == reason
    if reason == "frequency_cap":
        assert datetime.fromisoformat(item["earliest_recontact_at"]) > NOW
    else:
        assert item["earliest_recontact_at"] is None


@pytest.mark.parametrize("kind", ["callback", "approval_follow_up", "requested_follow_up", "new_assignment"])
def test_contactability_applies_to_every_kind(borrower_repo: Any, kind: str) -> None:
    borrower_repo.contactability_overrides = {"B-48291": {"suppression_reason": "litigation"}}
    _install(_Lakebase(rows=[_row("B-48291", kind)]))

    [item] = _get(LIST, LO).json()["items"]

    assert (item["kind"], item["contactable"], item["contact_block_reason"]) == (kind, False, "suppressed")


def test_an_id_gold_no_longer_carries_is_withheld_not_dropped(borrower_repo: Any) -> None:
    _install(_Lakebase(rows=[_row("B-0000000000001"), _row("B-48291")]))

    items = {item["borrower_id"]: item for item in _get(LIST, LO).json()["items"]}

    assert items["B-0000000000001"]["contactable"] is False
    assert items["B-0000000000001"]["contact_block_reason"] == "not_in_coverage"
    assert items["B-48291"]["contactable"] is True


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        # evaluate() allows, the warehouse predicate refuses.
        ({"contactable_sql": False}, "eligibility_not_proven"),
        ({"contactable_sql": None}, "eligibility_not_proven"),
        # The warehouse predicate allows, evaluate() refuses.
        ({"consent_status": "opt_out", "contactable_sql": True}, "consent_not_opt_in"),
        ({"suppression_reason": "fraud", "contactable_sql": True}, "suppressed"),
        ({"eligible_recontact_at": NOW + timedelta(days=4), "contactable_sql": True}, "frequency_cap"),
    ],
)
def test_the_evaluate_and_predicate_pair_fails_closed_both_ways(
    borrower_repo: Any, overrides: dict[str, Any], reason: str
) -> None:
    borrower_repo.contactability_overrides = {"B-48291": overrides}
    _install(_Lakebase(rows=[_row("B-48291")]))

    [item] = _get(LIST, LO).json()["items"]

    assert (item["contactable"], item["contact_block_reason"]) == (False, reason)


def test_an_unconfigured_row_fails_closed() -> None:
    row = BorrowerContactabilityRow.model_construct(contactable_sql=True)

    verdict = contact_verdict(row)

    assert (verdict.contactable, verdict.reason) == (False, "contactability_not_configured")


def test_a_snooze_hides_a_callable_or_capped_row_but_never_a_suppression(borrower_repo: Any) -> None:
    borrower_repo.contactability_overrides = {
        "B-48294": {"last_touch_at": NOW - timedelta(days=2)},
        "B-48295": {"suppression_reason": "bankruptcy"},
    }
    rows = [
        _row("B-48291", snoozed=True),
        _row("B-48294", snoozed=True),
        _row("B-48295", snoozed=True),
        _row("B-51872", woke=True),
    ]
    _install(_Lakebase(rows=rows))

    items = {item["borrower_id"]: item for item in _get(LIST, LO).json()["items"]}

    assert set(items) == {"B-48295", "B-51872"}
    assert items["B-48295"]["contact_block_reason"] == "suppressed", "a suppression always re-surfaces"
    assert items["B-51872"]["woke_on_signal"] is True


def test_a_kind_past_its_cap_sets_truncated_and_drops_the_extra_row(borrower_repo: Any) -> None:
    rows = [_row("B-48291", rank=50), _row("B-48294", rank=51)]
    _install(_Lakebase(rows=rows))

    body = _get(LIST, LO).json()

    assert body["truncated"] is True
    assert [item["borrower_id"] for item in body["items"]] == ["B-48291"]


def test_a_warehouse_failure_is_a_503_never_unverified_rows(
    borrower_repo: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail(_ids: Any) -> Any:
        raise DatabricksSqlError("statement_id=abc state=FAILED")

    monkeypatch.setattr(borrower_repo, "contactability", _fail)
    _install(_Lakebase(rows=[_row("B-48291")]))

    response = _get(LIST, LO)

    assert response.status_code == 503
    assert response.json()["detail"] == "warehouse is temporarily unavailable"
    assert "B-48291" not in response.text


def test_the_routes_carry_the_exact_paths() -> None:
    paths = {getattr(route, "path", "") for route in app.routes}

    assert {"/api/sales/followups/summary", "/api/sales/followups"} <= paths
    assert {"/api/v1/sales/followups/summary", "/api/v1/sales/followups"} <= paths
