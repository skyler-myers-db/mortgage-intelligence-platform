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
* ``agentic_only_modules``: the tools only the agentic tier's deploy slices
  run, read from a tree; ``AGENTIC_ONLY_MODULES`` is the same set committed,
  since a set read from the tree under test shrinks when a core file starts
  calling an agentic tool (review of #330);
* ``core_deploy_tools``: every tool a core-tier deploy file names, which
  tests/unit/test_client_portability_integrity.py holds to the reviewed
  tables and to ``role_ledger.OUTSIDE_PROOF_MODULES`` (the tools no dry run
  the proof replays plans or runs: a live-only branch, another phase or a
  mention in a message);
* both read a deploy file word by word (``_named_tools``, review of #330,
  round 3): the word after any ``-m`` and any word role_ledger.TOOL_PATH
  reads as a tool, past quotes, line continuations and array bodies, so
  ``-m "tools.x"``, an array's ``-m`` on its own line and
  ``"$ROOT_DIR/tools/x.py"`` are read as the call classifier reads them;
  tests/fixtures/role_ledger.py has ``account_api_reach``, the check behind
  its table;
* ``default_install_render``: client #1's render, read from the sql/ sources
  themselves rather than from tools/render_sql.py;
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
from tests.fixtures.role_ledger import TOOL_MODULE, TOOL_PATH
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
# What separates the words of a deploy file: whitespace (newlines included,
# so a line continuation or an array body is one run of words), quotes, a
# backslash and the shell's operators and expansion brackets, with the
# ``=``, ``,`` and ``:`` that join a word to an option or a message.
_WORD_BREAK = re.compile(r"""[\s'"`\\;|&()<>{}\[\]=,:]+""")
# agentic_only_modules() of the reviewed tree, committed: a test holds the
# tree to it, so a core file that starts calling one of these fails both ways.
AGENTIC_ONLY_MODULES = frozenset(
    {
        "tools.databricks.agent_proxy_access",
        "tools.databricks.agent_runtime_access",
        "tools.databricks.converge_verifier_gateway_access",
        "tools.databricks.export_gateway_runtime_contract",
        "tools.databricks.grant_ai_gateway_inference_table",
        "tools.databricks.provision_agentic_resources",
        "tools.databricks.read_agent_tool_probe_expectation",
        "tools.databricks.reconcile_historical_agent_endpoints",
        "tools.databricks.run_agent_eval",
        "tools.databricks.supervisor_creation_control",
        "tools.databricks.supervisor_creation_runtime",
        "tools.databricks.verify_agent_proxy_identity_boundary",
        "tools.databricks.verify_agent_proxy_uc_boundary_dual_authority",
        "tools.databricks.verify_agent_runtime_identity_boundary",
        # Named only as an array body's ``-m`` (deploy_step_agentic_cutover.sh,
        # AI_GATEWAY_PROOF_ARGS), which round 3 of the review of #330 found
        # the reader missed.
        "tools.databricks.verify_ai_gateway_exact_proof",
        "tools.databricks.verify_hosted_agent_tool_execution",
        "tools.databricks.verify_lakebase_oauth_identity",
        "tools.databricks.verify_verifier_identity_boundary",
        "tools.verify_app_agent_green_path",
        "tools.verify_deployed_app_contract",
    }
)
# The demo switch, the one token client #1's render (catalog mip, the demo
# feeds on, the canonical share, no pinned lender) changes in a source file.
DEMO_SWITCH = "{{mip_enable_demo_first_party_feeds}}"


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


def _named_tools(text: str) -> set[str]:
    """Every tool a deploy file names: the word after any ``-m``, and any word that is a tool's path."""

    words = [word.rstrip(".") for word in _WORD_BREAK.split(text)]
    named = {after for flag, after in zip(words, words[1:], strict=False) if flag == "-m" and TOOL_MODULE.fullmatch(after)}
    return named | {match.group(1).replace("/", ".") for word in words if (match := TOOL_PATH.fullmatch(word))}


def _deploy_files(scripts: Path) -> list[tuple[Path, bool]]:
    """Each deploy file and whether it is one of the agentic slices or their libraries."""

    files = sorted([scripts / "deploy.sh", *(scripts / "lib").glob("*.sh")])
    return [(path, AGENTIC_MARKER in path.read_text(encoding="utf-8") or path.name in AGENTIC_LIBRARIES) for path in files]


def agentic_only_modules(scripts: Path = REPO / "scripts") -> frozenset[str]:
    """The tools only the agentic slices name (``-m`` or by path), never another deploy script."""

    agentic: set[str] = set()
    elsewhere: set[str] = set()
    for path, is_agentic in _deploy_files(scripts):
        (agentic if is_agentic else elsewhere).update(_named_tools(path.read_text(encoding="utf-8")))
    return frozenset(agentic - elsewhere)


def core_deploy_tools(scripts: Path = REPO / "scripts") -> frozenset[str]:
    """Every tool a core-tier deploy file names (``-m`` or by path), whether a dry run reaches it or not."""

    return frozenset().union(*(_named_tools(path.read_text(encoding="utf-8")) for path, agentic in _deploy_files(scripts) if not agentic))


def default_install_render(dest: Path, sql: Path = REPO / "sql") -> Path:
    """Client #1's render read from the sources, never from tools/render_sql.py.

    Every canonical source file with the demo switch on and nothing else
    changed: the sources name catalog ``mip``, ``mip``'s schemas and the
    canonical share already. A fixed reference, so a change to the renderer
    that alters every install's SQL, client #1's included, fails the pin
    (review of #330), while a reviewed change to sql/ moves both sides.
    """

    for source in sorted(sql.rglob("*.sql")):
        relative = source.relative_to(sql)
        if "_rendered" in relative.parts:
            continue
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8").replace(DEMO_SWITCH, "TRUE"), encoding="utf-8")
    return dest


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
