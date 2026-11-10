"""The second-client portability proof's checks (M3-PR4), each one a function a positive control can fail.

tests/unit/test_client_portability_proof.py applies them to acme's replayed
dispatches (tests/fixtures/client_portability.py) and to a replay with
planted defects, so each check is shown to find what it looks for:

* ``leaks``: an identifying value in a line, in any letter case (a runner mask
  matches exactly, so a re-cased value is a leak even where it is masked);
* ``install_literals`` / ``literal_hits``: a value of the client-#1-shaped
  install (tests/fixtures/client_profiles.paychex_shaped_profile, placeholders
  only) that acme configures differently, wherever acme's run names it: a
  client literal in code;
* ``agentic_only_modules``: the tools only the agentic tier's deploy slices run;
* ``checkout_state``: the real checkout's status and the build artifacts a
  dry run would write there;
* ``app_settings``: the App's settings, read in a fresh process with exactly
  a payload's literal env;
* ``one_char_tampers``: every one-character substitution of a JSON value.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from backend.config import source_names
from backend.schemas.lender_identity_record import reviewed_lender_identity_from_env
from tests.fixtures.client_profiles import paychex_shaped_profile
from tests.fixtures.role_recording_cli import tree_sha256
from tools.clients import profile as views
from tools.clients.environment import RawProfile, resolve
from tools.clients.shapes import https_host

REPO = Path(__file__).resolve().parents[2]
# The public fields that name a resource of the install, beside every identifying leaf.
RESOURCE_NAME_FIELDS = (
    "MIP_CLIENT",
    "MIP_APP_NAME",
    "MIP_LAKEBASE_INSTANCE",
    "LAKEBASE_DATABASE",
    "MIP_LAKEBASE_SYNC_CATALOG",
    "MIP_LAKEBASE_SYNC_SCHEMA",
    "MIP_RUNTIME_SECRET_SCOPE",
    "MIP_APP_ROLLBACK_SECRET_SCOPE",
    "MIP_AGENT_PROXY_SECRET_SCOPE",
    "MIP_GENIE_SPACE_NAME",
    "MIP_AGENT_SUPERVISOR_NAME",
    "MIP_AI_GATEWAY_TABLE_PREFIX",
    "MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE",
)
# Prefix-shaped values: matched with a left boundary only, since a table name follows.
PREFIX_FIELDS = frozenset({source_names.TABLE_PREFIX_ENV})
# The deploy's agentic step slices (each refuses the core tier) and the
# function libraries only they call.
AGENTIC_MARKER = "is agentic-only and refuses the core tier"
AGENTIC_LIBRARIES = (
    "deploy_agent_proxy_lifecycle.sh",
    "deploy_verifier_gateway_lifecycle.sh",
    "deploy_supervisor_creation_lifecycle.sh",
)
# Build artifacts a dry run, a render or a validate would write in the checkout it runs in.
ARTIFACTS = ("sql/_rendered", ".databricks", ".env.local", "genie/space_id.txt")
_MODULE = re.compile(r"-m (tools(?:\.[A-Za-z0-9_]+)+)")


def leaks(lines: Iterable[str], forbidden: Sequence[str]) -> list[tuple[str, str]]:
    """``(value, line)`` for each forbidden value a line holds in any letter case."""

    folded = [(value, value.casefold()) for value in forbidden if value]
    return [(value, line) for line in lines for value, low in folded if low in line.casefold()]


def _own_values(values: Mapping[str, str]) -> set[str]:
    """Every value the client's own profile names or derives (its account host, say), case-folded."""

    own = resolve(RawProfile(values=dict(values)))
    leaves = (value for _field, value in views.identifying_leaves(own))
    named = (*views.canonical_env(own).values(), *leaves, *views.derived_masks(own), views.account_host(own))
    return {value.casefold() for value in named} | {https_host(views.account_host(own)).casefold()}


def install_literals(values: Mapping[str, str]) -> dict[str, str]:
    """Each client-#1-shaped install value acme configures differently, to the field it came from.

    The resource-name fields, the canonical Cotality source names (the shaped
    profile leaves them unset), every identifying leaf and the demo tenant the
    shaped install serves (it pins no lender record). A value acme's own
    profile names or derives too (the platform's account host) is no literal.
    """

    shaped = paychex_shaped_profile()
    shaped_profile = resolve(RawProfile(values=shaped))
    canonical = source_names.CANONICAL
    own = _own_values(values)
    named: dict[str, str] = {}
    for field in RESOURCE_NAME_FIELDS:
        if shaped[field] != values.get(field):
            named[shaped[field]] = field
    for field, value in (
        (source_names.CATALOG_ENV, canonical.catalog),
        (source_names.SCHEMA_ENV, canonical.schema),
        (source_names.TABLE_PREFIX_ENV, canonical.table_prefix),
    ):
        if value != values.get(field):
            named[value] = field
    for field, value in views.identifying_leaves(shaped_profile):
        named.setdefault(value, field)
    demo = reviewed_lender_identity_from_env({})
    named.setdefault(demo.lender_name, "MIP_LENDER_NAME (demo default)")
    return {value: field for value, field in named.items() if value.casefold() not in own}


def _literal(value: str, field: str) -> re.Pattern[str]:
    right = "" if field in PREFIX_FIELDS else r"(?![A-Za-z0-9_-])"
    # A bundle resource selector (``database_instances.<key>``) names a resource key, not a value.
    return re.compile(r"(?<![A-Za-z0-9_.-])" + re.escape(value) + right, re.IGNORECASE)


def literal_hits(
    lines: Iterable[str], literals: Mapping[str, str], exempt: Iterable[tuple[str, str]] = ()
) -> list[tuple[str, str]]:
    """``(field, line)`` for each install literal a line names, past the ``(field, line prefix)`` exemptions."""

    patterns = [(field, _literal(value, field)) for value, field in literals.items()]
    waived = tuple(exempt)
    return [
        (field, line)
        for line in lines
        for field, pattern in patterns
        if pattern.search(line) and not any(field == f and line.lstrip().startswith(p) for f, p in waived)
    ]


def agentic_only_modules(scripts: Path = REPO / "scripts") -> frozenset[str]:
    """The ``-m`` tools only the agentic slices name, never another deploy script."""

    agentic: set[str] = set()
    elsewhere: set[str] = set()
    for path in sorted([scripts / "deploy.sh", *(scripts / "lib").glob("*.sh")]):
        text = path.read_text(encoding="utf-8")
        is_agentic = AGENTIC_MARKER in text or path.name in AGENTIC_LIBRARIES
        (agentic if is_agentic else elsewhere).update(_MODULE.findall(text))
    return frozenset(agentic - elsewhere)


def checkout_state(repo: Path) -> dict[str, str | None]:
    """The checkout's ``git status`` and a digest of each build artifact (``None`` when absent)."""

    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    state: dict[str, str | None] = {"git status": status}
    for rel in ARTIFACTS:
        path = repo / rel
        if path.is_dir():
            state[rel] = tree_sha256(path)
        else:
            state[rel] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    return state


_SETTINGS_PROBE = """
import json, os
from backend.config.settings import settings
identity = settings.reviewed_lender_identity()
print(json.dumps({
    "app_env": settings.app_env,
    "catalog": settings.mip_default_catalog,
    "lakebase_instance": os.environ.get("LAKEBASE_INSTANCE_NAME"),
    "lakebase_database": settings.lakebase_database,
    "sync_catalog": settings.mip_lakebase_sync_catalog,
    "sync_schema": settings.mip_lakebase_sync_schema,
    "source": [settings.mip_cotality_source_catalog, settings.mip_cotality_source_schema,
               settings.mip_cotality_source_table_prefix],
    "lender": [identity.lender_name, identity.nmls_id, identity.tenant_id, identity.digest],
    "agent_orchestrator": settings.mip_agent_orchestrator,
    "ai_gateway": settings.mip_ai_gateway,
    "presenter_mode": settings.mip_presenter_mode,
}, sort_keys=True))
"""


def literal_env(payload: Mapping[str, Any]) -> dict[str, str]:
    """A payload's literal env entries (its resource bindings resolve only on the platform)."""

    return {str(item["name"]): str(item["value"]) for item in payload["env_vars"] if "value" in item}


def app_settings(env: Mapping[str, str], cwd: Path) -> dict[str, Any]:
    """The App's settings in a fresh process started with exactly ``env``, in a directory with no dotenv."""

    completed = subprocess.run(
        [sys.executable, "-P", "-c", _SETTINGS_PROBE],
        cwd=cwd,
        env={**env, "PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(REPO)},
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    return dict(json.loads(completed.stdout.splitlines()[-1]))


def _substitute(char: str) -> str:
    """Another character of the same class, so a tamper stays well-formed where it can."""

    for alphabet in ("0123456789", "abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
        if char in alphabet:
            return alphabet[(alphabet.index(char) + 1) % len(alphabet)]
    return "x"


def one_char_tampers(text: str) -> Iterator[tuple[int, str]]:
    """``(position, text)`` for each one-character substitution of ``text``."""

    for index, char in enumerate(text):
        yield index, text[:index] + _substitute(char) + text[index + 1 :]
