"""Admin read service -- sources offer-rule thresholds and per-table
readiness from Unity Catalog.

Two callers:

* ``GET /api/admin/rules`` reads `mip.ref.offer_rules_config` and returns
  one row per threshold (key, value, unit, label, description,
  updated_at) plus an ``offer_rules_version`` derived from a stable hash
  of the ruleset payload + the max ``last_updated`` across rows. The
  hash means a single seed edit bumps the version deterministically
  without needing an explicit schema migration.
* ``GET /api/admin/sources`` reads the non-PII
  ``mip.gold.source_readiness`` summary produced by the gold refresh job.
  If that summary has not been deployed yet, the service falls back to the
  legacy per-table probes and degrades per-source. The running app should
  not need direct ``mip.silver.*`` grants for the Admin panel.

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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from backend.services.databricks_sql_helpers import qualify
from backend.services.resilience import TTLCache

# ---------------------------------------------------------------------------
# Per-source descriptors -- one row per panel entry. The ``uc_table`` column
# is ``None`` for roadmap sources (no UC object exists yet). For live sources
# the readiness read is a single ``DESCRIBE DETAIL`` call; no row scans.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _SourceDescriptor:
    name: str
    note: str
    uc_table: str | None  # None => roadmap
    # Slice-13 admin-sources follow-up: when we add an MLS / permits
    # ingest, wire the table here and status flips to LIVE automatically
    # once the row count > 0.


# Ordering mirrors the admin panel's prior literal list so the UI visual
# order is preserved post-migration.
_SOURCES: tuple[_SourceDescriptor, ...] = (
    _SourceDescriptor(
        name="Cotality Public Records",
        note="Delta Share · nightly",
        uc_table=qualify("silver", "property_master"),
    ),
    _SourceDescriptor(
        name="Voluntary Lien",
        note="Delta Share · nightly",
        uc_table=qualify("silver", "lien_current"),
    ),
    _SourceDescriptor(
        name="MMA Mortgage Analytics",
        note="Delta Share · nightly",
        uc_table=qualify("silver", "mortgage_events"),
    ),
    _SourceDescriptor(
        name="CLIP",
        note="Mastered property id",
        uc_table=qualify("silver", "property_master"),
    ),
    _SourceDescriptor(
        name="Owner Link",
        note="Mastered owner graph",
        uc_table=qualify("silver", "owner_property_bridge"),
    ),
    _SourceDescriptor(
        name="AVM",
        note="Delta Share · weekly",
        uc_table=qualify("silver", "market_rates_weekly"),
    ),
    _SourceDescriptor(
        name="MLS",
        note="Contracted · pending load",
        uc_table=None,
    ),
    _SourceDescriptor(
        name="Building Permits",
        note="Contracted · pending load",
        uc_table=None,
    ),
)


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
    status: str  # 'live' | 'roadmap' | 'permission_denied' | 'error'
    rows: int | None
    last_updated: str | None
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "rows": self.rows,
            "last_updated": self.last_updated,
            "note": self.note,
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
            return cached  # type: ignore[no-any-return]
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

    # ---- Data source readiness -------------------------------------

    def get_sources(self) -> tuple[SourceRow, ...]:
        cached = self._cache.get(self._SOURCES_CACHE_KEY)
        if cached is not None:
            return cached  # type: ignore[no-any-return]
        payload = self._load_sources()
        self._cache.set(self._SOURCES_CACHE_KEY, payload, self._ttl)
        return payload

    def _load_sources(self) -> tuple[SourceRow, ...]:
        summary_rows = self._try_load_source_readiness_summary()
        if summary_rows is not None:
            return summary_rows

        # Parallelise the per-source probes. Each live source issues up
        # to two warehouse round-trips (DESCRIBE DETAIL, optional COUNT)
        # and the serial version paid 8 * (describe + count) ~= 16 round
        # trips on every cache miss, which dominated the admin page's
        # p95. A thread pool collapses the wall-clock to roughly the
        # slowest single source. The "degrade per-source" contract is
        # preserved by catching exceptions inside the worker so one
        # failing table never aborts the batch.
        #
        # ``max_workers=8`` matches ``len(_SOURCES)``; roadmap entries
        # return inline without issuing a future, so the pool is only
        # sized for the live probes.
        results: dict[int, SourceRow] = {}
        live_indices: list[int] = []
        for idx, desc in enumerate(_SOURCES):
            if desc.uc_table is None:
                results[idx] = SourceRow(
                    name=desc.name,
                    status="roadmap",
                    rows=None,
                    last_updated=None,
                    note=desc.note,
                )
            else:
                live_indices.append(idx)

        if live_indices:
            with ThreadPoolExecutor(max_workers=8) as executor:
                future_by_idx = {
                    idx: executor.submit(self._probe_source, _SOURCES[idx])
                    for idx in live_indices
                }
                for idx, future in future_by_idx.items():
                    results[idx] = future.result()

        return tuple(results[i] for i in range(len(_SOURCES)))

    def _try_load_source_readiness_summary(self) -> tuple[SourceRow, ...] | None:
        """Read the gold-layer source-readiness snapshot when available.

        This is the preferred production path: ETL has silver access, writes
        a non-PII summary into gold, and the Databricks App principal reads
        only gold. Returning ``None`` means "summary unavailable; use the
        legacy direct-probe fallback" so older deployments keep rendering.
        """
        try:
            rows = self._sql.execute(
                "SELECT source_name, status, row_count, "
                "CAST(last_updated AS STRING) AS last_updated, note, sort_order "
                f"FROM {qualify('gold', 'source_readiness')} "
                "ORDER BY sort_order"
            )
        except Exception:  # noqa: BLE001 -- fallback preserves legacy deploys
            return None
        if not rows:
            return None

        by_name = {str(r.get("source_name")) for r in rows}
        expected = {desc.name for desc in _SOURCES}
        if by_name != expected:
            # A partial/mismatched table is more dangerous than the fallback:
            # the Admin panel would look green while omitting a contracted
            # source. Use the legacy probe path until the CTAS is corrected.
            return None

        return tuple(
            SourceRow(
                name=str(r.get("source_name")),
                status=str(r.get("status") or "error"),
                rows=_opt_int(r.get("row_count")),
                last_updated=_opt_str(r.get("last_updated")),
                note=_opt_str(r.get("note")) or "",
            )
            for r in sorted(rows, key=lambda r: _opt_int(r.get("sort_order")) or 999)
        )

    def _probe_source(self, desc: _SourceDescriptor) -> SourceRow:
        """Probe a single live source. Never raises -- returns a
        degraded SourceRow on failure so one slow/denied table cannot
        block the others when called from the thread pool."""
        assert desc.uc_table is not None  # enforced by caller
        # Per-source try/except: if the app identity doesn't have
        # USE SCHEMA / SELECT on a specific table (e.g. silver) we
        # surface that source as "permission_denied" rather than
        # 503-ing the entire /api/admin/sources call. A customer's
        # app identity may only have GRANTs on gold; admin sources
        # should degrade gracefully. (E2E verifier 2026-04-23 caught
        # this — `mip.silver.*` denied to workspace identity.)
        try:
            rows, last_mod = self._describe_detail(desc.uc_table)
            return SourceRow(
                name=desc.name,
                status="live",
                rows=rows,
                last_updated=last_mod,
                note=desc.note,
            )
        except Exception as exc:  # noqa: BLE001 -- degrade-per-source contract
            msg = str(exc)
            # Detect permission denial vs. other SQL errors distinctly so
            # the admin UI can show a clear "grant needed" vs. "transient
            # error" banner per source. Everything else surfaces as an
            # unknown status with the raw error clipped to 200 chars.
            is_permission = "PERMISSION_DENIED" in msg or "does not have" in msg
            return SourceRow(
                name=desc.name,
                status="permission_denied" if is_permission else "error",
                rows=None,
                last_updated=None,
                note=(
                    "App identity lacks USE SCHEMA/SELECT on "
                    f"{desc.uc_table}"
                    if is_permission
                    else f"{desc.note} (read error: {msg[:200]})"
                ),
            )

    def _describe_detail(self, fqtn: str) -> tuple[int | None, str | None]:
        """Cheap metadata read for a single table.

        ``DESCRIBE DETAIL`` returns one row with columns including
        ``numFiles``, ``sizeInBytes``, ``lastModified``, and -- when the
        Delta writer populated it -- ``numRecords``. We prefer
        ``numRecords`` from the detail row because it avoids a second
        warehouse round-trip entirely; many Delta writers (Lakeflow,
        MERGE, optimized writes) publish an accurate count there.
        ``SELECT COUNT(*)`` is used only as a fallback when
        ``numRecords`` is absent or NULL -- which still forces a table
        scan if Delta stats are stale, but that's the corner case, not
        the common path. Results are cached for ``self._ttl`` seconds.
        """
        # DESCRIBE DETAIL is a table function; the canonical form works
        # with the fully-qualified name directly.
        detail_rows = self._sql.execute(f"DESCRIBE DETAIL {fqtn}")
        last_mod: str | None = None
        row_count: int | None = None
        if detail_rows:
            last_mod = _opt_str(detail_rows[0].get("lastModified"))
            row_count = _opt_int(detail_rows[0].get("numRecords"))
        if row_count is None:
            # Fallback: Delta writer didn't publish numRecords. COUNT(*)
            # is still pushed down to Parquet row-group summaries on
            # unpartitioned tables, so cost stays bounded.
            count_rows = self._sql.execute(f"SELECT COUNT(*) AS row_count FROM {fqtn}")
            row_count = _opt_int(count_rows[0].get("row_count")) if count_rows else None
        return row_count, last_mod


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
