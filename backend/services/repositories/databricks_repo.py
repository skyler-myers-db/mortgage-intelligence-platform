"""Compatibility facade for live Databricks-backed repository implementations.

The concrete repository classes are split by concern across sibling
modules. This facade preserves the historical import path used by
factory wiring and tests while the implementations keep shrinking into
focused files. Each class implements one Protocol from
``backend.services.repositories.protocols`` against the ``mip.gold.*``
tables materialised by the Slice-3 Lakeflow pipeline. The constructor
takes a ``DatabricksSqlClient`` so the same pool/keep-alive is reused
across every repository in the process.

PII posture (non-negotiable, enforced by ``backend/services/pii_redaction``):

- No raw owner names, mailing addresses, or street addresses leave
  these classes.
- The ``clip`` -> ``clip_id`` and ``delta_vs_prior`` -> ``delta`` renames
  documented in ``docs/data-contract-module0.md`` §12 happen here, at
  the repository boundary, not in the SQL view and not in the router.
- Every ``SELECT`` lists columns explicitly -- never ``SELECT *`` -- so
  a future gold schema expansion cannot silently surface new PII.
- Every WHERE clause uses named / positional parameters, never string
  interpolation. A caller-supplied borrower id is always bound.

Evidence ordering: ``ORDER BY signal_rank ASC`` per the data-contract
§3.4. Chronological order is a display concern handled in the UI; the
canonical order for the evidence drawer is the gold-defined priority.

Slice-7+: ``DatabricksGenieRepository`` serves ``/api/genie`` from the
real Mortgage Lead Intelligence Genie space. If the ``genie`` circuit
breaker is OPEN, the repository returns an honest "warming up" message
rather than fabricating or replaying analytic content.
"""

from __future__ import annotations

import logging

from backend.services.lakebase import get_lakebase_client  # noqa: F401 - compatibility seam
from backend.services.repositories.databricks_analytics import DatabricksAnalyticsRepository
from backend.services.repositories.databricks_borrowers import (
    DatabricksBorrowerRepository,
    DatabricksOutreachRepository,
)
from backend.services.repositories.databricks_genie import (  # noqa: F401
    _GENIE_IDENTIFIER_COLUMNS,
    _GENIE_PII_KEYS,
    _PII_TEXT_PATTERNS,
    _TRUSTED_GENIE_ASSETS,
    DatabricksGenieRepository,
    _adapt_genie_response,
    _answer_text_contains_pii,
    _borrower_ids_from_rows,
    _bounded_genie_sql,
    _build_genie_proof,
    _canonical_genie_answer,
    _dateish_columns,
    _execute_trusted_genie_sql,
    _extract_filters,
    _freshness_from_rows,
    _genie_question_hash,
    _genie_response_has_query_proof,
    _is_genie_identifier_column,
    _is_select_only,
    _known_data_gaps,
    _known_data_gaps_for_result,
    _label_column,
    _likely_data_question,
    _merge_trusted_assets,
    _needs_genie_sql_repair,
    _normalise_genie_key,
    _numeric_columns,
    _pending_feed_gaps_from_material,
    _pending_feed_gaps_from_rows,
    _plan_genie_visualization,
    _portfolio_criteria_from_sql,
    _redact_genie_rows,
    _route_from_answer_rows,
    _row_columns,
    _row_values,
    _segment_codes_from_question,
    _source_readiness_only_assets,
    _sql_hash,
    _sql_uses_impossible_retention_conjunction,
    _sql_uses_stale_evidence_signal_enum,
    _suggest_genie_actions,
    _text_columns,
    _total_matching_from_rows,
    _trusted_genie_asset_names,
    _trusted_sql_policy,
    _trusted_sql_policy_allowing_stale_evidence_enum,
    _trusted_sql_policy_core,
    _trusted_sql_repair_prompt,
    _value_column,
)
from backend.services.repositories.databricks_genie_canonical import (  # noqa: F401
    _AMBIGUOUS_STATE_CODES,  # noqa: F401 - compatibility re-export
    _CANONICAL_CURRENT_CUSTOMER_RETENTION_RISK_SQL,
    _CANONICAL_ITM_COUNT_BY_CITY_SQL,
    _CANONICAL_ITM_COUNT_BY_STATE_SQL,
    _CANONICAL_ITM_COUNT_SQL,
    _CANONICAL_ITM_TOP_ZIPS_SQL,
    _CANONICAL_MSA_SCORE_SQL,
    _CANONICAL_RETENTION_COMPETITOR_LIEN_LIST_SQL,
    _CANONICAL_STRATEGY_BOARD_SQL,
    _US_STATE_FILTERS,  # noqa: F401 - compatibility re-export
    _ambiguous_state_code_match_is_contextual,  # noqa: F401 - compatibility re-export
    _canonical_in_the_money_count_scope,  # noqa: F401 - compatibility re-export
    _canonical_itm_city_scope,  # noqa: F401 - compatibility re-export
    _canonical_itm_state_scope,  # noqa: F401 - compatibility re-export
    _canonical_itm_zip_scope,  # noqa: F401 - compatibility re-export
    _canonical_msa_score_scope,  # noqa: F401 - compatibility re-export
    _canonical_strategy_board_scope,  # noqa: F401 - compatibility re-export
    _current_footprint_label,
    _retention_competitor_lien_list_question,  # noqa: F401 - compatibility re-export
    _retention_risk_question,  # noqa: F401 - compatibility re-export
)
from backend.services.repositories.databricks_genie_policy import (  # noqa: F401
    _GENIE_PII_SQL_COLUMNS,
    _LONG_RAW_IDENTIFIER_LITERAL_RE,
    _RAW_IDENTIFIER_SQL_LITERAL_RE,
    _SENSITIVE_SQL_IDENTIFIER_RE,
    _SQL_IDENT_RE,
    _cte_names,
    _extract_asset_refs,
    _iter_from_clause_segments,
    _normalise_sql_ref,
    _projection_has_wildcard_expansion,
    _relation_refs,
    _scrub_sql_for_policy,
    _split_top_level_commas,
    _sql_has_raw_identifier_literal,
    _sql_has_unqualified_relations,
    _sql_has_wildcard_expansion,
    _sql_mentions_pii_columns,
)
from backend.services.repositories.databricks_geo import (
    DatabricksGeoRepository,
    DatabricksSegmentRepository,
)
from backend.services.repositories.databricks_leads import DatabricksLeadRepository
from backend.services.repositories.databricks_offers import DatabricksOfferRepository
from backend.services.repositories.databricks_portfolio import DatabricksPortfolioRepository
from backend.services.repositories.databricks_shared import (
    _BORROWER_360_COLUMNS,  # noqa: F401 - compatibility re-export for schema-guard tests
    _BORROWER_DOSSIER_COLUMNS,  # noqa: F401 - compatibility re-export
    _EVIDENCE_COLUMNS,  # noqa: F401 - compatibility re-export
    _LEAD_POPULATION_COLUMNS,  # noqa: F401 - compatibility re-export for schema-guard tests
    _LEAD_POPULATION_SELECT_FROM_B360,  # noqa: F401 - compatibility re-export
    _LEAD_POPULATION_SELECT_FROM_LP,  # noqa: F401 - compatibility re-export
    _SEGMENT_COLUMNS,  # noqa: F401 - compatibility re-export
    _coerce_bool,  # noqa: F401 - compatibility re-export
    _parse_timeline,  # noqa: F401 - compatibility re-export
    _redact_evidence_list,  # noqa: F401 - compatibility re-export
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------

__all__ = [
    "DatabricksAnalyticsRepository",
    "DatabricksBorrowerRepository",
    "DatabricksGenieRepository",
    "DatabricksGeoRepository",
    "DatabricksLeadRepository",
    "DatabricksOfferRepository",
    "DatabricksOutreachRepository",
    "DatabricksPortfolioRepository",
    "DatabricksSegmentRepository",
]
