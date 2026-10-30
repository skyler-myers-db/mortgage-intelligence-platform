"""The audit source-asset policy refuses the configured Cotality share too.

``validate_source_assets`` admits ``<catalog>.{gold,semantics,ref,mip_app}.<object>``
and then refuses the canonical share by its ``cotality_mortgage_data.``
prefix, as defence in depth: the App reads only its own gold, ref and audit
assets, so a share table is never an audit source asset. A configured share
mounted in a schema the pattern admits would pass that canonical prefix, so
the policy refuses the configured share's qualified names as well
(``backend/schemas/_validators_source_names.py``).

Each case runs in a fresh process with exactly its env, as the App reads it.
The pairs are ON/OFF: a configured share's table is accepted on the canonical
install, so the configured name, and nothing else, owns its refusal.
"""

from __future__ import annotations

import pytest

from backend.config import source_names
from backend.config.source_names import CANONICAL, TABLE_STEMS, CotalitySource
from tests.fixtures.cotality_source_probe import run_fresh

FORBIDDEN = "contains a forbidden source asset"
SOURCES = {
    "gold_no_prefix": CotalitySource("acme_share", "gold", ""),
    "semantics_prefixed": CotalitySource("acme_share", "semantics", "acme_"),
    "ref_no_prefix": CotalitySource("zyrplax_feed", "ref", ""),
}
APP_ASSETS = (
    "mip.gold.borrower_360",
    "mip.semantics.portfolio_headline_metric_view",
    "mip.ref.offer_rules_config",
    "mip_app.approvals",
)
CANONICAL_SHARE = tuple(f"cotality_mortgage_data.gold.{CANONICAL.table_name(stem)}" for stem in TABLE_STEMS)


def _env(source: CotalitySource) -> dict[str, str]:
    return {
        source_names.CATALOG_ENV: source.catalog,
        source_names.SCHEMA_ENV: source.schema,
        source_names.TABLE_PREFIX_ENV: source.table_prefix_setting,
    }


def _share_assets(source: CotalitySource) -> list[str]:
    return [source.table(stem) for stem in TABLE_STEMS] + [
        f"{source.catalog}.{source.schema}.any_other_object",
        f"{source.catalog.title()}.{source.schema}.{source.table_name(TABLE_STEMS[0])}",
    ]


_ASSETS = sorted(
    {
        *APP_ASSETS,
        *CANONICAL_SHARE,
        *(asset for source in SOURCES.values() for asset in _share_assets(source)),
    }
)


@pytest.fixture(scope="module")
def verdicts(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, str]]:
    cwd = tmp_path_factory.mktemp("cotality-audit-assets")
    envs = {"canonical": {}, **{case: _env(source) for case, source in SOURCES.items()}}
    return {case: run_fresh(env, "source_assets", cwd, _ASSETS) for case, env in envs.items()}


@pytest.mark.parametrize("case", SOURCES)
def test_the_configured_share_is_a_forbidden_source_asset(
    verdicts: dict[str, dict[str, str]], case: str
) -> None:
    for asset in _share_assets(SOURCES[case]):
        # OFF: the canonical install admits the name, as any governed-schema asset.
        assert verdicts["canonical"][asset] == "accepted", asset
        # ON: the configured share's qualified prefix refuses it.
        assert verdicts[case][asset] == FORBIDDEN, asset


@pytest.mark.parametrize("case", ["canonical", *SOURCES])
def test_app_assets_stay_accepted_and_the_canonical_share_refused(
    verdicts: dict[str, dict[str, str]], case: str
) -> None:
    assert {asset: verdicts[case][asset] for asset in APP_ASSETS} == dict.fromkeys(APP_ASSETS, "accepted")
    assert {asset: verdicts[case][asset] for asset in CANONICAL_SHARE} == dict.fromkeys(
        CANONICAL_SHARE, FORBIDDEN
    )


def test_another_installs_share_is_not_refused_here(verdicts: dict[str, dict[str, str]]) -> None:
    for asset in _share_assets(SOURCES["gold_no_prefix"]):
        assert verdicts["ref_no_prefix"][asset] == "accepted", asset
