"""Admin read service -- sources offer-rule thresholds and per-table
readiness from Unity Catalog.

Two callers:

* ``GET /api/admin/rules`` reads `mip.ref.offer_rules_config` and injects
  the operating market-rate row from `mip.gold.borrower_360`, which is the
  gold surface that already consumed the latest FRED MORTGAGE30US row.
  The response returns one row per threshold/source value (key, value,
  unit, label, description, updated_at) plus an ``offer_rules_version``
  derived from a stable hash of the payload + the max ``last_updated``
  across rows.
* ``GET /api/admin/sources`` reads ONLY the non-PII
  ``mip.gold.source_readiness`` summary produced by the gold refresh job
  (App SQL is gold-only). A source the summary has no row for, or every
  non-roadmap source when the summary is absent, reads ``unavailable``
  (``readiness unavailable`` in the UI); the App never probes
  ``mip.silver.*``, ``mip.first_party.*`` or the provider catalog.

Both read paths:

* Wrap every warehouse call in a short (``60s`` default) TTLCache so
  repeat hits from the admin panel don't re-query on every render.
* Propagate ``DependencyDownError`` / ``DatabricksSqlError`` to the
  router, which translates them to HTTP 503 with a muted UI message --
  no silent fallback to fabricated data.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from backend.services.databricks_sql import DatabricksSqlObjectMissingError
from backend.services.databricks_sql_helpers import qualify
from backend.services.observability import emit
from backend.services.resilience import TTLCache

log = logging.getLogger(__name__)

_OPERATING_MARKET_RATE_SQL = (
    "SELECT CAST(market_rate_fraction AS DOUBLE) AS rate_fraction, "
    "CAST(refreshed_at AS STRING) AS last_updated "
    f"FROM {qualify('gold', 'borrower_360')} "
    "WHERE market_rate_fraction IS NOT NULL "
    "LIMIT 1"
)

# ---------------------------------------------------------------------------
# Per-source descriptors -- one row per panel entry. Readiness itself comes
# only from ``mip.gold.source_readiness``; a descriptor carries the name the
# summary must have a row for, the panel note, and whether the source is on
# the roadmap (no UC object exists yet).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _SourceDescriptor:
    name: str
    note: str
    roadmap: bool = False


# Ordering mirrors the admin panel's prior literal list so the UI visual
# order is preserved post-migration.
_SOURCES: tuple[_SourceDescriptor, ...] = (
    _SourceDescriptor(
        name="Cotality Public Records",
        note="Delta Share · nightly",
    ),
    _SourceDescriptor(
        name="Voluntary Lien",
        note="Delta Share · nightly",
    ),
    _SourceDescriptor(
        name="MMA Mortgage Analytics",
        note="Delta Share · nightly",
    ),
    _SourceDescriptor(
        name="CLIP",
        note="Mastered property id",
    ),
    _SourceDescriptor(
        name="Owner Link",
        note="Mastered owner graph",
    ),
    _SourceDescriptor(
        name="AVM",
        note="Delta Share · weekly",
    ),
    _SourceDescriptor(
        name="FRED Market Rates",
        note="Public FRED MORTGAGE30US · weekly",
    ),
    _SourceDescriptor(
        name="First-party LOS / Applications",
        note="Customer LOS/application feed · optional",
    ),
    _SourceDescriptor(
        name="First-party Servicing Portfolio",
        note="Customer servicing feed · optional",
    ),
    _SourceDescriptor(
        name="First-party CRM / Campaigns",
        note="Customer CRM/campaign feed · optional",
    ),
    _SourceDescriptor(
        name="First-party Customer Interactions",
        note="Customer call-center/digital feed · optional",
    ),
    _SourceDescriptor(
        name="First-party Product Balances",
        note="Customer banking-product feed · optional",
    ),
    _SourceDescriptor(
        name="MLS Listings",
        note="Cotality MLS listing feed · current active/under-contract rows drive listed_for_sale",
    ),
    _SourceDescriptor(
        name="Cotality HELOC Propensity",
        note="Cotality HELOC propensity model feed · drives HELOC Intent; not a permit filing source",
    ),
    _SourceDescriptor(
        name="Cotality Refi Propensity",
        note="Cotality refinance propensity model feed · enriches refi timing evidence",
    ),
    _SourceDescriptor(
        name="Building Permits",
        note="Contracted · pending load",
        roadmap=True,
    ),
)

_SOURCE_READINESS_SQL = (
    "SELECT source_name, status, row_count, "
    "CAST(last_updated AS STRING) AS last_updated, note, "
    "CAST(checked_at AS STRING) AS checked_at, "
    "COALESCE(synthetic_demo, FALSE) AS synthetic_demo, sort_order "
    f"FROM {qualify('gold', 'source_readiness')} "
    "ORDER BY sort_order"
)
_NO_READINESS_ROW_NOTE = "readiness summary has no row for this source"

# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThresholdRow:
    key: str
    value: float
    unit: str | None
    label: str | None
    description: str | None
    sort_order: int | None
    last_updated: str | None  # ISO-8601 string


@dataclass(frozen=True)
class RulesPayload:
    """Response shape for ``GET /api/admin/rules``."""

    offer_rules_version: str
    rules_edited_at: str | None  # ISO-8601 max(last_updated) across rows
    thresholds: tuple[ThresholdRow, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "offer_rules_version": self.offer_rules_version,
            "rules_edited_at": self.rules_edited_at,
            "thresholds": [
                {
                    "key": t.key,
                    "value": t.value,
                    "unit": t.unit,
                    "label": t.label,
                    "description": t.description,
                    "sort_order": t.sort_order,
                    "last_updated": t.last_updated,
                }
                for t in self.thresholds
            ],
        }


@dataclass(frozen=True)
class SourceRow:
    name: str
    status: str  # 'live' | 'demo_synthetic' | 'configured_empty' | 'not_configured' | 'roadmap' | 'permission_denied' | 'error' | 'unavailable'
    rows: int | None
    last_updated: str | None
    note: str
    checked_at: str | None = None
    synthetic_demo: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "rows": self.rows,
            "last_updated": self.last_updated,
            "note": self.note,
            "checked_at": self.checked_at,
            "synthetic_demo": self.synthetic_demo,
        }


class AdminRulesService:
    """Reader for governed admin data.

    The service holds two TTL caches (rules, sources) so a burst of
    admin-page renders issues at most one warehouse round-trip per cache
    window. Cache misses bubble up the full exception surface --
    ``DependencyDownError`` when the breaker is open, or the underlying
    ``DatabricksSqlError`` for a hard failure -- and the router turns
    those into a 503 so the UI can show a muted "temporarily
    unavailable" notice instead of silently falling back to literals.
    """

    _RULES_CACHE_KEY = "rules"
    _SOURCES_CACHE_KEY = "sources"

    def __init__(
        self,
        sql_client: Any,
        *,
        cache_ttl_s: float = 60.0,
        cache: TTLCache | None = None,
    ) -> None:
        self._sql = sql_client
        self._ttl = cache_ttl_s
        self._cache = cache if cache is not None else TTLCache()

    # ---- Offer rules -----------------------------------------------

    def get_rules(self) -> RulesPayload:
        cached = self._cache.get(self._RULES_CACHE_KEY)
        if cached is not None:
            return cached
        payload = self._load_rules()
        self._cache.set(self._RULES_CACHE_KEY, payload, self._ttl)
        return payload

    def _load_rules(self) -> RulesPayload:
        rows = self._sql.execute(
            "SELECT key, value, unit, label, description, sort_order, "
            "CAST(last_updated AS STRING) AS last_updated "
            f"FROM {qualify('ref', 'offer_rules_config')} "
            "ORDER BY sort_order NULLS LAST, key"
        )
        thresholds = tuple(
            ThresholdRow(
                key=str(r["key"]),
                value=float(r["value"]) if r.get("value") is not None else 0.0,
                unit=_opt_str(r.get("unit")),
                label=_opt_str(r.get("label")),
                description=_opt_str(r.get("description")),
                sort_order=_opt_int(r.get("sort_order")),
                last_updated=_opt_str(r.get("last_updated")),
            )
            for r in rows
        )
        latest_market_rate = self._latest_market_rate()
        if latest_market_rate is not None:
            rate_value, rate_updated_at = latest_market_rate
            saw_market_rate = any(t.key == "mip_market_rate" for t in thresholds)
            thresholds = tuple(
                ThresholdRow(
                    key=t.key,
                    value=rate_value,
                    unit=t.unit,
                    label=t.label,
                    description="Operating market rate used by gold rate-spread calculations; sourced from FRED MORTGAGE30US during gold refresh.",
                    sort_order=t.sort_order,
                    last_updated=rate_updated_at or t.last_updated,
                )
                if t.key == "mip_market_rate"
                else t
                for t in thresholds
            )
            if not saw_market_rate:
                thresholds = (
                    *thresholds,
                    ThresholdRow(
                        key="mip_market_rate",
                        value=rate_value,
                        unit="rate_fraction",
                        label="Market rate reference",
                        description="Operating market rate used by gold rate-spread calculations; sourced from FRED MORTGAGE30US during gold refresh.",
                        sort_order=6,
                        last_updated=rate_updated_at,
                    ),
                )
        # Deterministic version: hash of the (key, value) pairs. A seed
        # update that changes any value bumps the hash; a pure metadata
        # edit (description / label) does not. Pairs are sorted by key
        # so ordering differences don't move the hash.
        body = json.dumps(
            [(t.key, t.value) for t in sorted(thresholds, key=lambda x: x.key)],
            separators=(",", ":"),
        )
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
        version = f"itm_{digest}"
        # rules_edited_at = max last_updated across rows (string compare
        # works for ISO-8601 TIMESTAMP CAST output).
        edited_at = None
        stamps = [t.last_updated for t in thresholds if t.last_updated]
        if stamps:
            edited_at = max(stamps)
        return RulesPayload(
            offer_rules_version=version,
            rules_edited_at=edited_at,
            thresholds=thresholds,
        )

    def _latest_market_rate(self) -> tuple[float, str | None] | None:
        rows = self._sql.execute(_OPERATING_MARKET_RATE_SQL)
        if not rows:
            return None
        row = rows[0]
        if row.get("rate_fraction") is None:
            return None
        return float(row["rate_fraction"]), _opt_str(row.get("last_updated"))

    # ---- Data source readiness -------------------------------------

    def get_sources(self) -> tuple[SourceRow, ...]:
        cached = self._cache.get(self._SOURCES_CACHE_KEY)
        if cached is not None:
            return cached
        payload = self._load_sources()
        self._cache.set(self._SOURCES_CACHE_KEY, payload, self._ttl)
        return payload

    def _load_sources(self) -> tuple[SourceRow, ...]:
        """Read ``mip.gold.source_readiness`` once; never probe silver or first_party.

        * Complete: the summary rows as they are.
        * Partial: the present rows as they are, then one ``unavailable`` row
          per expected source the summary has no row for.
        * Absent (no rows, or the table itself is missing): every non-roadmap
          source is ``unavailable``; roadmap sources stay ``roadmap``.
        * Any other failure propagates, so the router answers 503.

        ETL has silver access and writes this non-PII summary into gold; the
        Databricks App principal reads only gold.
        """
        try:
            rows = self._sql.execute(_SOURCE_READINESS_SQL) or []
        except Exception as exc:
            if not _names_missing_source_readiness(exc):
                raise
            rows = []
        if not rows:
            absent = tuple(
                _placeholder_row(desc, status="roadmap" if desc.roadmap else "unavailable")
                for desc in _SOURCES
            )
            _warn_readiness_unavailable(
                "absent", sum(1 for row in absent if row.status == "unavailable")
            )
            return absent

        present = tuple(
            SourceRow(
                name=str(r.get("source_name")),
                status=str(r.get("status") or "error"),
                rows=_opt_int(r.get("row_count")),
                last_updated=_opt_str(r.get("last_updated")),
                note=_opt_str(r.get("note")) or "",
                checked_at=_opt_str(r.get("checked_at")),
                synthetic_demo=bool(r.get("synthetic_demo")),
            )
            for r in sorted(rows, key=lambda r: _opt_int(r.get("sort_order")) or 999)
        )
        # Extra rows are allowed: /api/data-estate consumes gold/runtime proof
        # rows added after the original Admin source list.
        names = {row.name for row in present}
        missing = tuple(
            _placeholder_row(desc, status="unavailable")
            for desc in _SOURCES
            if desc.name not in names
        )
        if missing:
            _warn_readiness_unavailable("partial", len(missing))
        return present + missing


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _opt_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v)
    return s if s else None


def _opt_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _placeholder_row(desc: _SourceDescriptor, *, status: str) -> SourceRow:
    note = f"{desc.note} · {_NO_READINESS_ROW_NOTE}" if status == "unavailable" else desc.note
    return SourceRow(
        name=desc.name,
        status=status,
        rows=None,
        last_updated=None,
        note=note,
        checked_at=None,
        synthetic_demo=False,
    )


def _names_missing_source_readiness(exc: BaseException) -> bool:
    """True when the failure is ``gold.source_readiness`` itself not existing.

    The resilient SQL client wraps the warehouse error in a
    ``DependencyDownError`` (``last_error``); the raw client raises it bare.
    Either way the typed ``DatabricksSqlObjectMissingError`` is on the chain.
    Any other missing object (a missing gold schema, say) is a real failure.
    """
    seen: set[int] = set()
    node: BaseException | None = exc
    while node is not None and id(node) not in seen:
        seen.add(id(node))
        if isinstance(node, DatabricksSqlObjectMissingError) and "source_readiness" in str(node):
            return True
        next_node = getattr(node, "last_error", None)
        node = next_node if isinstance(next_node, BaseException) else node.__cause__
    return False


def _warn_readiness_unavailable(outcome: str, missing_count: int) -> None:
    emit(
        log,
        "admin_source_readiness_unavailable",
        level=logging.WARNING,
        dependency="warehouse",
        outcome=outcome,
        missing_count=missing_count,
    )


# ---------------------------------------------------------------------------
# Process-lifetime singleton + FastAPI dependency
# ---------------------------------------------------------------------------


_SERVICE: AdminRulesService | None = None


def get_admin_rules_service() -> AdminRulesService:
    """Return the process-wide admin-rules service.

    Lazy so test processes that inject a stub via
    ``app.dependency_overrides[get_admin_rules_service]`` never touch
    ``get_sql_client()`` and never need real credentials.
    """
    global _SERVICE
    if _SERVICE is not None:
        return _SERVICE
    from backend.services.databricks_sql import get_sql_client

    _SERVICE = AdminRulesService(get_sql_client())
    return _SERVICE


def _reset_admin_rules_service_for_tests() -> None:
    """Test helper -- drops the singleton so tests can reinstall fresh
    stubs. NOT for production use."""
    global _SERVICE
    _SERVICE = None
