"""wow-ai-5 spike tooling (tools/genie_tiles_spike.py, tools/genie_tiles_live.py).

The offline measurement is deterministic and read-only over the guards; these
tests run it on a shrunken corpus so they stay fast. The full run is
``python -m tools.genie_tiles_spike --offline`` (numbers in
docs/genie-tiles-spike.md). The W5c ``--live`` probe runs here against a fake
WorkspaceClient: age buckets, the execute-state gate, the per-bucket cap,
the --allow-wake gate, isolation, exit codes, and no question, SQL, row value
or email in its JSON.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools import genie_tiles_live as live
from tools import genie_tiles_spike as spike

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "genie_tiles_spike.py"


def test_the_offline_corpus_is_deterministic_and_tile_shaped() -> None:
    first = [spike.tile_rows(tile, 50) for tile in range(spike.TILE_COUNT)]
    second = [spike.tile_rows(tile, 50) for tile in range(spike.TILE_COUNT)]

    assert first == second
    for rows in first:
        assert len(rows) == 50
        assert all(len(row) <= spike.MAX_COLUMNS for row in rows)
        assert all(re.fullmatch(r"B-[0-9A-Z]{13}", row["top_borrower_id"]) for row in rows)
        assert all(row["contact_email"].endswith(".example") for row in rows)


def test_the_offline_measurement_counts_guard_work_and_emits_no_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spike, "ROW_SIZES", (2, 4))
    monkeypatch.setattr(spike, "TILE_COUNT", 2)

    result = spike.measure_offline()
    text = json.dumps(result)

    assert set(result["sizes"]) == {"2", "4"}
    assert result["sizes"]["4"]["guard_calls"] > result["sizes"]["2"]["guard_calls"] > 0
    assert result["narrative_baseline"]["guard_calls"] > 0
    assert result["criterion_1"]["verdict"] in {"PASS", "FAIL"}
    for leaked in ("Texas", "County A", "B-", "summit.example", "SELECT", "Prime Refi", "512-555"):
        assert leaked not in text, leaked


def test_the_row_path_fails_closed_on_a_pii_shaped_cell() -> None:
    asset = sorted(spike._trusted_genie_asset_names())[0]
    rows = spike.tile_rows(spike.TILE_COUNT - 1, 3)

    outcome = spike.refresh_row_path(spike.tile_sql(0, asset), rows, asset)

    assert outcome["sql_trusted"] is True
    assert outcome["flagged_cells"] > 0


def test_the_tool_is_read_only_over_the_guards_and_imported_by_nothing() -> None:
    for tool in (TOOL, LIVE):
        source = tool.read_text(encoding="utf-8")
        assert not re.search(r"\bsetattr\(|\.monkeypatch|importlib\.reload|sys\.modules\[", source)
        assert "from tests" not in source and "import tests" not in source
    for base in (ROOT / "backend", ROOT / "frontend" / "src"):
        for path in base.rglob("*"):
            if path.suffix in {".py", ".ts", ".tsx"} and "node_modules" not in path.parts:
                text = path.read_text(encoding="utf-8")
                assert "genie_tiles_spike" not in text and "genie_tiles_live" not in text, path


def test_a_mode_is_required() -> None:
    with pytest.raises(SystemExit):
        spike.main([])
    with pytest.raises(SystemExit):
        spike.main(["--live", "--space-id", "space-1"])  # no profile


# ------------------------------------------------- --live (W5c), a fake client

LIVE = ROOT / "tools" / "genie_tiles_live.py"
SECRET_QUESTION = "Which borrower named Zyrplax owes the most?"
SECRET_SQL = "SELECT zyrplax_secret_column FROM mip.gold.borrower_360"
SECRET_ROW = "zyrplax-row-value-42"
SECRET_EMAIL = "zyrplax@summit.example"
NOW_S = 1_800_000_000.0
DAY_MS = 86_400_000


class PermissionDenied(Exception):  # the SDK error class name the probe recognizes
    status_code = 403


def _message(message_id: str, age_days: float, *, status: str = "COMPLETED", query: bool = True) -> Any:
    attachments = [SimpleNamespace(attachment_id=f"att-{message_id}", query=SimpleNamespace(query=SECRET_SQL), text=None)]
    return SimpleNamespace(
        message_id=message_id,
        id=message_id,
        status=SimpleNamespace(value=status),
        created_timestamp=int(NOW_S * 1000 - age_days * DAY_MS),
        attachments=attachments if query else [SimpleNamespace(attachment_id="txt", query=None, text=SECRET_QUESTION)],
        content=SECRET_QUESTION,
        user_id=SECRET_EMAIL,
    )


class _Genie:
    def __init__(self, messages: list[Any], *, refuse: bool = False, fail_after_days: float | None = None,
                 status_now: dict[str, str] | None = None) -> None:
        self.messages = {m.message_id: m for m in messages}
        self.refuse = refuse
        self.fail_after_days = fail_after_days
        self.status_now = status_now or {}
        self.executed: list[str] = []

    def get_space(self, space_id: str) -> Any:
        return SimpleNamespace(warehouse_id="wh-1", title=SECRET_QUESTION)

    def list_conversations(self, space_id: str, *, page_size: int, page_token: str | None) -> Any:
        return SimpleNamespace(
            conversations=[SimpleNamespace(conversation_id="conv-1", title=SECRET_QUESTION, created_timestamp=0)],
            next_page_token=None,
        )

    def list_conversation_messages(self, space_id: str, conversation_id: str, *, page_size: int,
                                   page_token: str | None) -> Any:
        return SimpleNamespace(messages=list(self.messages.values()), next_page_token=None)

    def get_message(self, space_id: str, conversation_id: str, message_id: str) -> Any:
        message = self.messages[message_id]
        status = self.status_now.get(message_id)
        return SimpleNamespace(**{**vars(message), "status": SimpleNamespace(value=status)}) if status else message

    def execute_message_attachment_query(self, space_id: str, conversation_id: str, message_id: str,
                                         attachment_id: str) -> Any:
        self.executed.append(message_id)
        if self.refuse:
            raise PermissionDenied(SECRET_EMAIL)
        age_days = (NOW_S * 1000 - self.messages[message_id].created_timestamp) / DAY_MS
        if self.fail_after_days is not None and age_days > self.fail_after_days:
            raise RuntimeError(f"expired {SECRET_SQL}")
        return SimpleNamespace(
            statement_response=SimpleNamespace(
                manifest=SimpleNamespace(total_row_count=3), result=SimpleNamespace(data_array=[[SECRET_ROW]])
            )
        )


class _Client:
    def __init__(self, genie: _Genie, *, state: str = "RUNNING") -> None:
        self.genie = genie
        self.warehouses = SimpleNamespace(
            get=lambda _id: SimpleNamespace(state=SimpleNamespace(value=state), cluster_size="Small")
        )


def _ladder() -> list[Any]:
    return [_message("m-day", 0.5), _message("m-week", 3), _message("m-month", 10), _message("m-old", 40)]


def _probe(clients: dict[str, _Client], **kwargs: Any) -> tuple[dict[str, Any], int]:
    times = iter(float(n) for n in range(10_000))
    return live.measure_live(
        lambda profile: clients[profile],
        profile="me",
        space_id="space-1",
        clock=lambda: NOW_S,
        monotonic=lambda: next(times) * 0.001,
        **kwargs,
    )


def test_live_buckets_completed_query_messages_by_age_only() -> None:
    messages = [*_ladder(), _message("m-failed", 2, status="FAILED"), _message("m-text", 2, query=False)]
    result, _code = _probe({"me": _Client(_Genie(messages))}, max_executions=0)

    assert {name: bucket["completed_with_query"] for name, bucket in result["buckets"].items()} == {
        "le_1d": 1, "1_7d": 1, "7_30d": 1, "gt_30d": 1,
    }
    assert result["executions"] == []


def test_live_executes_only_while_completed_or_executing_and_caps_per_bucket() -> None:
    genie = _Genie(
        [_message(f"m-{n}", 10) for n in range(5)] + [_message("m-gone", 0.5)],
        status_now={"m-0": "EXECUTING_QUERY", "m-gone": "FAILED"},
    )
    result, _code = _probe({"me": _Client(genie)}, max_executions=2)

    month = [e for e in result["executions"] if e["bucket"] == "7_30d"]
    assert [e["outcome"] for e in month] == ["ok", "ok"], "the cap: two per bucket"
    day = [e for e in result["executions"] if e["bucket"] == "le_1d"]
    assert [(e["message_status"], e["outcome"]) for e in day] == [("FAILED", "skipped_state")]
    assert genie.executed == ["m-0", "m-1"]
    assert month[0]["row_count"] == 3 and month[0]["http_status"] == 200


def test_a_cold_warehouse_is_never_woken_without_allow_wake() -> None:
    cold = _Genie(_ladder())
    result, code = _probe({"me": _Client(cold, state="STOPPED")})

    assert {e["outcome"] for e in result["executions"]} == {"skipped_cold"}
    assert cold.executed == []
    assert code == 2 and result["criteria"]["criterion_2"]["verdict"] == "INCONCLUSIVE"

    woken = _Genie(_ladder())
    awake, _ = _probe({"me": _Client(woken, state="STOPPED")}, allow_wake=True)
    assert woken.executed and {e["warehouse_state"] for e in awake["executions"]} == {"STOPPED"}
    assert awake["criteria"]["criterion_4"]["verdict"] == "INCONCLUSIVE", "no warm execute was measured"


def test_isolation_and_exit_codes() -> None:
    clients = {"me": _Client(_Genie(_ladder())), "other": _Client(_Genie(_ladder(), refuse=True))}
    passed, code = _probe(clients, other_profile="other", app_identity=True)

    assert code == 0, passed["criteria"]
    assert passed["isolation"]["outcome"] == "refused" and passed["isolation"]["http_status"] == 403
    assert passed["criteria"]["criterion_3"]["verdict"] == "PASS"

    leaky = {"me": _Client(_Genie(_ladder())), "other": _Client(_Genie(_ladder()))}
    failed, code = _probe(leaky, other_profile="other", app_identity=True)
    assert code == 1 and failed["criteria"]["criterion_3"]["other_identity_refused"] == "FAIL"

    unproven, code = _probe({"me": _Client(_Genie(_ladder()))})
    assert code == 2
    assert unproven["criteria"]["criterion_3"]["app_identity"] == "INCONCLUSIVE"
    assert unproven["criteria"]["criterion_3"]["reason"]

    stale, code = _probe({"me": _Client(_Genie(_ladder(), fail_after_days=7))}, other_profile=None)
    assert stale["criteria"]["criterion_2"]["verdict"] == "FAIL" and code == 1


def test_an_auth_or_network_failure_is_inconclusive() -> None:
    def no_auth(profile: str) -> Any:
        raise PermissionDenied(SECRET_EMAIL)

    result, code = live.measure_live(no_auth, profile="me", space_id="space-1")

    assert code == 2
    assert result["error_class"] == "PermissionDenied"
    assert {c["verdict"] for c in result["criteria"].values()} == {"INCONCLUSIVE"}


def test_the_live_json_never_carries_question_sql_rows_or_emails(tmp_path: Path) -> None:
    out = tmp_path / "live.json"
    clients = {"me": _Client(_Genie(_ladder())), "other": _Client(_Genie(_ladder(), refuse=True))}

    code = spike.main(
        ["--live", "--profile", "me", "--space-id", "space-1", "--other-profile", "other", "--json", str(out)],
        client_factory=lambda profile: clients[profile],
    )
    text = out.read_text(encoding="utf-8")

    assert code in {0, 1, 2}
    assert json.loads(text)["mode"] == "live"
    for sentinel in (SECRET_QUESTION, SECRET_SQL, SECRET_ROW, SECRET_EMAIL, "zyrplax", "Zyrplax"):
        assert sentinel not in text, sentinel
