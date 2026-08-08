"""S4 home summary contracts.

Three layers, pinned separately:

1. ``compose_home_summary`` — deterministic template golden fixtures for
   the delta / first-visit / no-baseline states (the S3 edge contract
   surfaces honestly; no fake deltas).
2. ``validate_genie_phrasing`` — the exact-number guard that keeps model
   output a *rephrasing* of the deterministic template, never a
   recomputation.
3. ``HomeSummaryService`` — gate + cache + honest fallback reasons; the
   Genie turn never runs on the request path.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from backend.config.settings import settings
from backend.schemas.home_summary import HomeSummaryResponse
from backend.schemas.kpi_deltas import HeadlineKpis, KpiDeltaResult, KpiDeltas
from backend.services.home_summary import (
    HomeSummaryService,
    compose_home_summary,
    phrasing_prompt,
    validate_genie_phrasing,
)

_PREVIOUS_VISIT = datetime(2026, 7, 9, 14, 30, 0, tzinfo=UTC)
_SNAPSHOT_AT = datetime(2026, 7, 9, 6, 0, 0, tzinfo=UTC)

_CURRENT = HeadlineKpis(
    marketable_population=5_240_100,
    refi_economics_screen=261_400,
    high_opportunity=88_210,
    offers_available=402_330,
    offers_recommended=310_450,
    avg_opportunity_score=61.5,
)
_BASELINE = HeadlineKpis(
    marketable_population=5_212_000,
    refi_economics_screen=259_150,
    high_opportunity=86_900,
    offers_available=398_210,
    offers_recommended=305_925,
    avg_opportunity_score=60.75,
)
_DELTAS = KpiDeltas(
    marketable_population=28_100,
    refi_economics_screen=2_250,
    high_opportunity=1_310,
    offers_available=4_120,
    offers_recommended=4_525,
    avg_opportunity_score=0.75,
)


def _delta_result() -> KpiDeltaResult:
    return KpiDeltaResult(
        actor_email="lo01@summit.example",
        previous_visit_at=_PREVIOUS_VISIT,
        baseline_snapshot_at=_SNAPSHOT_AT,
        current=_CURRENT,
        baseline=_BASELINE,
        deltas=_DELTAS,
    )


# -- deterministic composer ------------------------------------------------


def test_delta_summary_golden() -> None:
    summary = compose_home_summary(_delta_result())
    assert summary.status == "delta"
    assert summary.previous_visit_at == _PREVIOUS_VISIT
    assert summary.baseline_snapshot_at == _SNAPSHOT_AT
    # +1,310 / 86,900 = +1.5075% -> +1.5%
    assert summary.headline == (
        "Since your last login: +1.5% high-opportunity, "
        "+2,250 refi candidates, +4,120 offers available."
    )
    assert [h.display for h in summary.highlights] == ["+1.5%", "+2,250", "+4,120"]
    assert [h.measure for h in summary.highlights] == [
        "high_opportunity",
        "refi_economics_screen",
        "offers_available",
    ]
    high = summary.highlights[0]
    assert high.current == 88_210
    assert high.baseline == 86_900
    assert high.delta == 1_310
    assert high.delta_pct == 1.5
    assert summary.phrasing_source == "deterministic"
    assert summary.deltas == _DELTAS
    assert summary.baseline == _BASELINE
    assert summary.current_source == "mip.semantics.portfolio_headline_metric_view"
    assert summary.baseline_source == "mip_app.kpi_snapshots"


def test_delta_summary_negative_and_zero_movements() -> None:
    result = _delta_result().model_copy(
        update={
            "deltas": _DELTAS.model_copy(
                update={
                    "high_opportunity": -1_310,
                    "refi_economics_screen": 0,
                    "offers_available": -315,
                }
            )
        }
    )
    summary = compose_home_summary(result)
    assert [h.display for h in summary.highlights] == ["-1.5%", "no change", "-315"]
    assert summary.headline == (
        "Since your last login: -1.5% high-opportunity, "
        "no change in refi candidates, -315 offers available."
    )
    # The delta itself is untouched -- only its rendering changed.
    assert summary.highlights[1].delta == 0


def test_zero_delta_never_reads_as_a_zero_total() -> None:
    """2026-08-07 platform audit F12.

    The live home page opened with "Since your last login: 0
    high-opportunity, 0 refi candidates, 0 offers available." while the book
    held 3,503 / 88,806 / 5,156,184 of them. The deltas were genuinely zero;
    the sentence was not about the deltas, and it is the first thing a buyer
    reads on the home route.
    """
    result = _delta_result().model_copy(
        update={
            "deltas": _DELTAS.model_copy(
                update={
                    "high_opportunity": 0,
                    "refi_economics_screen": 0,
                    "offers_available": 0,
                }
            )
        }
    )
    summary = compose_home_summary(result)

    assert summary.headline == (
        "Since your last login: no change in high-opportunity, "
        "no change in refi candidates, no change in offers available."
    )
    # No bare "0" token anywhere in the sentence a reader could take for a
    # population count.
    assert "0 " not in summary.headline
    # Current totals still ride along for the evidence drawer.
    assert summary.current.high_opportunity == 88_210
    assert [h.current for h in summary.highlights] == [88_210, 261_400, 402_330]
    # Each token still appears exactly once per highlight, so the frontend's
    # exact-substring attachment keeps working.
    for highlight in summary.highlights:
        assert summary.headline.count(f"{highlight.value_token} in {highlight.label}") == 1


def test_delta_summary_zero_baseline_falls_back_to_signed_count() -> None:
    result = _delta_result().model_copy(
        update={
            "baseline": _BASELINE.model_copy(update={"high_opportunity": 0}),
            "deltas": _DELTAS.model_copy(update={"high_opportunity": 42}),
        }
    )
    summary = compose_home_summary(result)
    assert summary.highlights[0].display == "+42"
    assert summary.highlights[0].delta_pct is None


def test_first_visit_summary_has_no_deltas() -> None:
    result = KpiDeltaResult(actor_email="new@summit.example", current=_CURRENT)
    summary = compose_home_summary(result)
    assert summary.status == "first_visit"
    assert summary.previous_visit_at is None
    assert summary.deltas is None
    assert summary.baseline is None
    assert summary.headline == (
        "Welcome — here's your book today: 5,240,100 marketable borrowers, "
        "88,210 high-opportunity, 402,330 offers available."
    )
    assert [h.display for h in summary.highlights] == ["5,240,100", "88,210", "402,330"]
    assert all(h.delta is None for h in summary.highlights)


def test_no_baseline_summary_is_honest_about_pending_snapshots() -> None:
    result = KpiDeltaResult(
        actor_email="lo01@summit.example",
        previous_visit_at=_PREVIOUS_VISIT,
        current=_CURRENT,
    )
    summary = compose_home_summary(result)
    assert summary.status == "no_baseline"
    assert summary.previous_visit_at == _PREVIOUS_VISIT
    assert summary.deltas is None
    assert "baseline is still being captured" in summary.headline
    assert [h.display for h in summary.highlights] == ["5,240,100", "88,210", "402,330"]


# -- Genie phrasing validation ----------------------------------------------


def _highlights() -> list[Any]:
    return compose_home_summary(_delta_result()).highlights


def test_validator_substitutes_tokens_into_placeholder_slots() -> None:
    text = (
        "Great news since you were last here: high-opportunity is up {{0}}, "
        "you have {{1}} refi candidates, and {{2}} offers available."
    )
    assert validate_genie_phrasing(text, _highlights()) == (
        "Great news since you were last here: high-opportunity is up +1.5%, "
        "you have +2,250 refi candidates, and +4,120 offers available."
    )


def test_validator_keeps_attachment_structural_under_reordering() -> None:
    # The model may reorder the slots freely — each slot still carries its
    # OWN highlight's number, so a delta can never land on the wrong KPI.
    text = "You gained {{1}} refi candidates, {{2}} offers available, and {{0}} high-opportunity."
    assert validate_genie_phrasing(text, _highlights()) == (
        "You gained +2,250 refi candidates, +4,120 offers available, "
        "and +1.5% high-opportunity."
    )


def test_validator_rejects_the_reorder_exploit_sentence() -> None:
    # Cross-review B1 regression: under the old bag-of-tokens check this
    # sentence VALIDATED while attaching every delta to the wrong KPI.
    # Model-written digits are now rejected wholesale.
    exploit = (
        "Since your last login: +2,250 high-opportunity and +1.5% more "
        "refi candidates, +4,120 offers available"
    )
    assert validate_genie_phrasing(exploit, _highlights()) is None


def test_validator_normalizes_whitespace() -> None:
    text = "Up {{0}},\n with {{1}} refi candidates   and {{2}} offers."
    assert validate_genie_phrasing(text, _highlights()) == (
        "Up +1.5%, with +2,250 refi candidates and +4,120 offers."
    )


def test_validator_rejects_bad_slots_digits_and_markup() -> None:
    highlights = _highlights()
    # Missing a placeholder.
    assert validate_genie_phrasing("Up {{0}} with {{1}} refi candidates.", highlights) is None
    # Placeholder repeated.
    assert (
        validate_genie_phrasing(
            "Up {{0}} and {{0}}, {{1}} refi candidates, {{2}} offers.", highlights
        )
        is None
    )
    # Unknown slot index.
    assert (
        validate_genie_phrasing(
            "Up {{0}}, {{1}} refi candidates, {{3}} offers available.", highlights
        )
        is None
    )
    # Model-written digit alongside the slots.
    assert (
        validate_genie_phrasing(
            "Up {{0}}, {{1}} refi candidates, {{2}} offers, 7 days left.", highlights
        )
        is None
    )
    # Raw numbers instead of slots.
    assert (
        validate_genie_phrasing(
            "Up +1.5%, +2,250 refi candidates, +4,120 offers available.", highlights
        )
        is None
    )
    # Markup rejected outright (React escapes on render; cached phrasings
    # must be clean too).
    assert (
        validate_genie_phrasing(
            "Up {{0}}, {{1}} refi candidates, {{2}} offers <b>today</b>.", highlights
        )
        is None
    )


def test_validator_rejects_empty_and_oversized_text() -> None:
    highlights = _highlights()
    assert validate_genie_phrasing(None, highlights) is None
    assert validate_genie_phrasing("   ", highlights) is None
    padding = "steady growth ahead " * 20
    oversized = f"{{{{0}}}} {{{{1}}}} {{{{2}}}} {padding}"
    assert validate_genie_phrasing(oversized, highlights) is None


def test_phrasing_prompt_carries_headline_slots_and_tokens() -> None:
    summary = compose_home_summary(_delta_result())
    prompt = phrasing_prompt(summary)
    assert summary.headline in prompt
    for slot in ("{{0}}", "{{1}}", "{{2}}"):
        assert slot in prompt
    for token in ("+1.5%", "+2,250", "+4,120"):
        assert token in prompt
    assert "Do not write any digits" in prompt


# -- service: gate, cache, honest fallbacks ---------------------------------


class _StubDeltas:
    def __init__(self, result: KpiDeltaResult) -> None:
        self.result = result
        self.calls: list[str] = []

    def deltas_for_actor(self, actor_email: str) -> KpiDeltaResult:
        self.calls.append(actor_email)
        return self.result


def _sync_spawn(work: Any) -> None:
    work()


def _service(
    *,
    result: KpiDeltaResult | None = None,
    genie_ask: Any = None,
) -> HomeSummaryService:
    return HomeSummaryService(
        delta_service=_StubDeltas(result or _delta_result()),
        genie_ask=genie_ask,
        spawn=_sync_spawn,
    )


_VALID_REPHRASE = (
    "Since yesterday your book gained {{0}} high-opportunity, "
    "{{1}} refi candidates and {{2}} offers available — nice tailwind."
)
_VALID_REPHRASE_SUBSTITUTED = (
    "Since yesterday your book gained +1.5% high-opportunity, "
    "+2,250 refi candidates and +4,120 offers available — nice tailwind."
)


def test_service_serves_genie_phrasing_after_background_fill(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(settings, "mip_genie_live_first", True)
    asked: list[str] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return _VALID_REPHRASE

    service = _service(genie_ask=ask)
    first = service.summary_for_actor("lo01@summit.example")
    assert first.phrasing_source == "deterministic"
    assert first.phrasing_fallback_reason == "genie_phrasing_pending"
    assert first.headline.startswith("Since your last login:")

    second = service.summary_for_actor("lo01@summit.example")
    assert second.phrasing_source == "genie"
    assert second.headline == _VALID_REPHRASE_SUBSTITUTED
    assert second.phrasing_fallback_reason is None
    # The deterministic numbers are untouched enrichment or not.
    assert second.deltas == _DELTAS
    assert len(asked) == 1  # cached: one Genie turn per template window


def test_service_falls_back_when_genie_writes_its_own_numbers(monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "mip_genie_live_first", True)
    service = _service(
        genie_ask=lambda prompt: "You gained +9.9% high-opportunity overnight!"
    )
    service.summary_for_actor("lo01@summit.example")
    second = service.summary_for_actor("lo01@summit.example")
    assert second.phrasing_source == "deterministic"
    assert second.phrasing_fallback_reason == "genie_numbers_mismatch"
    assert second.headline.startswith("Since your last login:")


def test_service_skips_enrichment_when_tokens_are_not_distinct(monkeypatch: Any) -> None:
    # Two zero deltas render the same "no change" token; the frontend
    # attaches evidence drawers by exact token string, so enrichment stays
    # off to keep number->drawer attachment provable.
    monkeypatch.setattr(settings, "mip_genie_live_first", True)
    asked: list[str] = []
    result = _delta_result().model_copy(
        update={
            "deltas": _DELTAS.model_copy(
                update={"refi_economics_screen": 0, "offers_available": 0}
            )
        }
    )
    service = HomeSummaryService(
        delta_service=_StubDeltas(result),
        genie_ask=lambda p: asked.append(p) or _VALID_REPHRASE,
        spawn=_sync_spawn,
    )
    summary = service.summary_for_actor("lo01@summit.example")
    assert [h.display for h in summary.highlights] == ["+1.5%", "no change", "no change"]
    assert summary.phrasing_source == "deterministic"
    assert summary.phrasing_fallback_reason == "genie_duplicate_tokens"
    assert asked == []


def test_service_falls_back_when_genie_raises(monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "mip_genie_live_first", True)

    def boom(prompt: str) -> str:
        raise RuntimeError("genie down")

    service = _service(genie_ask=boom)
    service.summary_for_actor("lo01@summit.example")
    second = service.summary_for_actor("lo01@summit.example")
    assert second.phrasing_source == "deterministic"
    assert second.phrasing_fallback_reason == "genie_unavailable"


def test_service_respects_emergency_deterministic_posture(monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "mip_genie_live_first", False)
    asked: list[str] = []
    service = _service(genie_ask=lambda p: asked.append(p) or _VALID_REPHRASE)
    summary = service.summary_for_actor("lo01@summit.example")
    assert summary.phrasing_source == "deterministic"
    assert summary.phrasing_fallback_reason == "genie_live_first_disabled"
    assert asked == []


def test_service_reports_not_configured_without_genie_client(monkeypatch: Any) -> None:
    # No injected ask callable and no constructible Genie client -> the gate
    # resolves to an honest "not configured", never a doomed live call.
    import backend.services.genie_client as genie_client_mod

    monkeypatch.setattr(settings, "mip_genie_live_first", True)

    def refuse() -> Any:
        raise RuntimeError("no Genie space configured")

    monkeypatch.setattr(genie_client_mod, "get_genie_client", refuse)
    service = _service(genie_ask=None)
    summary = service.summary_for_actor("lo01@summit.example")
    assert summary.phrasing_source == "deterministic"
    assert summary.phrasing_fallback_reason == "genie_not_configured"


def test_service_reports_not_configured_for_placeholder_space(monkeypatch: Any) -> None:
    import backend.services.genie_client as genie_client_mod

    monkeypatch.setattr(settings, "mip_genie_live_first", True)

    class _PlaceholderClient:
        space_id = "00000000PLACEHOLDER"

    monkeypatch.setattr(
        genie_client_mod, "get_genie_client", lambda: _PlaceholderClient()
    )
    service = _service(genie_ask=None)
    summary = service.summary_for_actor("lo01@summit.example")
    assert summary.phrasing_fallback_reason == "genie_not_configured"


def test_service_never_enriches_welcome_states(monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "mip_genie_live_first", True)
    asked: list[str] = []
    result = KpiDeltaResult(actor_email="new@summit.example", current=_CURRENT)
    service = HomeSummaryService(
        delta_service=_StubDeltas(result),
        genie_ask=lambda p: asked.append(p) or _VALID_REPHRASE,
        spawn=_sync_spawn,
    )
    summary = service.summary_for_actor("new@summit.example")
    assert summary.status == "first_visit"
    assert summary.phrasing_source == "deterministic"
    assert asked == []


def test_summary_response_carries_no_actor_pii() -> None:
    payload = compose_home_summary(_delta_result())
    assert isinstance(payload, HomeSummaryResponse)
    dumped = payload.model_dump_json()
    assert "lo01@summit.example" not in dumped
