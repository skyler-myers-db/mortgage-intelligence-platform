"""The demo seed names its operator by a token, rendered from the install's profile (M1-PR4).

lakebase/seed_campaigns.sql once committed one install's operator address on
nine rows (the sales_team admin row, the archived campaigns' owner and the
sample approvals' actor), so every install seeded that person. The rows now
carry ``{{mip_seed_operator_email}}``. The migrate job renders it from
``--seed-operator-email``, the ``seed_operator_email`` bundle variable that
tools/databricks/bundle_env.py sets from the client profile's demo-only
``MIP_SEED_OPERATOR_EMAIL``; with none it renders the synthetic
``operator@summit.example``. Plan and apply render the same text, so an
install that sets the address it seeded before renders the same seed, and the
same schema-input digest, as before the token.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from jobs import lakebase_migrate
from jobs.lakebase_migration_inputs import render_schema_inputs
from jobs.lakebase_migration_seed import (
    SEED_OPERATOR_EMAIL_ENV,
    SEED_OPERATOR_TOKEN,
    SYNTHETIC_SEED_OPERATOR,
    render_seed_sql,
)
from tests.fixtures.client_profiles import env_file_text, paychex_shaped_profile
from tools.databricks import bundle_env

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = (ROOT / "lakebase" / "seed_campaigns.sql").read_text(encoding="utf-8")
OPERATOR = "demo.operator@summit.example"
_AMBIENT_PREFIXES = ("MIP_", "DATABRICKS_", "BUNDLE_VAR_", "GENIE_", "LAKEBASE_", "APP_ENV")


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [name for name in os.environ if name.startswith(_AMBIENT_PREFIXES)]:
        monkeypatch.delenv(name)


def test_the_template_names_its_operator_only_by_the_token() -> None:
    assert TEMPLATE.count(SEED_OPERATOR_TOKEN) == 9
    # Every token sits in a single-quoted literal, the only place an address may go.
    assert TEMPLATE.count(f"'{SEED_OPERATOR_TOKEN}'") == 9
    assert "{{" not in TEMPLATE.replace(SEED_OPERATOR_TOKEN, "")


def test_a_configured_operator_replaces_every_token_and_nothing_else() -> None:
    rendered = render_seed_sql(TEMPLATE, seed_operator_email=f"  {OPERATOR}\n")

    assert rendered == TEMPLATE.replace(SEED_OPERATOR_TOKEN, OPERATOR)
    assert rendered.count(f"'{OPERATOR}'") == 9 and SEED_OPERATOR_TOKEN not in rendered


@pytest.mark.parametrize("unset", ["", "   "])
def test_no_operator_seeds_the_synthetic_one(unset: str) -> None:
    rendered = render_seed_sql(TEMPLATE, seed_operator_email=unset)

    assert rendered == TEMPLATE.replace(SEED_OPERATOR_TOKEN, SYNTHETIC_SEED_OPERATOR)
    assert SYNTHETIC_SEED_OPERATOR.endswith(".example")


@pytest.mark.parametrize(
    "value",
    [
        "not-an-address",
        "o'brien@summit.example",
        "a@summit.example,b@summit.example",
        "a@summit.example b@summit.example",
        "a@summit.example'); DROP TABLE mip_app.approvals; --",
    ],
)
def test_anything_but_one_address_refuses_without_echoing_it(value: str) -> None:
    with pytest.raises(RuntimeError) as refused:
        render_seed_sql(TEMPLATE, seed_operator_email=value)

    assert SEED_OPERATOR_EMAIL_ENV in str(refused.value) and value not in str(refused.value)


def test_an_unknown_template_token_refuses() -> None:
    with pytest.raises(RuntimeError, match="unknown template token"):
        render_seed_sql(TEMPLATE + "\n-- {{mip_other}}\n", seed_operator_email=OPERATOR)


def test_plan_and_apply_render_the_same_seed_for_the_same_operator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setattr(lakebase_migrate, "_resolve_connection", lambda: {"host": "test"})
    monkeypatch.setattr(
        lakebase_migrate, "_preflight_database_roles", lambda _kwargs, **_options: ("app-role", None)
    )
    monkeypatch.setattr(
        lakebase_migrate,
        "_run_transaction",
        lambda sql_texts, _kwargs, **_options: captured.update(sql_texts=sql_texts),
    )
    monkeypatch.setattr(lakebase_migrate, "_apply_app_role_grants", lambda _kwargs, **_options: None)

    lakebase_migrate.main(seed_operator_email=OPERATOR)
    _schema, planned = render_schema_inputs(
        ROOT,
        lender_name="Summit Mortgage",
        lender_nmls_id="",
        tenant_id="",
        seed_operator_email=OPERATOR,
    )

    assert captured["sql_texts"][1] == planned[1] == render_seed_sql(TEMPLATE, seed_operator_email=OPERATOR)


def test_the_parser_reads_the_flag_and_falls_back_to_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert lakebase_migrate.build_parser().parse_args([]).seed_operator_email == ""
    monkeypatch.setenv(SEED_OPERATOR_EMAIL_ENV, OPERATOR)
    assert lakebase_migrate.build_parser().parse_args([]).seed_operator_email == OPERATOR
    flagged = lakebase_migrate.build_parser().parse_args(["--seed-operator-email=x@summit.example"])
    assert flagged.seed_operator_email == "x@summit.example"


def test_the_bundle_passes_the_variable_to_the_migrate_job() -> None:
    bundle = yaml.safe_load((ROOT / "databricks.yml").read_text(encoding="utf-8"))
    (task,) = bundle["resources"]["jobs"]["mip_lakebase_migrate"]["tasks"]

    assert "--seed-operator-email=${var.seed_operator_email}" in task["spark_python_task"]["parameters"]
    assert bundle["variables"]["seed_operator_email"]["default"] == ""


def test_the_deploys_migration_plan_renders_the_same_operator() -> None:
    lifecycle = (ROOT / "scripts" / "lib" / "deploy_quiesce_window_lifecycle.sh").read_text(encoding="utf-8")
    plan = lifecycle[lifecycle.index('"$PYTHON" jobs/lakebase_migrate.py --mode plan') :]
    plan = plan[: plan.index("\n    plan_args+=")]

    assert '--seed-operator-email "$(deployment_control_value MIP_SEED_OPERATOR_EMAIL)"' in plan


def _client_validate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, profile: dict[str, str]) -> dict[str, str]:
    """The env bundle_env ``validate -t client`` would hand the CLI for ``profile``."""

    env_local = tmp_path / ".env.local"
    env_local.write_text(env_file_text(profile), encoding="utf-8")
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    monkeypatch.setattr(bundle_env, "BUNDLE_ROOT", tmp_path)
    monkeypatch.setattr(bundle_env.render_sql, "render", lambda **_kwargs: (1, 0, 0))
    seen: dict[str, str] = {}

    def fake_cli(command: list[str], *, env: dict[str, str], check: bool) -> object:
        seen.update(env)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(bundle_env.subprocess, "run", fake_cli)
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", "validate", "-t", "client"])
    assert bundle_env.main() == 0
    return seen


def test_the_profiles_operator_alone_reaches_the_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    profile = {**paychex_shaped_profile(), SEED_OPERATOR_EMAIL_ENV: OPERATOR}
    assert _client_validate(monkeypatch, tmp_path, profile)["BUNDLE_VAR_seed_operator_email"] == OPERATOR

    # An ambient export never reaches the CLI: a profile without the field binds none.
    monkeypatch.setenv("BUNDLE_VAR_seed_operator_email", "ambient@summit.example")
    unset = {name: value for name, value in profile.items() if name != SEED_OPERATOR_EMAIL_ENV}
    assert "BUNDLE_VAR_seed_operator_email" not in _client_validate(monkeypatch, tmp_path, unset)


def test_no_tracked_seed_or_bundle_text_names_an_operator_address() -> None:
    """The ratchet (test_no_client_identifiers_committed.py) covers every file; this pins the seed."""

    addresses = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", TEMPLATE)
    assert addresses and all(address.endswith("@summit.example") for address in addresses)


def test_every_module_that_applies_the_seed_renders_it_first() -> None:
    """A raw apply would write the literal token as an address (CI's real-PostgreSQL contracts).

    A module that reads the seed and executes SQL must render it; one that
    only inspects the template's text may read it raw.
    """

    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", "tests", "tools", "jobs", "scripts", "backend"],
        cwd=ROOT,
        capture_output=True,
        check=True,
        timeout=120,
    ).stdout.decode("utf-8")
    appliers: list[str] = []
    for rel in filter(None, listed.split("\0")):
        if not rel.endswith(".py") or rel == "tests/unit/test_lakebase_seed_operator.py":
            continue
        text = (ROOT / rel).read_text(encoding="utf-8")
        reads = re.search(r"seed_campaigns\.sql\"\)\.read_text\(", text)
        applies = re.search(r"_run_transaction\(|\.execute\(", text)
        if reads and applies:
            appliers.append(rel)
            assert "render_seed_sql(" in text, f"{rel} applies the raw seed template"
    # The migrate job, its plan inputs and the real-PostgreSQL and live contracts.
    assert {"jobs/lakebase_migrate.py", "tests/integration/test_sales_loop_postgres.py"} <= set(appliers)
    assert len(appliers) >= 20, appliers
