"""No code path of the GitHub tools reads a runtime secret value (attack G10).

The App's runtime secret scope (``mip-runtime`` by default) holds the
Cotality id mask, the Genie action keys and the Salesforce credentials.
They stay in the workspace, presence-only
(tools/databricks/provision_runtime_secrets.py), and must never be copied
to GitHub. So:

* lexically, no GitHub module names a runtime secret input or key, the
  runtime scope, the Secrets API, ``dbutils``, the SDK or the runtime-secret
  provisioner (the same scan finds them in that provisioner: a positive
  control);
* structurally, no name the GitHub tools can write is a runtime secret name;
* behaviourally, runtime inputs in the shell and in the local file never
  reach the environment tool's inputs, its output or gh's stdin.
"""

from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

import pytest

from tests.fixtures.client_profiles import demo_profile
from tests.fixtures.fake_gh import install_fake_gh
from tests.fixtures.github_environment_harness import plan_and_apply, write_profile
from tools.clients.github_environment_plan import (
    MODEL_SIGNING_KEY,
    PINS_FIELD,
    PROFILE_DIGEST_VARIABLE,
    PROOF_SIGNING_KEY,
    load_inputs,
)
from tools.clients.github_sink import MINTED_FIELDS
from tools.clients.schema import FIELD_CLASS
from tools.databricks import provision_runtime_secrets as runtime

ROOT = Path(__file__).resolve().parents[2]
GITHUB_MODULES = (
    *sorted((ROOT / "tools" / "clients").glob("github_*.py")),
    ROOT / "tools" / "databricks" / "m2m_oauth_github.py",
)
RUNTIME_NAMES = frozenset(
    {*runtime.ENV_TO_KEY, *runtime.OPTIONAL_RESOURCE_ENVS, runtime.PREVIOUS_KID_ENV, runtime.CURRENT_KID_ENV}
)
RUNTIME_TOKENS = frozenset(
    {
        *RUNTIME_NAMES,
        *runtime.ENV_TO_KEY.values(),
        runtime.DEFAULT_SCOPE,
        "provision_runtime_secrets",
        "get_secret",
        "dbutils",
        "databricks.sdk",
        "WorkspaceClient",
    }
)
CANARY = "canary-runtime-mask-4d9f01"


def runtime_references(source: str) -> set[str]:
    """Runtime-secret tokens in ``source``, and any read through a ``.secrets`` API."""

    found = {token for token in RUNTIME_TOKENS if re.search(rf"(?<![A-Za-z0-9_-]){re.escape(token)}(?![A-Za-z0-9_-])", source)}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and node.attr == "secrets" and isinstance(node.value, ast.Attribute):
            found.add(f"line {node.lineno}: <client>.secrets")
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(("databricks", "tools.databricks.provision")):
            found.add(f"line {node.lineno}: import {node.module}")
    return found


def test_the_scan_finds_runtime_secret_reads_in_the_runtime_provisioner() -> None:
    provisioner = (ROOT / "tools" / "databricks" / "provision_runtime_secrets.py").read_text(encoding="utf-8")
    found = runtime_references(provisioner)
    assert {"MIP_COTALITY_ID_MASK_SECRET", "genie-action-current", "mip-runtime"} <= found
    assert runtime_references("value = w.workspace.secrets.get_secret(scope, key)\n") >= {"get_secret"}


@pytest.mark.parametrize("path", GITHUB_MODULES, ids=lambda path: path.name)
def test_no_github_module_reads_a_runtime_secret(path: Path) -> None:
    assert runtime_references(path.read_text(encoding="utf-8")) == set()


def test_no_name_the_github_tools_write_is_a_runtime_secret() -> None:
    writable = {*FIELD_CLASS, PROFILE_DIGEST_VARIABLE, PINS_FIELD, PROOF_SIGNING_KEY, MODEL_SIGNING_KEY, *MINTED_FIELDS}
    assert RUNTIME_NAMES and not writable & RUNTIME_NAMES
    assert len(GITHUB_MODULES) == 6


def test_runtime_inputs_never_reach_the_environment(tmp_path: Path) -> None:
    runtime_values = {name: f"{CANARY}-{index}" for index, name in enumerate(sorted(RUNTIME_NAMES))}
    env = write_profile(tmp_path / "input" / "runtime.env", {**demo_profile(), **runtime_values})
    inputs = load_inputs(runtime_values, env)
    assert not (set(inputs.variables) | set(inputs.identifying)) & RUNTIME_NAMES
    fake = install_fake_gh(tmp_path / "github")
    planned, applied = plan_and_apply(fake, env, environ=runtime_values)
    assert applied.code == 0, applied.err
    digests = {call["stdin_sha256"] for call in fake.calls()}
    for value in runtime_values.values():
        assert hashlib.sha256(value.encode()).hexdigest() not in digests
        assert value not in planned.out + applied.out + applied.err
    stored = fake.state()["environments"]["fictional-demo"]
    assert not (set(stored["secrets"]) | set(stored["variables"])) & RUNTIME_NAMES
    assert fake.state()["violations"] == []
