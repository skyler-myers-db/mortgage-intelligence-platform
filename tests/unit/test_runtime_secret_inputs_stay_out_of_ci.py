"""No workflow or script carries a runtime HMAC secret value.

The runtime secret scope is presence-only (``provision_runtime_secrets.py``):
an existing key is kept unread, and an absent one is generated on the runner.
So no workflow binds, requires, writes or un-exports the three inputs, and no
deploy script resolves them. They remain optional first-install inputs of the
provisioner only, read from an operator's own shell or ``.env.local``.

The pattern is checked against the provisioner itself (a positive control), so
an absence here is not the vacuous result of a pattern that matches nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_HMAC_INPUTS = re.compile(
    r"\b(?:MIP_COTALITY_ID_MASK_SECRET|MIP_GENIE_ACTION_SECRET_CURRENT|MIP_GENIE_ACTION_SECRET_PREVIOUS)\b"
)
CONTROL = ROOT / "tools" / "databricks" / "provision_runtime_secrets.py"


def _ci_and_script_files() -> list[Path]:
    roots = (ROOT / ".github" / "workflows", ROOT / "scripts")
    return sorted(path for root in roots for path in root.rglob("*") if path.is_file())


def test_the_pattern_finds_each_input_in_the_provisioner() -> None:
    found = set(RUNTIME_HMAC_INPUTS.findall(CONTROL.read_text(encoding="utf-8")))

    assert found == {
        "MIP_COTALITY_ID_MASK_SECRET",
        "MIP_GENIE_ACTION_SECRET_CURRENT",
        "MIP_GENIE_ACTION_SECRET_PREVIOUS",
    }


def test_the_pattern_leaves_the_public_key_ids_alone() -> None:
    assert not RUNTIME_HMAC_INPUTS.search("MIP_GENIE_ACTION_SECRET_PREVIOUS_KID MIP_GENIE_ACTION_SECRET_KID")


def test_no_workflow_or_script_references_a_runtime_hmac_input() -> None:
    files = _ci_and_script_files()
    assert ROOT / ".github" / "workflows" / "deploy-dev.yml" in files
    assert ROOT / "scripts" / "lib" / "deploy_step_preflight_gates.sh" in files

    hits = [
        f"{path.relative_to(ROOT)}:{number}"
        for path in files
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
        if RUNTIME_HMAC_INPUTS.search(line)
    ]

    assert hits == []
