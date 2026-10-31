from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.deploy_block_harness import block, drive
from tests.fixtures.deploy_script import deploy_entrypoint_text

REPO = Path(__file__).resolve().parents[2]
# deploy.sh's demo-feed switch, rendered before the lease since M0-PR4
# (scripts/lib/deploy_step_automation_identities.sh) and the client profile's
# own MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS since M1-PR3 (tools/clients/policy.py).
FEEDS_START = 'if [[ ! "${CLIENT_DEMO_FEEDS:-}" =~ ^[01]$ ]]; then'
FEEDS_END = 'export MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS="$CLIENT_DEMO_FEEDS"'


def test_render_sql_demo_feed_switch_defaults_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare render must not seed synthetic first-party rows by accident."""
    from tools import render_sql

    monkeypatch.delenv("MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS", raising=False)

    assert render_sql._resolve_demo_first_party_enabled(None) is False
    assert render_sql._resolve_demo_first_party_enabled("true") is True


def test_bundle_env_takes_the_demo_feeds_from_the_profile_policy_alone() -> None:
    """M1-PR3: no class default and no override flag; the ci target renders them off."""
    from tools.databricks import bundle_env

    assert bundle_env._target_from_args(["-t", "client"]) == "client"
    assert bundle_env._target_from_args(["--target=customer"]) == "customer"
    assert bundle_env._target_from_args(["--select=jobs.x"]) == "", "no target is the default"
    for retired in ("_demo_first_party_flag", "_demo_feeds_allowed"):
        assert not hasattr(bundle_env, retired), retired
    source = (REPO / "tools/databricks/bundle_env.py").read_text(encoding="utf-8")
    assert "MIP_ALLOW_DEMO_FIRST_PARTY_IN_PROD" not in source
    assert "demo_first_party_enabled = policy is not None and policy.demo_first_party_feeds" in source


def test_local_rendered_demo_feed_artifact_is_fail_closed_when_present() -> None:
    """A checked-out workstation must not be left with a stale enabled seed."""
    rendered = REPO / "sql/_rendered/transformations/demo_first_party_feeds.sql"
    if not rendered.exists():
        pytest.skip("rendered SQL tree has not been materialized")

    sql = rendered.read_text(encoding="utf-8")

    assert "SELECT FALSE AS enabled" in sql
    assert "SELECT TRUE AS enabled" not in sql


def test_demo_first_party_seed_is_explicitly_render_gated() -> None:
    sql = (REPO / "sql/transformations/demo_first_party_feeds.sql").read_text(
        encoding="utf-8"
    )
    bundle = (REPO / "databricks.yml").read_text(encoding="utf-8")

    assert "{{mip_enable_demo_first_party_feeds}}" in sql
    assert ":enable_demo_first_party_feeds" not in sql
    assert "feed_mode" in sql
    assert "'demo_synthetic' AS feed_mode" in sql
    assert "TRUE AS synthetic_demo" in sql
    assert "seed_demo_first_party_feeds" in bundle
    seed_task = bundle[
        bundle.index("seed_demo_first_party_feeds") : bundle.index(
            "ctas_property_owner_bridge"
        )
    ]
    assert "parameters:" not in seed_task


def test_first_party_contracts_carry_demo_disclosure_columns() -> None:
    for relative in ("sql/ddl/001_catalogs_schemas.sql", "sql/ddl/003_gold_tables.sql"):
        ddl = (REPO / relative).read_text(encoding="utf-8")
        for table in (
            "loan_applications",
            "servicing_portfolio",
            "crm_campaign_membership",
            "customer_interactions",
            "product_balances",
        ):
            assert f"mip.first_party.{table}" in ddl
        assert "source_system" in ddl
        assert "feed_mode" in ddl
        assert "synthetic_demo" in ddl


def test_gold_consumes_first_party_relationship_signals() -> None:
    borrower_sql = (REPO / "sql/transformations/gold_borrower_360.sql").read_text(
        encoding="utf-8"
    )
    score_sql = (REPO / "sql/transformations/gold_lead_scores.sql").read_text(
        encoding="utf-8"
    )
    readiness_sql = (REPO / "sql/transformations/gold_source_readiness.sql").read_text(
        encoding="utf-8"
    )

    for table in (
        "mip.first_party.servicing_portfolio",
        "mip.first_party.loan_applications",
        "mip.first_party.crm_campaign_membership",
        "mip.first_party.customer_interactions",
        "mip.first_party.product_balances",
    ):
        assert table in borrower_sql

    assert "has_first_party_relationship" in borrower_sql
    assert "first_party_relationship_depth" in borrower_sql
    assert "first_party_recent_interactions" in score_sql
    assert "first_party_recent_application" in score_sql
    assert "'demo_synthetic'" in readiness_sql
    assert "COUNT_IF(COALESCE(synthetic_demo, FALSE)) = COUNT(*) THEN 'demo_synthetic'" in readiness_sql
    assert "Summit Mortgage synthetic servicing feed" in readiness_sql


def _deploy_feed_switch(tmp_path: Path, **variables: str) -> tuple[int, str, str]:
    """Drive deploy.sh's demo-feed switch with ``variables`` as its shell state."""

    result = drive(
        tmp_path,
        "dotenv_value() { printf ''; }\n" + block(FEEDS_START, FEEDS_END),
        dry_run=1,
        env=variables,
        epilogue='echo "feeds=${MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS}"\n',
    )
    return result.returncode, result.stdout, result.stderr


@pytest.mark.parametrize(
    ("variables", "feeds"),
    (
        ({"CLIENT_DEMO_FEEDS": "1"}, "1"),
        ({"CLIENT_DEMO_FEEDS": "0"}, "0"),
        # The profile's value is the switch; an ambient export never overrides it.
        ({"CLIENT_DEMO_FEEDS": "0", "MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS": "1"}, "0"),
        ({"CLIENT_DEMO_FEEDS": "1", "MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS": "0"}, "1"),
    ),
)
def test_deploy_renders_the_profiles_demo_feed_policy(
    tmp_path: Path, variables: dict[str, str], feeds: str
) -> None:
    rc, out, err = _deploy_feed_switch(tmp_path, **variables)

    assert rc == 0, err
    assert f"feeds={feeds}" in out and "reached-after-block" in out


@pytest.mark.parametrize("value", ("", "TRUE", "on", "2"))
def test_deploy_refuses_a_feed_policy_that_is_not_the_profiles_closed_value(
    tmp_path: Path, value: str
) -> None:
    rc, out, err = _deploy_feed_switch(tmp_path, CLIENT_DEMO_FEEDS=value, MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS="1")

    assert rc == 2
    assert "the client profile's demo-feed policy did not resolve; refusing to render." in err
    assert "reached-after-block" not in out


def test_the_feed_switch_runs_only_after_the_client_profile_resolves(tmp_path: Path) -> None:
    """M0-PR4 moved the switch before the lease; it still follows resolve_client_profile."""

    text = deploy_entrypoint_text()
    assert text.index("\nresolve_client_profile\n") < text.index(FEEDS_START)
    assert text.index(FEEDS_END) < text.index("\nrun_bundle_plan_gate pre-lease\n")
    # Without the profile's policy the switch fails closed, never defaults.
    rc, out, err = _deploy_feed_switch(tmp_path)
    assert rc == 2 and "demo-feed policy did not resolve" in err
    assert "reached-after-block" not in out
