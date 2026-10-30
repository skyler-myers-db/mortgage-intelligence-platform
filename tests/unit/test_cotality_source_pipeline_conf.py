"""The feature pipeline reads its Cotality source from its configuration, or fails.

``databricks.yml`` hands the pipeline ``mip.cotality_source_*`` from the bundle
variables. The pipeline has no default source: a run whose configuration does
not name one (for example between a bundle deploy's file upload and its
pipeline update) raises before any table is declared, so it never reads a
source it was not given. The module runs here under stand-in ``dlt`` and
``pyspark`` modules (tests/fixtures/lakeflow_pipeline_stub.py).
"""

from __future__ import annotations

import ast
import re

import pytest
import yaml

from backend.config import source_names
from backend.config.source_names import CANONICAL, CotalitySource
from tests.fixtures.client_profiles import demo_profile
from tests.fixtures.lakeflow_pipeline_stub import PIPELINE, REPO, exec_pipeline
from tools.clients import profile as views
from tools.clients.environment import RawProfile, resolve
from tools.clients.schema import ClientProfile

KEYS = (
    "mip.cotality_source_catalog",
    "mip.cotality_source_schema",
    "mip.cotality_source_table_prefix",
)
ACME = CotalitySource(catalog="acme_src", schema="feed", table_prefix="acme_")
SHARES = {
    "_SHARE_PROPERTY_V3": "property_domain_v3",
    "_SHARE_VOLUNTARY_LIEN": "voluntary_lien_status_marketing_v2",
    "_SHARE_MLS_LISTING": "mls_listing_v1",
    "_SHARE_HELOC_PROPENSITY": "heloc_propensity_score_v1",
    "_SHARE_REFI_PROPENSITY": "refi_propensity_score_v1",
    "_SHARE_MORTGAGE_DOMAIN": "mortgage_domain_v1",
    "_SHARE_OWNER_TRANSFER": "owner_transfer_domain_v1",
}


def _conf(source: CotalitySource) -> dict[str, str]:
    return dict(zip(KEYS, (source.catalog, source.schema, source.table_prefix), strict=True))


def _pipeline_resource() -> dict:
    bundle = yaml.safe_load((REPO / "databricks.yml").read_text(encoding="utf-8"))
    return bundle["resources"]["pipelines"]["mip_feature_pipeline"]


# --- no configuration, no source ----------------------------------------------------


def test_a_run_without_the_source_configuration_raises_before_declaring_a_table() -> None:
    with pytest.raises(RuntimeError) as refusal:
        exec_pipeline({})

    message = str(refusal.value)
    assert all(f"{key} unset" in message for key in KEYS)
    assert "there is no default source" in message
    assert CANONICAL.catalog not in message


@pytest.mark.parametrize("missing", KEYS)
def test_each_missing_key_raises_by_name(missing: str) -> None:
    conf = {key: value for key, value in _conf(ACME).items() if key != missing}

    with pytest.raises(RuntimeError, match=re.escape(f"{missing} unset")):
        exec_pipeline(conf)


@pytest.mark.parametrize(
    ("key", "value"),
    [(KEYS[0], "Acme_Src"), (KEYS[0], ""), (KEYS[1], "feed-x"), (KEYS[2], "acme.")],
)
def test_an_invalid_value_raises_without_reading_a_table(key: str, value: str) -> None:
    conf = {**_conf(ACME), key: value}

    with pytest.raises(RuntimeError, match=re.escape(f"{key} invalid")):
        exec_pipeline(conf)


# --- a configured source ---------------------------------------------------------


@pytest.mark.parametrize("source", [CANONICAL, ACME, CotalitySource("cotality_prod", "mortgage", "")])
def test_every_share_table_is_the_configured_source(source: CotalitySource) -> None:
    run = exec_pipeline(_conf(source))

    assert {name: run.namespace[name] for name in SHARES} == {
        name: source.table(stem) for name, stem in SHARES.items()
    }
    assert set(SHARES.values()) == set(source_names.TABLE_STEMS)
    assert run.conf_reads == list(KEYS)


def test_the_canonical_configuration_keeps_todays_table_comments() -> None:
    run = exec_pipeline(_conf(CANONICAL))

    assert len(run.tables) == 9
    assert run.table("property_master")["comment"].startswith(
        "CLIP-grain property snapshot from entrada_eval_property_domain_v3, retaining"
    )
    assert run.table("lien_current")["comment"].startswith(
        "CLIP-grain lien + rates snapshot from entrada_eval_voluntary_lien_status_marketing_v2 "
        "retaining every valid source state. THE SPINE of"
    )
    assert run.table("mortgage_events")["comment"].startswith(
        "Event-grain mortgage history from entrada_eval_mortgage_domain_v1 retaining"
    )
    assert run.table("owner_transfer_events")["comment"].startswith(
        "Event-grain owner-transfer history from entrada_eval_owner_transfer_domain_v1 retaining"
    )
    assert run.table("property_owners")["comment"].startswith(
        "Multi-owner explode of entrada_eval_property_domain_v3 (S1.1): one row"
    )


def test_a_fictional_source_leaves_no_canonical_token_in_any_table_declaration() -> None:
    run = exec_pipeline(_conf(ACME))
    declared = repr(run.tables) + repr({name: run.namespace[name] for name in SHARES})

    for name in (CANONICAL.catalog, CANONICAL.schema, CANONICAL.table_prefix):
        assert name not in declared.lower()
    assert "from acme_property_domain_v3" in run.table("property_master")["comment"]


def test_the_pipeline_source_names_no_canonical_source_outside_its_docstring() -> None:
    tree = ast.parse(PIPELINE.read_text(encoding="utf-8"))
    docstring = tree.body[0]
    assert isinstance(docstring, ast.Expr)
    constants = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node is not docstring.value
    ]

    for name in (CANONICAL.catalog, CANONICAL.schema, CANONICAL.table_prefix):
        assert not [text for text in constants if name in text.lower()], name


# --- the bundle hands the pipeline its source ------------------------------------


def test_the_bundle_configures_exactly_the_keys_the_pipeline_reads() -> None:
    run = exec_pipeline(_conf(CANONICAL))

    assert run.namespace["_SOURCE_CONF_KEYS"] == KEYS
    assert _pipeline_resource()["configuration"] == {
        "mip.cotality_source_catalog": "${var.cotality_source_catalog}",
        "mip.cotality_source_schema": "${var.cotality_source_schema}",
        "mip.cotality_source_table_prefix": "${var.cotality_source_table_prefix}",
    }


def test_the_pipeline_shapes_are_the_source_names_shapes() -> None:
    run = exec_pipeline(_conf(CANONICAL))

    assert run.namespace["_SOURCE_NAME_SHAPES"] == (
        source_names.UC_IDENTIFIER.pattern,
        source_names.UC_IDENTIFIER.pattern,
        source_names.TABLE_PREFIX.pattern,
    )


def test_the_bundle_variable_defaults_are_the_canonical_names() -> None:
    variables = yaml.safe_load((REPO / "databricks.yml").read_text(encoding="utf-8"))["variables"]

    assert {
        name: variables[name]["default"]
        for name in ("cotality_source_catalog", "cotality_source_schema", "cotality_source_table_prefix")
    } == {
        "cotality_source_catalog": CANONICAL.catalog,
        "cotality_source_schema": CANONICAL.schema,
        "cotality_source_table_prefix": CANONICAL.table_prefix,
    }


def _profile(values: dict[str, str]) -> ClientProfile:
    return resolve(RawProfile(values=values))


def test_the_profile_bundle_view_carries_set_source_names() -> None:
    values = demo_profile()
    profile = _profile(values)

    variables = views.bundle_vars(profile)

    assert variables["cotality_source_catalog"] == values["MIP_COTALITY_SOURCE_CATALOG"]
    assert variables["cotality_source_schema"] == values["MIP_COTALITY_SOURCE_SCHEMA"]
    assert variables["cotality_source_table_prefix"] == values["MIP_COTALITY_SOURCE_TABLE_PREFIX"]


def test_an_unset_profile_source_name_keeps_the_canonical_variable_default() -> None:
    values = demo_profile(
        MIP_COTALITY_SOURCE_CATALOG=None,
        MIP_COTALITY_SOURCE_SCHEMA=None,
        MIP_COTALITY_SOURCE_TABLE_PREFIX=None,
    )

    variables = views.bundle_vars(_profile(values))

    assert not {name for name in variables if name.startswith("cotality_source_")}
