"""The deploy command-of-record as one reviewed text.

``scripts/deploy.sh`` sources function-only lifecycle libraries and, since the
2026-09-08 slicing, verbatim step files from ``scripts/lib/``. The contract
tests pin the wording and ordering of that orchestration, so they read the
script with the sliced files expanded back into place instead of the short
entrypoint.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = REPO / "scripts" / "deploy.sh"
_LIB = REPO / "scripts" / "lib"

# Function-only libraries reviewed before the slicing. They are sourced ahead
# of the orchestration, so the contracts written against the pre-slicing text
# keep them as source lines: expanding them in ``deploy_entrypoint_text`` would
# move their call sites ahead of every step and change the ``index``/``count``
# arithmetic those contracts rely on.
DEPLOY_REVIEWED_LIBRARIES = (
    _LIB / "deploy_agent_proxy_lifecycle.sh",
    _LIB / "deploy_verifier_gateway_lifecycle.sh",
    _LIB / "deploy_cutover_journal_lifecycle.sh",
    _LIB / "deploy_supervisor_creation_lifecycle.sh",
)
# Function-only libraries sliced verbatim out of the entrypoint (2026-09-08).
DEPLOY_SLICED_LIBRARIES = (
    _LIB / "deploy_app_access_lifecycle.sh",
    _LIB / "deploy_app_release_lifecycle.sh",
    _LIB / "deploy_first_install_lifecycle.sh",
    _LIB / "deploy_identity_lifecycle.sh",
)
# Top-level orchestration sliced verbatim out of the entrypoint, in step order.
DEPLOY_STEP_SCRIPTS = (
    _LIB / "deploy_step_preflight_gates.sh",
    _LIB / "deploy_step_automation_identities.sh",
    _LIB / "deploy_step_signed_blue_proof.sh",
    _LIB / "deploy_step_bundle_apply.sh",
    _LIB / "deploy_step_lakebase_and_uc_grants.sh",
    _LIB / "deploy_step_app_promotion_and_refresh.sh",
    _LIB / "deploy_step_agentic_provisioning.sh",
    _LIB / "deploy_step_agentic_cutover.sh",
    _LIB / "deploy_step_agent_eval_and_smoke.sh",
)
# Every sourced file in the order ``scripts/deploy.sh`` sources it.
DEPLOY_SOURCED_SCRIPTS = (
    DEPLOY_SLICED_LIBRARIES[0],
    *DEPLOY_REVIEWED_LIBRARIES,
    *DEPLOY_SLICED_LIBRARIES[1:],
    *DEPLOY_STEP_SCRIPTS,
)
_SLICED = frozenset((*DEPLOY_SLICED_LIBRARIES, *DEPLOY_STEP_SCRIPTS))


def _source_line(source: Path) -> str:
    return f'. "$REPO_ROOT/scripts/lib/{source.name}"'


def _directive_line(source: Path) -> str:
    return f"# shellcheck source=scripts/lib/{source.name}"


def _slice_body(source: Path) -> str:
    """Return a sliced file without its fixed header (everything to the first blank line)."""

    text = source.read_text(encoding="utf-8")
    assert text.startswith("# shellcheck shell=bash\n"), source.name
    header_end = text.index("\n\n")
    assert header_end < 1200, f"{source.name} header is not the fixed short header"
    return text[header_end + 2 :]


def _expand(script: str, sources: tuple[Path, ...]) -> str:
    for source in sources:
        if source in _SLICED:
            # The directive + source pair stands exactly where the slice was, so
            # putting the body back yields the pre-slicing text at that point.
            pair = f"{_directive_line(source)}\n{_source_line(source)}\n"
            assert script.count(pair) == 1, source.name
            script = script.replace(pair, _slice_body(source))
        else:
            source_line = _source_line(source)
            assert script.count(source_line) == 1, source.name
            script = script.replace(source_line, source.read_text(encoding="utf-8"))
    return script


def deploy_entrypoint_text() -> str:
    """``scripts/deploy.sh`` with its sliced libraries and step files back in place.

    Apart from two comment blocks that point at the slices, this is the
    command-of-record exactly as it read before the slicing, which is the text
    the ordering and count contracts were written against.
    """

    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    return _expand(script, (*DEPLOY_SLICED_LIBRARIES, *DEPLOY_STEP_SCRIPTS))


def deploy_contract_text() -> str:
    """The entrypoint with every sourced file expanded, reviewed libraries included."""

    return _expand(DEPLOY_SCRIPT.read_text(encoding="utf-8"), DEPLOY_SOURCED_SCRIPTS)
