"""The offline deploy sandbox inherits no profile name from the shell (M1-PR7 review).

A non-empty shell value beats .env.local (tools/clients/environment.from_local),
so an ambient profile name changes a sandboxed deploy's profile. The Lakebase
tools write LAKEBASE_DATABASE straight into os.environ, and a test that ran
first in the same xdist worker made the dispatch e2e refuse its profile, by
test order only (tests/unit/test_public_summary_redaction.py now runs it with
such names set).
"""

from __future__ import annotations

from tests.fixtures.deploy_sandbox import INHERITED_PREFIXES
from tests.fixtures.lakebase_env import LAKEBASE_ENV_NAMES
from tools.clients.environment import KNOWN_NAMES


def test_every_profile_field_and_alias_name_is_stripped() -> None:
    assert sorted(name for name in KNOWN_NAMES if not name.startswith(INHERITED_PREFIXES)) == []


def test_every_lakebase_name_a_tool_writes_into_the_environment_is_stripped() -> None:
    assert [name for name in LAKEBASE_ENV_NAMES if not name.startswith(INHERITED_PREFIXES)] == []
