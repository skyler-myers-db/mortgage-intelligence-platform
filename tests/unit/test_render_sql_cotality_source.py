"""tools/render_sql.py's closed-shape rewrite of the Cotality source share.

The canonical source renders byte-identically (an artifact hash over every
rendered file). A non-canonical source rewrites exactly the closed shapes and
leaves zero canonical tokens, and anything outside those shapes refuses the
render before a file is written, rather than shipping SQL that still reads the
canonical share.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from backend.config.source_names import CANONICAL, CotalitySource
from tools import render_sql
from tools.databricks import ensure_asset_lineage_view

REPO = Path(__file__).resolve().parents[2]
SQL = REPO / "sql"
ACME = CotalitySource(catalog="acme_src", schema="feed", table_prefix="acme_")
CANONICAL_NAMES = (CANONICAL.catalog, CANONICAL.schema, CANONICAL.table_prefix)


def _tree_digest(root: Path) -> str:
    files = {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*.sql"))}
    manifest = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()


def _sources() -> list[Path]:
    return [path for path in sorted(SQL.rglob("*.sql")) if "_rendered" not in path.parts]


# --- the canonical source: identity --------------------------------------------


@pytest.mark.parametrize("demo", [False, True])
def test_the_canonical_render_is_byte_identical_with_and_without_the_rewrite(
    tmp_path: Path, demo: bool
) -> None:
    """Artifact hash: the default render equals one forced through the rewrite."""

    default = tmp_path / "default"
    explicit = tmp_path / "explicit"
    assert render_sql.render(
        catalog="mip", demo_first_party_enabled=demo, source_root=SQL, dest_root=default
    ) == render_sql.render(
        catalog="mip",
        demo_first_party_enabled=demo,
        source_root=SQL,
        dest_root=explicit,
        cotality=CANONICAL,
    )
    forced = tmp_path / "forced"
    for source in _sources():
        rendered = render_sql._render_text(
            source.read_text(encoding="utf-8"), "mip", demo_first_party_enabled=demo
        )[0]
        rewritten, _ = render_sql._rewrite_cotality_source(rendered, CANONICAL)
        target = forced / source.relative_to(SQL)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rewritten, encoding="utf-8")

    assert _tree_digest(default) == _tree_digest(explicit) == _tree_digest(forced)


def test_the_canonical_rewrite_is_an_identity_on_every_source() -> None:
    touched = 0
    for source in _sources():
        text = source.read_text(encoding="utf-8")
        rewritten, count = render_sql._rewrite_cotality_source(text, CANONICAL)
        assert rewritten == text, source
        touched += bool(count)

    assert touched >= 15  # the closed shapes really occur in the sources
    assert render_sql.render_cotality_source("x", CANONICAL, where="t") == ("x", 0)


# --- a non-canonical source: complete rewrite ------------------------------------


def test_a_fictional_source_renders_with_zero_canonical_tokens(tmp_path: Path) -> None:
    dest = tmp_path / "acme"
    processed, written, _ = render_sql.render(
        catalog="mip", demo_first_party_enabled=False, source_root=SQL, dest_root=dest, cotality=ACME
    )
    rendered = {path: path.read_text(encoding="utf-8") for path in sorted(dest.rglob("*.sql"))}

    assert processed == written == len(rendered) == len(_sources())
    for text in rendered.values():
        for name in CANONICAL_NAMES:
            assert name not in text
            assert name.upper() not in text
    # The only case-insensitive match left is the vendor's former brand in prose.
    loose = [
        line
        for text in rendered.values()
        for line in text.splitlines()
        if re.search(r"(?i)cotality_mortgage_data|corelogic|entrada_eval_", line)
    ]
    assert loose == ["--            owner_occupancy_code    'O'/'A'/'T' per CoreLogic dictionary."]


def test_every_canonical_table_reference_becomes_the_configured_one(tmp_path: Path) -> None:
    dest = tmp_path / "acme"
    render_sql.render(
        catalog="mip", demo_first_party_enabled=False, source_root=SQL, dest_root=dest, cotality=ACME
    )
    for source in _sources():
        text = source.read_text(encoding="utf-8")
        output = (dest / source.relative_to(SQL)).read_text(encoding="utf-8")
        assert output.count("acme_src.feed.acme_") == text.count(
            "cotality_mortgage_data.corelogic.entrada_eval_"
        ), source
        if "FROM cotality_mortgage_data.corelogic.entrada_eval_" in text:
            assert "FROM acme_src.feed.acme_" in output, source


def test_each_closed_shape_is_rewritten_and_only_its_own_names_change() -> None:
    source = (
        "SELECT * FROM cotality_mortgage_data.corelogic.entrada_eval_mls_listing_v1 AS l\n"
        "-- see `cotality_mortgage_data.corelogic.entrada_eval_voluntary_\n"
        "-- found in cotality_mortgage_data.corelogic. Not live.\n"
        "-- every table in cotality_mortgage_data.corelogic.* is read-only\n"
        "WHERE source_table_catalog = 'cotality_mortgage_data'\n"
        "COMMENT 'from entrada_eval_property_domain_v3 (S1.1)'\n"
    )

    rendered, count = render_sql.render_cotality_source(source, ACME, where="t")

    assert count == 6
    assert rendered == (
        "SELECT * FROM acme_src.feed.acme_mls_listing_v1 AS l\n"
        "-- see `acme_src.feed.acme_voluntary_\n"
        "-- found in acme_src.feed. Not live.\n"
        "-- every table in acme_src.feed.* is read-only\n"
        "WHERE source_table_catalog = 'acme_src'\n"
        "COMMENT 'from acme_property_domain_v3 (S1.1)'\n"
    )
    only_catalog = CotalitySource("acme_src", CANONICAL.schema, CANONICAL.table_prefix)
    assert render_sql.render_cotality_source(source, only_catalog, where="t")[0].splitlines()[0] == (
        "SELECT * FROM acme_src.corelogic.entrada_eval_mls_listing_v1 AS l"
    )
    no_prefix = CotalitySource("acme_src", "feed", "")
    assert "FROM acme_src.feed.mls_listing_v1 AS l" in render_sql.render_cotality_source(
        source, no_prefix, where="t"
    )[0]


@pytest.mark.parametrize(
    "lookalike",
    [
        "SELECT x_cotality_mortgage_data.y",
        "SELECT cotality_mortgage_data_v2.x, cotality_mortgage_data_v2.corelogic_v2.x",
        "SELECT corelogic_feed.x",
        "SELECT entrada_evaluation, my_entrada_eval_flag",
        "-- per CoreLogic dictionary",
    ],
)
def test_a_lookalike_identifier_is_neither_rewritten_nor_refused(lookalike: str) -> None:
    assert render_sql.render_cotality_source(lookalike, ACME, where="t") == (lookalike, 0)


@pytest.mark.parametrize(
    "outside",
    [
        "SELECT * FROM COTALITY_MORTGAGE_DATA.CORELOGIC.ENTRADA_EVAL_MLS_LISTING_V1",
        "SELECT * FROM `cotality_mortgage_data`.`corelogic`.`entrada_eval_mls_listing_v1`",
        "SELECT * FROM mip.cotality_mortgage_data.x",
        "SELECT * FROM mip.raw.entrada_eval_mls_listing_v1",
        "SELECT * FROM cotality_mortgage_data_v2.corelogic.x",
        "USE SCHEMA corelogic",
        "WHERE catalog_name IN ('cotality_mortgage_data')",
        "WHERE source_table_catalog <> 'cotality_mortgage_data'",
        "-- per `CoreLogic`.dictionary",
    ],
)
def test_a_reference_outside_the_closed_shapes_refuses_the_render(outside: str) -> None:
    with pytest.raises(render_sql.CanonicalSourceLeft, match=r"^t: canonical Cotality source token left"):
        render_sql.render_cotality_source(outside, ACME, where="t")


def test_a_configured_name_holding_a_canonical_token_is_refused_up_front() -> None:
    for cotality in (
        CotalitySource("acme_src", "feed", "entrada_eval_v2_"),
        CotalitySource("corelogic", "feed", "acme_"),
        CotalitySource("acme_src", "cotality_mortgage_data", "acme_"),
    ):
        with pytest.raises(render_sql.CanonicalSourceLeft, match="contains the canonical"):
            render_sql.render_cotality_source("SELECT 1", cotality, where="t")
    # An unchanged name may appear anywhere: only a changed one is proven.
    same_schema = CotalitySource("corelogic_feed", CANONICAL.schema, CANONICAL.table_prefix)
    assert render_sql.render_cotality_source("SELECT 1", same_schema, where="t") == ("SELECT 1", 0)


def test_a_refused_render_writes_nothing(tmp_path: Path) -> None:
    source = tmp_path / "sql"
    (source / "a").mkdir(parents=True)
    (source / "a" / "good.sql").write_text(
        "SELECT * FROM cotality_mortgage_data.corelogic.entrada_eval_mls_listing_v1;\n", encoding="utf-8"
    )
    (source / "z_bad.sql").write_text("SELECT * FROM COTALITY_MORTGAGE_DATA.CORELOGIC.X;\n", encoding="utf-8")
    dest = tmp_path / "out"

    with pytest.raises(render_sql.CanonicalSourceLeft, match=r"^z_bad\.sql: .*catalog at line 1"):
        render_sql.render(
            catalog="mip", demo_first_party_enabled=False, source_root=source, dest_root=dest, cotality=ACME
        )
    assert not dest.exists()


def test_the_cotality_source_rewrite_runs_after_the_install_catalog_rules() -> None:
    """A source catalog named like a governed prefix is emitted verbatim."""

    source = CotalitySource("mip", "raw", "acme_")
    rendered = render_sql._render_text(
        "SELECT * FROM cotality_mortgage_data.corelogic.entrada_eval_mls_listing_v1",
        "summit_mortgage",
        demo_first_party_enabled=False,
        cotality=source,
    )[0]

    assert rendered == "SELECT * FROM mip.raw.acme_mls_listing_v1"


# --- the lineage view and the CLI ---------------------------------------------------


def test_the_lineage_view_filters_on_the_configured_source_catalog() -> None:
    statement = " ".join(ensure_asset_lineage_view.view_statement("summit_mortgage", cotality=ACME).split())

    assert statement.endswith(
        "WHERE target_table_catalog = 'summit_mortgage' AND (source_table_catalog = "
        "'summit_mortgage' OR source_table_catalog = 'acme_src')"
    )
    assert CANONICAL.catalog not in ensure_asset_lineage_view.view_statement("mip", cotality=ACME)
    assert ensure_asset_lineage_view.view_statement("mip") == ensure_asset_lineage_view.view_statement(
        "mip", cotality=CANONICAL
    )


def test_the_cli_renders_the_environment_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MIP_COTALITY_SOURCE_CATALOG", "acme_src")
    monkeypatch.setenv("MIP_COTALITY_SOURCE_SCHEMA", "feed")
    monkeypatch.setenv("MIP_COTALITY_SOURCE_TABLE_PREFIX", "acme_")
    dest = tmp_path / "out"

    assert render_sql.main(["--catalog", "mip", "--dest", str(dest)]) == 0
    listing = (dest / "transformations" / "silver_listing_activity.sql").read_text(encoding="utf-8")
    assert "FROM acme_src.feed.acme_mls_listing_v1" in listing


@pytest.mark.parametrize("value", ["Acme", "acme-src", "<x>y"])
def test_the_cli_refuses_an_invalid_environment_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str
) -> None:
    monkeypatch.setenv("MIP_COTALITY_SOURCE_CATALOG", value)

    assert render_sql.main(["--catalog", "mip", "--dest", str(tmp_path / "out")]) == 2
    assert not (tmp_path / "out").exists()
