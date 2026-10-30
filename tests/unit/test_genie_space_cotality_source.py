"""The Genie space spec renders the Cotality source through the closed-shape rewrite.

The governed spec (genie/mortgage_lead_intelligence_space.yml) names no
Cotality source today, so a configured source must leave its serialized payload
unchanged. A string that does name one is rewritten after the install-catalog
rules, and one the rewrite cannot prove refuses the render.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.config import source_names
from backend.config.source_names import CANONICAL, CotalitySource
from tools.databricks import provision_genie_space as pgs

ACME_ENV = {
    "MIP_COTALITY_SOURCE_CATALOG": "acme_src",
    "MIP_COTALITY_SOURCE_SCHEMA": "feed",
    "MIP_COTALITY_SOURCE_TABLE_PREFIX": "acme_",
}
ACME = CotalitySource(catalog="acme_src", schema="feed", table_prefix="acme_")


def test_the_genie_space_spec_carries_no_source_name_and_renders_the_same(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Golden: the spec names no Cotality source, so a configured one changes nothing."""

    for name in (*source_names.ENV_NAMES, "MIP_DEFAULT_CATALOG"):
        monkeypatch.delenv(name, raising=False)
    canonical = pgs.SpaceSpec.load(pgs.SPACE_YAML).to_serialized_payload()
    for name, value in ACME_ENV.items():
        monkeypatch.setenv(name, value)
    configured = pgs.SpaceSpec.load(pgs.SPACE_YAML).to_serialized_payload()

    assert configured == canonical
    for token in (CANONICAL.catalog, CANONICAL.schema, CANONICAL.table_prefix):
        assert token not in canonical.lower()
    assert json.loads(canonical)["data_sources"]["tables"]


def test_the_genie_render_rewrites_a_source_reference_and_refuses_an_unprovable_one() -> None:
    spec = {
        "instructions": "Never query cotality_mortgage_data.corelogic.* from here.",
        "sql_snippets": ["SELECT 1 FROM mip.gold.borrower_360"],
    }

    rendered = pgs._render_space_templates(
        spec, tenant_name="Summit Mortgage", catalog_name="lender_uc", cotality=ACME
    )

    assert rendered == {
        "instructions": "Never query acme_src.feed.* from here.",
        "sql_snippets": ["SELECT 1 FROM lender_uc.gold.borrower_360"],
    }
    with pytest.raises(ValueError, match="Genie space spec: canonical Cotality source token left"):
        pgs._render_space_templates(
            {"x": "COTALITY_MORTGAGE_DATA.CORELOGIC"}, tenant_name="t", catalog_name="mip", cotality=ACME
        )


def test_the_genie_env_overlay_leaves_the_source_names_to_the_deploy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in source_names.ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env.local").write_text(
        "".join(f"{name}={value}\n" for name, value in ACME_ENV.items()), encoding="utf-8"
    )
    monkeypatch.setattr(pgs, "REPO_ROOT", tmp_path)

    pgs._load_env_local()

    assert not {name for name in source_names.ENV_NAMES if name in pgs.os.environ}
