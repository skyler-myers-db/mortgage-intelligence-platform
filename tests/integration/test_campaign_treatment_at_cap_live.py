"""Live performance proof for the 10,000-member campaign-treatment cap.

The proof exercises the production ``LeadCohortQueries`` materialization path
without creating a Lakebase campaign or writing the production append-only
audit table. It selects current eligible, app-masked borrower ids from governed
gold, redirects only the exact production treatment-table FQN to a unique
managed scratch table in the governed audit schema, and always drops that
table. Local and pull-request runs skip unless warehouse credentials and the
explicit mutation opt-in are present.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from itertools import product
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

import pytest

from backend.config.settings import settings
from backend.services.databricks_sql import DatabricksSqlClient
from backend.services.databricks_sql_helpers import qualify
from backend.services.eligibility import eligible_sql_predicate
from backend.services.repositories.databricks_lead_cohorts import (
    LeadCohortFilters,
    LeadCohortQueries,
)

AT_CAP_MEMBER_COUNT = 10_000
_LOAD_TEST_BASELINE = Path(__file__).resolve().parents[2] / "tools/load_test/baseline.json"
_LOAD_TEST_BUDGET_MS = json.loads(_LOAD_TEST_BASELINE.read_text(encoding="utf-8"))["endpoints"][
    "POST /api/v1/portfolio/create"
]["p95_budget_ms"]
# The governed borrower-selection query runs before the timer and warms the
# warehouse. The at-cap production materializer must therefore fit the same
# canonical p95 product budget as the portfolio-create endpoint it serves.
AT_CAP_MATERIALIZATION_CEILING_SECONDS = float(_LOAD_TEST_BUDGET_MS) / 1000.0
_MASKED_BORROWER_ID_RE = re.compile(r"B-[0-9A-Z]{13}")
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class _SqlClient(Protocol):
    def execute(
        self,
        statement: str,
        parameters: dict[str, Any] | list[Any] | tuple[Any, ...] | None = None,
    ) -> list[dict[str, Any]]: ...

    def execute_one(
        self,
        statement: str,
        parameters: dict[str, Any] | list[Any] | tuple[Any, ...] | None = None,
    ) -> dict[str, Any] | None: ...


class _ScratchTreatmentSqlClient:
    """Delegate SQL after replacing only the exact treatment-table FQN."""

    def __init__(
        self,
        delegate: _SqlClient,
        *,
        production_table: str,
        scratch_table: str,
    ) -> None:
        self._delegate = delegate
        self._production_table = production_table
        self._scratch_table = scratch_table
        production_parts = production_table.split(".")
        self._production_table_forms = tuple(
            ".".join(
                f"`{part}`" if quoted else part
                for part, quoted in zip(production_parts, quote_parts, strict=True)
            )
            for quote_parts in product((False, True), repeat=len(production_parts))
        )
        self.rewrite_count = 0
        self.rewrite_shapes: list[str] = []

    def _rewrite(self, statement: str) -> str:
        rewritten = statement
        occurrences = 0
        production_reference = any(
            re.search(
                rf"(?<![A-Za-z0-9_.`]){re.escape(production_form)}"
                r"(?![A-Za-z0-9_`])",
                statement,
            )
            for production_form in self._production_table_forms
        )
        leading_keyword_match = re.match(r"\s*([A-Za-z]+)", statement)
        leading_keyword = (
            leading_keyword_match.group(1).upper() if leading_keyword_match else ""
        )
        # The production materializer emits only SELECT/WITH reads, its WITH
        # ... MERGE append, and DESCRIBE HISTORY. Refuse any other top-level
        # statement that names the production table before considering a FROM
        # rewrite, so DELETE/UPDATE/DDL cannot be redirected into the scratch
        # proof under an accidentally over-broad relation match.
        if production_reference and leading_keyword not in {
            "SELECT",
            "WITH",
            "MERGE",
            "DESCRIBE",
        }:
            raise AssertionError(
                "unrecognized production campaign-treatment FQN context; refusing SQL"
            )
        # Restrict rewrites to the relation positions emitted by the production
        # materializer. A matching string literal or longer relation name is
        # deliberately left untouched.
        for relation_prefix in ("MERGE INTO ", "DESCRIBE HISTORY ", "FROM "):
            for production_form in self._production_table_forms:
                production_target = re.escape(relation_prefix + production_form)
                scratch_target = relation_prefix + self._scratch_table
                rewritten, prefix_occurrences = re.subn(
                    production_target + r"(?![A-Za-z0-9_`])",
                    scratch_target,
                    rewritten,
                )
                occurrences += prefix_occurrences
                self.rewrite_shapes.extend([relation_prefix.strip()] * prefix_occurrences)
        for production_form in self._production_table_forms:
            exact_production_fqn = re.compile(
                rf"(?<![A-Za-z0-9_.`]){re.escape(production_form)}(?![A-Za-z0-9_`])"
            )
            if exact_production_fqn.search(rewritten):
                raise AssertionError(
                    "unrecognized production campaign-treatment FQN context; refusing SQL"
                )
        self.rewrite_count += occurrences
        return rewritten

    def execute(
        self,
        statement: str,
        parameters: dict[str, Any] | list[Any] | tuple[Any, ...] | None = None,
    ) -> list[dict[str, Any]]:
        return self._delegate.execute(self._rewrite(statement), parameters)

    def execute_one(
        self,
        statement: str,
        parameters: dict[str, Any] | list[Any] | tuple[Any, ...] | None = None,
    ) -> dict[str, Any] | None:
        return self._delegate.execute_one(self._rewrite(statement), parameters)


def _live_warehouse_config() -> tuple[str, str, str] | None:
    if os.environ.get("MIP_LIVE_MUTATION_OK") != "1":
        return None
    host = os.environ.get("DATABRICKS_HOST") or os.environ.get("DATABRICKS_SERVER_HOSTNAME")
    token = os.environ.get("DATABRICKS_TOKEN")
    warehouse_id = os.environ.get("DATABRICKS_WAREHOUSE_ID")
    if not host or not token or not warehouse_id:
        return None
    if not host.startswith("http"):
        host = "https://" + host
    return host.rstrip("/"), token, warehouse_id


def _safe_identifier(value: str, *, field: str) -> str:
    normalized = value.strip()
    if not _IDENTIFIER_RE.fullmatch(normalized):
        raise ValueError(f"unsafe {field} identifier: {value!r}")
    return normalized


def _scratch_table_name(*, catalog: str, schema: str, suffix: str) -> str:
    return ".".join(
        _safe_identifier(value, field=field)
        for value, field in (
            (catalog, "catalog"),
            (schema, "schema"),
            (f"campaign_treatment_cap_smoke_{suffix}", "table"),
        )
    )


def _scratch_suffix() -> str:
    return _safe_identifier(
        os.environ.get("MIP_LIVE_SCRATCH_SUFFIX", ""),
        field="scratch suffix",
    )


@contextmanager
def _scratch_treatment_table(
    client: _SqlClient,
    *,
    production_table: str,
    scratch_table: str,
) -> Iterator[None]:
    """Create an exact-schema clone and attempt cleanup on every path."""

    try:
        client.execute(f"CREATE TABLE {scratch_table} LIKE {production_table}")
        yield
    finally:
        # A missing table is harmless; a permission or cleanup failure is not.
        client.execute(f"DROP TABLE IF EXISTS {scratch_table}")


def _select_at_cap_masked_borrower_ids(client: _SqlClient) -> list[str]:
    borrower_table = qualify("gold", "borrower_360")
    rows = client.execute(
        f"""
SELECT b.borrower_id
FROM {borrower_table} AS b
WHERE {eligible_sql_predicate("b")}
  AND COALESCE(b.has_unresolved_owner, FALSE) = FALSE
  AND (
    b.last_touch_at IS NULL
    OR b.last_touch_at < CURRENT_TIMESTAMP() - INTERVAL '30' DAYS
  )
ORDER BY b.opportunity_score DESC, b.borrower_id ASC
LIMIT {AT_CAP_MEMBER_COUNT}
"""
    )
    borrower_ids = [str(row.get("borrower_id") or "") for row in rows]
    if len(borrower_ids) != AT_CAP_MEMBER_COUNT:
        raise AssertionError(
            "governed gold does not contain exactly 10,000 selectable eligible borrowers "
            f"for the at-cap proof (returned {len(borrower_ids)})"
        )
    if len(set(borrower_ids)) != AT_CAP_MEMBER_COUNT:
        raise AssertionError("at-cap governed borrower selection contains duplicate ids")
    if any(_MASKED_BORROWER_ID_RE.fullmatch(value) is None for value in borrower_ids):
        raise AssertionError("at-cap governed borrower selection contains a non-masked id")
    return borrower_ids


@pytest.fixture
def live_warehouse_config() -> tuple[str, str, str]:
    config = _live_warehouse_config()
    if config is None:
        pytest.skip(
            "Set DATABRICKS_HOST, DATABRICKS_TOKEN, DATABRICKS_WAREHOUSE_ID, "
            "and MIP_LIVE_MUTATION_OK=1 to run the at-cap treatment proof."
        )
    _scratch_suffix()
    return config


@pytest.mark.integration
def test_campaign_treatment_materializes_exact_cap_with_bounded_latency(
    live_warehouse_config: tuple[str, str, str],
) -> None:
    host, token, warehouse_id = live_warehouse_config
    base_client = DatabricksSqlClient(host, token, warehouse_id, timeout_s=30)
    borrower_ids = _select_at_cap_masked_borrower_ids(base_client)

    catalog = _safe_identifier(settings.mip_default_catalog, field="catalog")
    # The proof is intentionally fixed to the governed audit schema. Allowing
    # an environment override would make a green run ambiguous about grants.
    schema = "audit"
    suffix = _scratch_suffix()
    production_table = qualify("audit", "campaign_treatment_snapshot", catalog=catalog)
    scratch_table = _scratch_table_name(catalog=catalog, schema=schema, suffix=suffix)
    campaign_id = str(uuid4())
    materialization_id = str(uuid4())

    with _scratch_treatment_table(
        base_client,
        production_table=production_table,
        scratch_table=scratch_table,
    ):
        scratch_client = _ScratchTreatmentSqlClient(
            base_client,
            production_table=production_table,
            scratch_table=scratch_table,
        )
        queries = LeadCohortQueries(scratch_client, cache_ttl_s=0)  # type: ignore[arg-type]
        filters = LeadCohortFilters(segment=None, borrower_ids=borrower_ids)

        def materialize() -> dict[str, Any]:
            return queries.materialize_campaign_treatment(
                filters,
                campaign_id=campaign_id,
                materialization_id=materialization_id,
                request_payload_hash="a" * 64,
                contract_fingerprint="b" * 64,
                frequency_cap_days=30,
                holdout_basis_points=0,
                household_dedup_enabled=False,
            )

        first_started = time.monotonic()
        first_manifest = materialize()
        first_elapsed_seconds = time.monotonic() - first_started

        replay_started = time.monotonic()
        replay_manifest = materialize()
        replay_elapsed_seconds = time.monotonic() - replay_started

        counts = (
            scratch_client.execute_one(
                f"""
SELECT
  COUNT(*) AS total_rows,
  COUNT(CASE WHEN row_kind = 'manifest' THEN 1 END) AS manifest_rows,
  COUNT(CASE WHEN row_kind = 'member' THEN 1 END) AS member_rows,
  COUNT(DISTINCT CASE WHEN row_kind = 'member' THEN record_key END)
    AS distinct_member_rows
FROM {production_table}
WHERE campaign_id = :campaign_id
  AND materialization_id = :materialization_id
""",
                {
                    "campaign_id": campaign_id,
                    "materialization_id": materialization_id,
                },
            )
            or {}
        )

        print(
            "campaign_treatment_at_cap "
            f"first_materialization_seconds={first_elapsed_seconds:.3f} "
            f"idempotent_replay_seconds={replay_elapsed_seconds:.3f} "
            f"ceiling_seconds={AT_CAP_MATERIALIZATION_CEILING_SECONDS:.0f}"
        )
        assert first_elapsed_seconds <= AT_CAP_MATERIALIZATION_CEILING_SECONDS
        assert replay_elapsed_seconds <= AT_CAP_MATERIALIZATION_CEILING_SECONDS
        assert first_manifest["selected_primary_count"] == AT_CAP_MEMBER_COUNT
        assert replay_manifest["selected_primary_count"] == AT_CAP_MEMBER_COUNT
        assert replay_manifest["treatment_fingerprint"] == first_manifest["treatment_fingerprint"]
        assert replay_manifest["assignment_digest"] == first_manifest["assignment_digest"]
        assert int(counts.get("total_rows") or 0) == AT_CAP_MEMBER_COUNT + 1
        assert int(counts.get("manifest_rows") or 0) == 1
        assert int(counts.get("member_rows") or 0) == AT_CAP_MEMBER_COUNT
        assert int(counts.get("distinct_member_rows") or 0) == AT_CAP_MEMBER_COUNT
        # Each production materialization emits exactly MERGE, HISTORY, and
        # manifest-table relation references (3 x 2 calls), followed by this
        # test's one count read. Shape drift must be reviewed, not tolerated.
        assert scratch_client.rewrite_count == 7
        assert scratch_client.rewrite_shapes == [
            "MERGE INTO",
            "DESCRIBE HISTORY",
            "FROM",
            "MERGE INTO",
            "DESCRIBE HISTORY",
            "FROM",
            "FROM",
        ]
