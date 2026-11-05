"""Ratchet: the deleted foreign-catalog binding remediation leaves no trace (M4-PR2).

The remediation flipped other teams' catalogs on the shared metastore to
ISOLATED and rewrote their workspace bindings. Its CI input went in M0-PR3,
the deploy stop branches that ran it in M0-PR5, and M4-PR2 deleted the tool,
its ``foreign_catalog_binding_*`` modules (catalog state, signed journal,
signed manifest, binding-policy parser), the auditor helpers only the manifest
called, their tests and their doc sections. This scan holds every tracked file
to zero mentions of them, except this file and the dated records below.

Two names deliberately survive, and neither is the tool:

* ``MIP_REMEDIATE_FOREIGN_CATALOG_BINDINGS``: scripts/deploy.sh refuses any
  value but ``0``, from the environment or a stale ``.env.local``, each read on
  its own, so neither can bring the remediation back silently and an export of
  ``0`` cannot mask the file. Only the files below may name it, and the
  workflow never does.
* ``retire_foreign_catalog_binding_policy``: M4-PR1's refusal of the retired
  ``MIP_UC_FOREIGN_CATALOG_BINDING_POLICY``. One lookbehind spares exactly that
  prefix; no alternative has a leading word boundary, so an underscore-prefixed
  spelling (``test_foreign_catalog_binding_journal``) is still seen.

The read-only binding check that borrowed the policy module's binding-type set
(``_catalog_bindings``, which the owner's exposure-candidate listing still
calls) keeps it, now defined in the auditor, and still refuses an incomplete,
unknown or duplicate binding.

The scan is ``git grep -P`` (``git grep -E`` has no ``\\b`` on macOS), so the
positive control proves it finds each alternative in this file, and each
prefixed spelling in a probe file, before any absence is trusted. It reads
counts per path, never a matched line, and leaves out the ``.env.*``
templates: agents may not read them, even indirectly, under the repository's
permission settings. A template that copied the flag into ``.env.local`` would
still refuse (``test_prestop_refusal_matrix.py``).
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.fixtures.deploy_script import deploy_contract_text
from tools.databricks import audit_agent_runtime_foreign_uc_access as auditor

ROOT = Path(__file__).resolve().parents[2]
SELF = "tests/unit/test_no_foreign_catalog_remediation_references.py"
DELETED_MODULES = (
    "converge_foreign_catalog_workspace_bindings",
    "foreign_catalog_binding_catalog",
    "foreign_catalog_binding_journal",
    "foreign_catalog_binding_manifest",
    "foreign_catalog_binding_policy",
)
DELETED = (
    r"converge_foreign_catalog_workspace_bindings"
    r"|(?<!retire_)foreign_catalog_binding_"
    r"|parse_foreign_catalog_binding_policy"
    r"|remediate_foreign_catalog_bindings"
    r"|foreign-catalog-binding"
    r"|foreign-catalog-manifest"
    r"|_assert_metastore_owner_inventory_identity"
    r"|_account_group_evidence"
)
# Positive-control spellings, one per alternative above (the scan must find
# each here): converge_foreign_catalog_workspace_bindings
# foreign_catalog_binding_manifest parse_foreign_catalog_binding_policy
# remediate_foreign_catalog_bindings foreign-catalog-binding-event
# mip-foreign-catalog-manifest _assert_metastore_owner_inventory_identity
# _account_group_evidence
# Prefixed spellings the scan must also see (the deleted test files' names and
# the deleted parser tests'), and the surviving names it must not.
PREFIXED = (
    "tests/unit/test_converge_foreign_catalog_workspace_bindings.py",
    "tests/unit/test_foreign_catalog_binding_journal.py",
    "def test_foreign_catalog_binding_policy_rejects_an_unknown_workspace",
    "test_remediate_foreign_catalog_bindings_is_refused",
    "auditor.x_assert_metastore_owner_inventory_identity",
    "self_account_group_evidence",
)
SURVIVING = (
    "retire_foreign_catalog_binding_policy",
    "MIP_UC_FOREIGN_CATALOG_BINDING_POLICY",
    "_REMEDIATE_FOREIGN_CATALOG_BINDINGS",
)
REFUSED_FLAG = "MIP_REMEDIATE_FOREIGN_CATALOG_BINDINGS"
# Each file that may name the refused flag, and why.
FLAG_FILES = {
    SELF: "this ratchet",
    "scripts/deploy.sh": "the refusal itself",
    "docs/deployment.md": "the pre-stop probe section's refusal note",
    "docs/security/GRANTS.md": "why it is refused; the refusal message cites this file",
    "tests/unit/test_prestop_refusal_matrix.py": "the refusal fires from the environment and from .env.local",
    "tests/unit/test_deploy_dev_workflow_contract.py": "the workflow never names it",
    "tests/unit/test_live_install_no_go_docs.py": "GRANTS.md's record of the deletion",
}
_TEMPLATES = (":(exclude).env*", ":(exclude,glob)**/.env*")
# Dated records, not code, tests, workflows or docs (as in the retired CI staging
# install's ratchet): an agent-memory note or an audit report may record the
# deleted tooling by name.
DATED_RECORDS = (".claude/agent-memory/", "docs/audits/")
_GUARD_START = "# >>> foreign-catalog remediation refused >>>\n"
_GUARD_END = "# <<< foreign-catalog remediation refused <<<\n"


def _counts(pattern: str, *paths: str) -> Counter[str]:
    """Matching line count per tracked path; paths only, never a matched line."""

    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout: the scan reads tracked files through git grep")
    result = subprocess.run(
        ["git", "grep", "-I", "-c", "-P", "-e", pattern, "--", *(paths or (".", *_TEMPLATES))],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode in (0, 1), f"git grep -P failed (exit {result.returncode}); is git built with PCRE?"
    counts: Counter[str] = Counter()
    for line in result.stdout.splitlines():
        path, _, count = line.rpartition(":")
        counts[path] = int(count)
    return counts


def _probe(tmp_path: Path, lines: tuple[str, ...]) -> int:
    """How many of ``lines`` the scan's own ``git grep -P`` matches, in an untracked probe file."""

    probe = tmp_path / "probe.txt"
    probe.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    result = subprocess.run(
        ["git", "grep", "--no-index", "-c", "-P", "-e", DELETED, "--", probe.name],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode in (0, 1), f"git grep -P failed (exit {result.returncode}); is git built with PCRE?"
    return int(result.stdout.rpartition(":")[2] or 0)


def _offenders(counts: Counter[str], *allowed: str) -> list[str]:
    """Every matching path outside ``allowed`` and the dated records, with its line count."""

    return sorted(
        f"{path} ({count} lines)"
        for path, count in counts.items()
        if path not in allowed and not path.startswith(DATED_RECORDS)
    )


def test_the_scan_finds_every_alternative_in_its_own_file(tmp_path: Path) -> None:
    """The positive control: an absence below means nothing unless each alternative matches here."""

    assert _counts(DELETED)[SELF] > 0
    alternatives = DELETED.split("|")
    assert len(alternatives) == 8
    for alternative in alternatives:
        assert _counts(alternative, SELF)[SELF] > 0, f"git grep -P found no {alternative!r} here"
    # No leading boundary: each prefixed spelling is seen, one line apiece.
    assert _probe(tmp_path, PREFIXED) == len(PREFIXED)
    # The lookbehind spares the surviving M4-PR1 guard (and the scan is case
    # sensitive, so the two flags' names): the scan sees the guard as a name,
    # so its absence from the deleted names is a fact, not a blind spot.
    assert _probe(tmp_path, SURVIVING) == 0
    assert _counts(r"\bretire_foreign_catalog_binding_policy\b")["scripts/lib/deploy_estate_inventory_lifecycle.sh"]
    assert not _counts(DELETED, "scripts/lib/deploy_estate_inventory_lifecycle.sh")


def test_only_code_tests_workflows_and_docs_are_held_to_zero() -> None:
    counts = Counter(
        {
            SELF: 9,
            ".claude/agent-memory/reviewer/project_note.md": 2,
            "docs/audits/2026-08-platform-audit.md": 1,
            "docs/deployment.md": 1,
            "tests/unit/test_foreign_catalog_binding_journal.py": 3,
        }
    )

    assert _offenders(counts, SELF) == [
        "docs/deployment.md (1 lines)",
        "tests/unit/test_foreign_catalog_binding_journal.py (3 lines)",
    ]


def test_no_tracked_file_but_this_one_names_the_deleted_tooling() -> None:
    offenders = _offenders(_counts(DELETED), SELF)

    assert offenders == [], "remove these mentions of the deleted remediation tooling: " + ", ".join(offenders)


@pytest.mark.parametrize("module", DELETED_MODULES)
def test_no_deleted_module_can_be_imported(module: str) -> None:
    assert importlib.util.find_spec(f"tools.databricks.{module}") is None
    assert not (ROOT / "tools" / "databricks" / f"{module}.py").exists()
    assert not (ROOT / "tests" / "unit" / f"test_{module}.py").exists()


def test_only_the_refusal_and_its_records_name_the_refused_flag() -> None:
    counts = _counts(rf"\b{REFUSED_FLAG}\b")

    assert _offenders(counts, *FLAG_FILES) == []
    assert not any(path.startswith(".github/") for path in counts), "no workflow may pass the flag"
    assert counts["scripts/deploy.sh"] > 0, "the positive control: the refusal names it"


def test_the_refusal_still_reads_every_source_and_refuses_anything_but_zero() -> None:
    """The guard's shape and position; test_prestop_refusal_matrix.py runs it from each source."""

    script = deploy_contract_text()
    assert script.count(_GUARD_START) == 1 and script.count(_GUARD_END) == 1
    before, rest = script.split(_GUARD_START, 1)
    commented, after = rest.split(_GUARD_END, 1)
    # Only code counts: a comment that kept an old read would satisfy a substring.
    guard = "\n".join(line for line in commented.splitlines() if not line.lstrip().startswith("#"))

    # Each source on its own, so an export of 0 cannot mask a .env.local request.
    assert f'_REMEDIATE_FOREIGN_CATALOG_BINDINGS="${{{REFUSED_FLAG}:-0}}"' in guard
    assert f'_REMEDIATE_FOREIGN_CATALOG_BINDINGS_DOTENV="$(dotenv_value {REFUSED_FLAG})"' in guard
    assert (
        'if [[ "$_REMEDIATE_FOREIGN_CATALOG_BINDINGS" != "0" '
        '|| "${_REMEDIATE_FOREIGN_CATALOG_BINDINGS_DOTENV:-0}" != "0" ]]; then'
    ) in guard
    assert f"deployment_control_value {REFUSED_FLAG}" not in guard
    assert f"[deploy] {REFUSED_FLAG} is refused" in guard
    assert "exit 2" in guard
    # Nothing else in the deploy reads, exports or forwards it.
    assert REFUSED_FLAG not in before and REFUSED_FLAG not in after
    # Before the first step file is sourced: the libraries above it only
    # define functions, so every Databricks call, in a dry run too, comes after.
    entrypoint = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    first_step = re.search(r'(?m)^\. "\$REPO_ROOT/scripts/lib/deploy_step_\w+\.sh"$', entrypoint)
    assert first_step and entrypoint.index(_GUARD_END) < first_step.start()


def test_the_read_only_binding_check_keeps_its_reviewed_binding_types() -> None:
    reviewed = frozenset({"BINDING_TYPE_READ_ONLY", "BINDING_TYPE_READ_WRITE"})
    assert reviewed == auditor._WORKSPACE_BINDING_TYPES

    def workspace(*bindings: tuple[str, str]) -> SimpleNamespace:
        listed = [SimpleNamespace(workspace_id=item, binding_type=kind) for item, kind in bindings]
        return SimpleNamespace(workspace_bindings=SimpleNamespace(get_bindings=lambda *_args: iter(listed)))

    accepted = auditor._catalog_bindings(
        workspace(("101", "binding_type_read_only"), ("202", "BINDING_TYPE_READ_WRITE")), catalog="shared"
    )
    assert accepted == {"101": "BINDING_TYPE_READ_ONLY", "202": "BINDING_TYPE_READ_WRITE"}
    for bindings, refusal in (
        ((("101", "BINDING_TYPE_UNKNOWN"),), "incomplete workspace binding"),
        ((("", "BINDING_TYPE_READ_ONLY"),), "incomplete workspace binding"),
        ((("101", ""),), "incomplete workspace binding"),
        ((("101", "BINDING_TYPE_READ_ONLY"), ("101", "BINDING_TYPE_READ_WRITE")), "duplicate workspace bindings"),
    ):
        with pytest.raises(RuntimeError, match=refusal):
            auditor._catalog_bindings(workspace(*bindings), catalog="shared")
