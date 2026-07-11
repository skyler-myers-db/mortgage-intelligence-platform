"""S4 personalized home summary over the S3 last-login delta service.

Composition is strictly two-layered:

1. :func:`compose_home_summary` — a PURE deterministic template over
   :class:`~backend.schemas.kpi_deltas.KpiDeltaResult`. This is the
   source of truth: every number the UI shows is a ``display`` token
   minted here, and the S3 edge states (first visit, empty snapshot
   table) surface honestly as welcome copy with live numbers only —
   never fake deltas.
2. Optional Genie phrasing — enrichment ONLY. Gated on the existing
   Genie posture (``mip_genie_live_first``) and the configured Genie
   space (same placeholder check the Genie client boots with); model
   output may only rephrase the deterministic sentence and is rejected
   unless every deterministic token appears verbatim exactly once and
   no other digits appear. Any gate/transport/validation failure falls
   back to the deterministic template with an honest
   ``phrasing_fallback_reason`` (the growth co-pilot's vocabulary).

The Genie turn never runs on the request path: the first request past
the cache returns the deterministic sentence immediately (reason
``genie_phrasing_pending``) and a daemon thread fills a short-TTL cache
for subsequent loads, mirroring the visit-tracking middleware's
off-the-request-path write.
"""
from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable
from threading import Lock, Thread

from backend.schemas.home_summary import (
    HomeSummaryHighlight,
    HomeSummaryResponse,
)
from backend.schemas.kpi_deltas import KpiDeltaResult
from backend.services.observability import emit
from backend.services.resilience import TTLCache

log = logging.getLogger(__name__)

#: Delta measures the summary sentence calls out, in render order, with
#: their display style: high-opportunity movement reads best as a percent
#: ("+1.5% high-opportunity"), the pipeline measures as signed counts
#: ("+2,250 refi candidates"). The full headline set stays available on
#: ``current``/``deltas``.
SUMMARY_DELTA_MEASURES: tuple[tuple[str, str, str], ...] = (
    ("high_opportunity", "high-opportunity", "pct"),
    ("refi_economics_screen", "refi candidates", "count"),
    ("offers_available", "offers available", "count"),
)

#: Current-state measures shown on first visit / pre-backfill installs.
SUMMARY_CURRENT_MEASURES: tuple[tuple[str, str], ...] = (
    ("marketable_population", "marketable borrowers"),
    ("high_opportunity", "high-opportunity"),
    ("offers_available", "offers available"),
)

_PHRASING_CACHE_TTL_S = 1800.0
_PHRASING_FAILURE_TTL_S = 120.0
_PHRASING_MAX_LEN = 300


def _fmt_count(value: int | float) -> str:
    return f"{int(value):,}"


def _fmt_signed(value: int | float) -> str:
    return f"{int(value):+,}" if int(value) != 0 else "0"


def _delta_highlight(
    measure: str,
    label: str,
    style: str,
    *,
    current: int,
    baseline: int,
    delta: int,
) -> HomeSummaryHighlight:
    """Signed since-last-login movement. ``style="pct"`` renders a signed
    percent when the baseline supports one (non-zero baseline, |pct| >=
    0.1 after rounding); everything else renders a signed count."""
    delta_pct: float | None = None
    if baseline > 0:
        delta_pct = round(delta / baseline * 100, 1)
    display = _fmt_signed(delta)
    if style == "pct" and delta != 0 and delta_pct is not None and abs(delta_pct) >= 0.1:
        display = f"{delta_pct:+.1f}%"
    return HomeSummaryHighlight(
        measure=measure,
        label=label,
        display=display,
        value_token=display,
        current=current,
        baseline=baseline,
        delta=delta,
        delta_pct=delta_pct,
    )


def _current_highlight(measure: str, label: str, *, current: int) -> HomeSummaryHighlight:
    display = _fmt_count(current)
    return HomeSummaryHighlight(
        measure=measure,
        label=label,
        display=display,
        value_token=display,
        current=current,
    )


def _fragments(highlights: list[HomeSummaryHighlight]) -> str:
    return ", ".join(f"{h.display} {h.label}" for h in highlights)


def compose_home_summary(result: KpiDeltaResult) -> HomeSummaryResponse:
    """Deterministic template over the S3 delta result (pure; pinned by
    tests/unit/test_home_summary.py)."""
    current = result.current
    if result.deltas is not None and result.baseline is not None:
        highlights = [
            _delta_highlight(
                measure,
                label,
                style,
                current=getattr(current, measure),
                baseline=getattr(result.baseline, measure),
                delta=getattr(result.deltas, measure),
            )
            for measure, label, style in SUMMARY_DELTA_MEASURES
        ]
        return HomeSummaryResponse(
            status="delta",
            previous_visit_at=result.previous_visit_at,
            baseline_snapshot_at=result.baseline_snapshot_at,
            headline=f"Since your last login: {_fragments(highlights)}.",
            highlights=highlights,
            current=current,
            baseline=result.baseline,
            deltas=result.deltas,
        )

    current_highlights = [
        _current_highlight(measure, label, current=getattr(current, measure))
        for measure, label in SUMMARY_CURRENT_MEASURES
    ]
    if result.previous_visit_at is None:
        return HomeSummaryResponse(
            status="first_visit",
            headline=(
                f"Welcome — here's your book today: {_fragments(current_highlights)}."
            ),
            highlights=current_highlights,
            current=current,
        )
    # Previous visit exists but the snapshot table is empty (pre-backfill
    # install; transient once the deploy script's backfill step runs).
    return HomeSummaryResponse(
        status="no_baseline",
        previous_visit_at=result.previous_visit_at,
        headline=(
            "Welcome back — your last-login baseline is still being captured, "
            "so deltas arrive after the next daily KPI snapshot. Today: "
            f"{_fragments(current_highlights)}."
        ),
        highlights=current_highlights,
        current=current,
    )


# -- Genie phrasing (optional enrichment) --------------------------------


def phrasing_prompt(summary: HomeSummaryResponse) -> str:
    tokens = ", ".join(h.value_token for h in summary.highlights)
    return (
        "Rephrase the portfolio update below as ONE friendly sentence for a "
        "mortgage growth leader opening their dashboard. You MUST keep these "
        f"figures verbatim, each exactly once, including any +/- sign: {tokens}. "
        "Do not add, recompute, or round any other number, do not run a query, "
        "and do not name any borrower. Update: "
        f"{summary.headline}"
    )


def _token_pattern(token: str) -> re.Pattern[str]:
    # Boundary guards: the token must not extend a larger number on either
    # side ("2,250" inside "12,250", or followed by ",345" / ".5" / "%",
    # which would silently change the magnitude or turn a count into a
    # percentage). Percent tokens already end in a literal %.
    if token.endswith("%"):
        return re.compile(rf"(?<![\d.,]){re.escape(token[:-1])}%")
    return re.compile(rf"(?<![\d.,]){re.escape(token)}(?![.,]?\d)(?!%)")


def validate_genie_phrasing(
    text: str | None, highlights: list[HomeSummaryHighlight]
) -> str | None:
    """Return the normalized Genie sentence only if it carries EXACTLY the
    deterministic tokens (each once, signs intact) and no other digits."""
    if not text:
        return None
    normalized = " ".join(text.split())
    if not normalized or len(normalized) > _PHRASING_MAX_LEN:
        return None
    remaining = normalized
    # Longest token first so "2,250" can't consume part of "12,250".
    for token in sorted((h.value_token for h in highlights), key=len, reverse=True):
        pattern = _token_pattern(token)
        if len(pattern.findall(remaining)) != 1:
            return None
        remaining = pattern.sub(" ", remaining, count=1)
    if re.search(r"\d", remaining):
        return None
    return normalized


class _PhrasingFailure:
    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        self.reason = reason


def _default_spawn(work: Callable[[], None]) -> None:
    Thread(target=work, daemon=True, name="mip-home-summary-phrasing").start()


def _default_genie_ask(question: str) -> str:
    from backend.services.genie_client import get_genie_client

    return get_genie_client().ask(question).answer_text


class HomeSummaryService:
    """Typed summary surface for the home route.

    Delegates all delta math to the injected S3
    :class:`~backend.services.kpi_deltas.KpiDeltaService` (never
    reimplements it) and layers the deterministic template + optional
    Genie phrasing on top.
    """

    def __init__(
        self,
        delta_service: object | None = None,
        *,
        genie_ask: Callable[[str], str] | None = None,
        cache: TTLCache | None = None,
        spawn: Callable[[Callable[[], None]], None] | None = None,
        phrasing_ttl_s: float = _PHRASING_CACHE_TTL_S,
        failure_ttl_s: float = _PHRASING_FAILURE_TTL_S,
    ) -> None:
        self._delta_service = delta_service
        self._genie_ask = genie_ask
        self._cache = cache if cache is not None else TTLCache(max_entries=32)
        self._spawn = spawn if spawn is not None else _default_spawn
        self._phrasing_ttl_s = phrasing_ttl_s
        self._failure_ttl_s = failure_ttl_s
        self._inflight: set[str] = set()
        self._inflight_lock = Lock()

    def _deltas(self) -> object:
        if self._delta_service is None:
            from backend.services.kpi_deltas import get_kpi_delta_service

            self._delta_service = get_kpi_delta_service()
        return self._delta_service

    # -- gate -------------------------------------------------------------

    def _phrasing_gate(self) -> str | None:
        """Honest reason phrasing stays deterministic, or None to proceed.

        Mirrors the capability posture the rest of the Genie stack uses:
        the emergency ``mip_genie_live_first=False`` booth posture keeps
        every Genie surface deterministic, and a missing/placeholder space
        id resolves to "not configured" instead of a doomed live call.
        """
        from backend.config.settings import settings

        if not settings.mip_genie_live_first:
            return "genie_live_first_disabled"
        if self._genie_ask is not None:
            return None
        try:
            from backend.services.genie_client import (
                get_genie_client,
                is_placeholder_space_id,
            )

            client = get_genie_client()
            if is_placeholder_space_id(client.space_id):
                return "genie_not_configured"
        except Exception:  # noqa: BLE001 - missing creds/config is a posture, not an error
            return "genie_not_configured"
        return None

    # -- enrichment ---------------------------------------------------------

    def _fetch_phrasing(self, summary: HomeSummaryResponse) -> str | None:
        ask = self._genie_ask or _default_genie_ask
        return validate_genie_phrasing(ask(phrasing_prompt(summary)), summary.highlights)

    def _enrich_in_background(self, key: str, summary: HomeSummaryResponse) -> None:
        with self._inflight_lock:
            if key in self._inflight:
                return
            self._inflight.add(key)

        def work() -> None:
            try:
                phrased = self._fetch_phrasing(summary)
                if phrased is None:
                    self._cache.set(
                        key, _PhrasingFailure("genie_numbers_mismatch"), ttl_s=self._failure_ttl_s
                    )
                else:
                    self._cache.set(key, phrased, ttl_s=self._phrasing_ttl_s)
            except Exception as exc:  # noqa: BLE001 - enrichment must never break the summary
                emit(
                    log,
                    "home_summary_phrasing_failed",
                    level=logging.WARNING,
                    dependency="genie",
                    outcome="deterministic_template",
                    error_type=type(exc).__name__,
                )
                self._cache.set(
                    key, _PhrasingFailure("genie_unavailable"), ttl_s=self._failure_ttl_s
                )
            finally:
                with self._inflight_lock:
                    self._inflight.discard(key)

        self._spawn(work)

    # -- the route surface ---------------------------------------------------

    def summary_for_actor(self, actor_email: str) -> HomeSummaryResponse:
        result: KpiDeltaResult = self._deltas().deltas_for_actor(actor_email)  # type: ignore[attr-defined]
        summary = compose_home_summary(result)
        if summary.status != "delta":
            # Welcome states stay deterministic by design (no numbers to
            # rephrase honestly without a baseline story).
            return summary
        reason = self._phrasing_gate()
        if reason is not None:
            return summary.model_copy(update={"phrasing_fallback_reason": reason})
        key = "home_summary.phrasing." + hashlib.sha256(
            summary.headline.encode("utf-8")
        ).hexdigest()
        cached = self._cache.get(key)
        if isinstance(cached, str):
            return summary.model_copy(
                update={"headline": cached, "phrasing_source": "genie"}
            )
        if isinstance(cached, _PhrasingFailure):
            return summary.model_copy(update={"phrasing_fallback_reason": cached.reason})
        self._enrich_in_background(key, summary)
        return summary.model_copy(
            update={"phrasing_fallback_reason": "genie_phrasing_pending"}
        )


_SERVICE: HomeSummaryService | None = None
_SERVICE_LOCK = Lock()


def get_home_summary_service() -> HomeSummaryService:
    """Process-singleton accessor (mirrors the other service factories)."""
    global _SERVICE
    if _SERVICE is None:
        with _SERVICE_LOCK:
            if _SERVICE is None:
                _SERVICE = HomeSummaryService()
    return _SERVICE


def _reset_service_for_tests() -> None:
    global _SERVICE
    with _SERVICE_LOCK:
        _SERVICE = None
