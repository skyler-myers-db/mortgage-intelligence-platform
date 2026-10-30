"""The Cotality source names: canonical defaults, overrides and their one shape.

``backend/config/source_names.py`` owns the canonical names; the client
profile carries the same three fields, with the same shapes.
"""

from __future__ import annotations

import pytest

from backend.config import source_names
from backend.config.source_names import CANONICAL, CotalitySource
from tools.clients import shapes

ACME = CotalitySource(catalog="acme_src", schema="feed", table_prefix="acme_")


def test_the_canonical_source_is_the_entrada_evaluation_share() -> None:
    assert (CANONICAL.catalog, CANONICAL.schema, CANONICAL.table_prefix) == (
        "cotality_mortgage_data",
        "corelogic",
        "entrada_eval_",
    )
    assert CANONICAL.is_canonical and not ACME.is_canonical
    assert CANONICAL.table("property_domain_v3") == (
        "cotality_mortgage_data.corelogic.entrada_eval_property_domain_v3"
    )
    assert ACME.table("mls_listing_v1") == "acme_src.feed.acme_mls_listing_v1"
    assert CANONICAL.describe() == "cotality_mortgage_data.corelogic.entrada_eval_*"


def test_an_unknown_table_stem_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown Cotality source table stem"):
        CANONICAL.table("property_basic")


@pytest.mark.parametrize("unset", [None, "", "   ", "<cotality-source-catalog>", "00000000PLACEHOLDER"])
def test_unset_blank_and_template_values_mean_the_canonical_name(unset: str | None) -> None:
    assert source_names.resolve(catalog=unset, schema=unset, table_prefix=unset) == CANONICAL


def test_each_set_override_replaces_only_its_own_name() -> None:
    assert source_names.resolve(catalog="acme_src") == CotalitySource(
        "acme_src", CANONICAL.schema, CANONICAL.table_prefix
    )
    assert source_names.resolve(schema=" feed ") == CotalitySource(
        CANONICAL.catalog, "feed", CANONICAL.table_prefix
    )
    assert source_names.resolve(table_prefix="acme_") == CotalitySource(
        CANONICAL.catalog, CANONICAL.schema, "acme_"
    )


def test_from_env_reads_the_three_documented_names() -> None:
    environ = {
        "MIP_COTALITY_SOURCE_CATALOG": "acme_src",
        "MIP_COTALITY_SOURCE_SCHEMA": "feed",
        "MIP_COTALITY_SOURCE_TABLE_PREFIX": "acme_",
        "MIP_DEFAULT_CATALOG": "ignored",
    }
    assert source_names.ENV_NAMES == (
        "MIP_COTALITY_SOURCE_CATALOG",
        "MIP_COTALITY_SOURCE_SCHEMA",
        "MIP_COTALITY_SOURCE_TABLE_PREFIX",
    )
    assert source_names.from_env(environ) == ACME
    assert source_names.from_env({}) == CANONICAL


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("catalog", "Acme_Src"),
        ("catalog", "acme-src"),
        ("catalog", "1acme"),
        ("catalog", "acme_src`; DROP"),
        ("catalog", "acme.src"),
        ("catalog", "a" * 256),
        ("schema", "feed schema"),
        ("table_prefix", "Acme_"),
        ("table_prefix", "acme-"),
        ("table_prefix", "a" * 65),
    ],
)
def test_a_name_outside_the_identifier_shape_is_refused_without_echoing_it(
    field: str, value: str
) -> None:
    with pytest.raises(ValueError) as refusal:
        source_names.resolve(**{field: value})

    assert f"Cotality source {field} must be a lowercase unquoted identifier" in str(refusal.value)
    assert value not in str(refusal.value)


def test_the_names_share_the_client_profile_shapes() -> None:
    assert source_names.UC_IDENTIFIER.pattern == shapes.UC_IDENTIFIER.pattern
    assert source_names.TABLE_PREFIX.pattern == shapes.TABLE_PREFIX.pattern
