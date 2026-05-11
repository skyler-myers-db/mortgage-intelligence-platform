"""Guards for Genie degraded-state prompt suggestions.

Production Genie code must never carry a local analytic answer catalog. When
live Genie is down, the app may show an honest degraded message plus suggested
questions, but it must not return hardcoded rows, counts, borrower examples, or
narrative answers from a local catalog.
"""
from __future__ import annotations

from pathlib import Path

from backend.services.genie_answers import (
    default_follow_up_questions,
    load_sample_questions,
    match_sample_question,
)

REPO = Path(__file__).resolve().parents[2]
GENIE_ANSWERS = REPO / "backend" / "services" / "genie_answers.py"
PRODUCTION_BACKEND_FILES = tuple(sorted((REPO / "backend").rglob("*.py")))


def test_sample_question_loader_returns_prompt_suggestions_only() -> None:
    questions = load_sample_questions()
    assert questions
    assert all(q.endswith("?") or q.endswith(".") for q in questions)
    assert default_follow_up_questions(3) == questions[:3]
    assert match_sample_question(questions[0]) == questions[0]


def test_production_genie_answers_module_has_no_canned_answer_catalog() -> None:
    text = GENIE_ANSWERS.read_text(encoding="utf-8")
    forbidden = [
        "_SAFE_CORPUS",
        "fallback" + "-conv",
        "deterministic" + "_fallback",
        "def respond(",
        "def match_intent(",
        "table_rows=[",
        "metric_value=",
        "James & Maria",
        "Thomas Chen",
        "12,840",
        "3,471",
        "1,842",
        "47 outreach",
        "Cost per contact is trending",
    ]
    for needle in forbidden:
        assert needle not in text, f"production Genie module still contains {needle!r}"


def test_production_backend_has_no_moved_genie_answer_catalog() -> None:
    forbidden_literals = [
        "_SAFE_CORPUS",
        "fallback" + "-conv",
        "deterministic" + "_fallback",
        "James & Maria",
        "Thomas Chen",
        "12,840",
        "3,471",
        "1,842",
        "47 outreach",
        "Cost per contact is trending",
        "table_rows=[{",
        'metric_value="',
        "metric_value='",
        "from backend.services.genie_answers import respond",
        "from backend.services.genie_answers import (respond",
        "export_insight",
        "demo-ready insight",
        "tests.fixtures",
        "backend.services.mock_data",
    ]
    for path in PRODUCTION_BACKEND_FILES:
        text = path.read_text(encoding="utf-8")
        for needle in forbidden_literals:
            assert needle not in text, f"{path.relative_to(REPO)} contains {needle!r}"


def test_databricks_repo_does_not_import_genie_answer_catalog() -> None:
    text = (REPO / "backend" / "services" / "repositories" / "databricks_repo.py").read_text(
        encoding="utf-8"
    )
    assert "genie_catalog" + "_respond" not in text
    assert "respond as" not in text


def test_offer_orchestrator_does_not_generate_local_outreach_copy() -> None:
    text = (REPO / "frontend" / "src" / "routes" / "offer-orchestrator.tsx").read_text(
        encoding="utf-8"
    )
    forbidden = [
        "defaultDraft",
        "Default template used",
        "hardcoded template",
        "Hi [first name]",
        "Reply or call",
    ]
    for needle in forbidden:
        assert needle not in text, f"Offer Orchestrator still contains {needle!r}"
    assert "draftReady" in text
    assert "Approval is disabled until the audited outreach draft loads" in text


def test_floating_genie_has_no_ai_shaped_seed_answer() -> None:
    text = (REPO / "frontend" / "src" / "components" / "mortgage" / "GenieChat.tsx").read_text(
        encoding="utf-8"
    )
    assert "useState<ChatMsg[]>([])" in text
    pre_ask = text.split("const ask = async", maxsplit=1)[0]
    assert "payload: {\n        answer:" not in pre_ask
    assert "Answers run against Unity Catalog metric views" not in text
