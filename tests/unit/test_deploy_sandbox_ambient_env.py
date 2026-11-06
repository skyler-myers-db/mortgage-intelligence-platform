"""The deploy sandbox never inherits a client-profile name from the test process.

deploy.sh reads a shell value over .env.local. Some profile names carry none of
the stripped prefixes (LAKEBASE_DATABASE, LAKEBASE_INSTANCE_NAME, APP_ENV), and
code under test writes them into os.environ, so an earlier module on the same
xdist worker could leak one into a later dry run, which then refused the profile
("must agree"): an order-dependent, CI-only failure. The strip set is derived
from tools/clients/schema.py, so a new unprefixed field is covered the day it
is added.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.deploy_sandbox import INHERITED_PREFIXES, PROFILE_ENV_NAMES, inherited, sandbox
from tools.clients import schema


def test_every_profile_field_and_alias_is_stripped_whatever_its_prefix() -> None:
    names = set(schema.FIELD_CLASS) | set(schema.IDENTITY_ALIASES)
    for canonical, aliases in schema.ALIASES.items():
        names.update((canonical, *aliases))

    assert names == PROFILE_ENV_NAMES
    unprefixed = sorted(name for name in names if not name.startswith(INHERITED_PREFIXES))
    assert {"LAKEBASE_DATABASE", "LAKEBASE_INSTANCE_NAME", "APP_ENV"} <= set(unprefixed), "the cases this guards"
    assert all(inherited(name) for name in names)
    assert not inherited("PATH") and not inherited("HOME") and not inherited("PGHOST"), "only profile names and prefixes"


@pytest.mark.parametrize("name", ["LAKEBASE_DATABASE", "LAKEBASE_INSTANCE_NAME", "APP_ENV"])
def test_a_leaked_profile_name_never_reaches_a_sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv(name, "leaked-by-an-earlier-test")

    _checkout, env, _log = sandbox(tmp_path)

    assert env.get(name) != "leaked-by-an-earlier-test"
