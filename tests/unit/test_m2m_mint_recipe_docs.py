"""Every documented M2M mint names the client's GitHub Environment (M2-PR2).

``provision_m2m_oauth`` refuses a mint without ``--set-gh-secrets``, a
``--gh-repo`` and ``--gh-env <client>`` before its first SDK call. ``--gh-repo``
defaults to the reviewed origin; ``--gh-env`` has no default. A recipe that
leaves it out is refused when an operator follows it word for word, so this
file runs the docs through the tool instead of grepping them:

* each rotation instruction (a clause naming both ``--rotate`` and
  ``--set-gh-secrets``) in the runbook, deployment.md and m2m-oauth-setup.md
  becomes an argv of exactly the flags it names, and ``main`` must get past
  the flag gate to the sink's protection check for every identity role;
* each ``provision_m2m_oauth.py`` command in those docs' bash blocks that
  passes ``--set-gh-secrets`` must get there too;
* the m2m-oauth-setup.md "Flags of note" table lists every flag the mint
  refusal names, and only flags the parser defines.

Nothing reaches a workspace or gh. The protection check's own flag tests run
for real; then a sentinel stops the run before the SDK client is built.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterator
from pathlib import Path

import pytest

from tools.databricks import m2m_oauth_cli
from tools.databricks import provision_m2m_oauth as pmo
from tools.databricks.m2m_identity_contract import AGENT_PROXY_CREDENTIAL_BUNDLE_SECRET

ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "docs" / "runbook.md"
DEPLOYMENT_DOC = ROOT / "docs" / "deployment.md"
M2M_SETUP_DOC = ROOT / "docs" / "security" / "m2m-oauth-setup.md"
MINT_DOCS = (RUNBOOK, DEPLOYMENT_DOC, M2M_SETUP_DOC)
PROVISIONER = "tools/databricks/provision_m2m_oauth.py"
CLIENT = "acme"
ROTATE = re.compile(r"(?<![\w-])--rotate(?![\w-])")
FLAG = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]*")
# The value an argv built from a prose instruction passes for each
# value-taking flag; a new one must be added here on purpose.
SYNTHETIC_VALUES = {
    "--expected-application-id": "00000000-0000-4000-8000-0000000000a1",
    "--gh-env": CLIENT,
    "--gh-repo": pmo.CANONICAL_GH_REPO,
}


class SinkGateReached(BaseException):
    """The run got past every flag check to the environment's protection read.

    A ``BaseException``, as ``SystemExit`` is: ``main`` turns any ``Exception``
    into exit code 1, which would hide whether the gate was reached.
    """

    def __init__(self, repo: str | None, environment: str | None) -> None:
        super().__init__(repo, environment)
        self.repo = repo
        self.environment = environment


@pytest.fixture(autouse=True)
def _offline_provisioner(monkeypatch: pytest.MonkeyPatch) -> None:
    """The origin is the canonical repository, the tree is clean, gh is never run."""

    real_require_mint_sink = pmo._github_helpers.require_mint_sink

    def reach_gate(repo: str | None, environment: str | None, **_kwargs: object) -> None:
        # The real flag and validator checks, with gh signed in and the
        # environment protected; then stop before any mutation.
        real_require_mint_sink(repo, environment, available=lambda: True)
        raise SinkGateReached(repo, environment)

    monkeypatch.setattr(pmo._github_helpers, "environment_refusals", lambda _repo, _env: [])
    monkeypatch.setattr(pmo._github_helpers, "require_mint_sink", reach_gate)
    monkeypatch.setattr(pmo, "_infer_gh_repo", lambda: pmo.CANONICAL_GH_REPO)
    monkeypatch.setattr(pmo, "_load_app_name_from_bundle", lambda *_args: "mip-app")
    monkeypatch.setattr(pmo._credential_mutation, "credential_source_git_sha", lambda _root: "a" * 40)
    monkeypatch.delenv("MIP_M2M_GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("MIP_APP_URL", raising=False)
    monkeypatch.delenv(AGENT_PROXY_CREDENTIAL_BUNDLE_SECRET, raising=False)
    for defaults in pmo.IDENTITY_DEFAULTS.values():
        monkeypatch.delenv(defaults.client_id_secret_name, raising=False)


def _run_until_gate(argv: list[str]) -> SinkGateReached:
    with pytest.raises(SinkGateReached) as reached:
        pmo.main(argv)
    return reached.value


def _paragraphs(path: Path) -> Iterator[str]:
    """Prose outside tables, plus comments inside code fences, one paragraph at a time."""

    lines: list[str] = []
    fenced = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("```"):
            fenced = not fenced
            line = ""
        elif fenced:
            line = line.lstrip("#").strip() if line.startswith("#") else ""
        elif line.startswith("|"):
            line = ""  # tables are checked as tables
        if line:
            lines.append(line)
        elif lines:
            yield " ".join(lines)
            lines = []
    if lines:
        yield " ".join(lines)


def rotation_instructions(path: Path) -> list[str]:
    """Each clause that tells the operator to rotate a credential into the sink."""

    found = []
    for paragraph in _paragraphs(path):
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z`(])", paragraph):
            for clause in sentence.split(";"):
                if ROTATE.search(clause) and "--set-gh-secrets" in clause:
                    found.append(clause.strip())
    return found


def _argv_for(instruction: str) -> list[str]:
    options = {
        option: action
        for action in m2m_oauth_cli.build_parser()._actions
        for option in action.option_strings
    }
    argv: list[str] = []
    for flag in dict.fromkeys(FLAG.findall(instruction)):
        assert flag in options, f"the instruction names {flag}, which the parser lacks: {instruction}"
        argv.append(flag)
        if options[flag].nargs != 0:
            assert flag in SYNTHETIC_VALUES, f"no synthetic value for {flag}: {instruction}"
            argv.append(SYNTHETIC_VALUES[flag])
    return argv


def _rotation_cases() -> list[tuple[str, str]]:
    return [
        (path.relative_to(ROOT).as_posix(), instruction)
        for path in MINT_DOCS
        for instruction in rotation_instructions(path)
    ]


def test_each_mint_doc_states_a_rotation_instruction() -> None:
    """The rotation check below is not vacuous: each doc has one to run."""

    for path in MINT_DOCS:
        assert rotation_instructions(path), f"{path.relative_to(ROOT)}: no rotation instruction found"


@pytest.mark.parametrize("role", sorted(pmo.IDENTITY_DEFAULTS))
@pytest.mark.parametrize(("doc", "instruction"), _rotation_cases())
def test_a_rotation_run_as_documented_reaches_the_environment_sink(
    doc: str, instruction: str, role: str
) -> None:
    """The flags a rotation instruction names get past the mint gate, into ``<client>``."""

    argv = ["--identity-role", role, *_argv_for(instruction)]
    assert "--rotate" in argv and "--set-gh-secrets" in argv, (doc, argv)

    reached = _run_until_gate(argv)

    assert (reached.repo, reached.environment) == (pmo.CANONICAL_GH_REPO, CLIENT), (doc, argv)


def test_the_gate_refuses_the_same_rotation_without_its_environment() -> None:
    """Control: dropping ``--gh-env`` from a documented rotation is refused before the gate."""

    _doc, instruction = _rotation_cases()[0]
    argv = ["--identity-role", "normal", *_argv_for(instruction)]
    if "--gh-env" in argv:
        index = argv.index("--gh-env")
        del argv[index : index + 2]

    with pytest.raises(SystemExit, match="--gh-env <client>"):
        pmo.main(argv)


def _bash_blocks(path: Path) -> Iterator[str]:
    """Each fenced ``bash`` block, indented fences (inside list items) included."""

    block: list[str] | None = None
    language = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("```"):
            if block is None:
                block, language = [], line[3:].strip()
            else:
                if language == "bash":
                    yield "\n".join(block)
                block = None
        elif block is not None:
            block.append(raw)


def documented_mint_commands(path: Path) -> list[list[str]]:
    """Each provisioner command with ``--set-gh-secrets``, its shell variables filled."""

    commands = []
    for block in _bash_blocks(path):
        variables = dict(re.findall(r"(?m)^([A-Z_][A-Z0-9_]*)=(\S+)", block))
        for line in block.replace("\\\n", " ").splitlines():
            if PROVISIONER not in line:
                continue
            tokens = shlex.split(line, comments=True)
            if tokens[:2] != ["python", PROVISIONER] or "--set-gh-secrets" not in tokens:
                continue
            filled = [
                variables.get(token[1:], token) if token.startswith("$") else token
                for token in tokens[2:]
            ]
            commands.append([token.replace("<client>", CLIENT) for token in filled])
    return commands


@pytest.mark.parametrize("path", MINT_DOCS, ids=lambda path: path.name)
def test_every_documented_mint_command_reaches_the_environment_sink(path: Path) -> None:
    """The seven bootstrap commands in each doc pass the gate with ``--gh-env <client>``."""

    commands = documented_mint_commands(path)
    roles = {command[command.index("--identity-role") + 1] for command in commands}
    assert roles == set(pmo.IDENTITY_DEFAULTS), f"{path.name}: one mint command per role"

    for argv in commands:
        reached = _run_until_gate(argv)
        assert (reached.repo, reached.environment) == (pmo.CANONICAL_GH_REPO, CLIENT), argv


def _flag_table() -> dict[str, str]:
    """``{flag: purpose}`` for each row of m2m-oauth-setup.md's "Flags of note" table."""

    section = M2M_SETUP_DOC.read_text(encoding="utf-8").split("Flags of note:", 1)[1]
    rows: dict[str, str] = {}
    for line in section.strip().splitlines():
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        for flag in FLAG.findall(cells[0]):
            rows[flag] = cells[-1]
    return rows


def test_the_flag_table_lists_every_flag_the_mint_refusal_names() -> None:
    """The refusal an operator sees names only flags the table explains."""

    with pytest.raises(SystemExit) as refused:
        pmo.main(["--identity-role", "normal", "--rotate", "--set-gh-secrets"])
    gate_flags = set(FLAG.findall(str(refused.value)))
    assert "--gh-env" in gate_flags, refused.value

    rows = _flag_table()
    assert {"--identity-role", "--rotate", "--dry-run"} <= set(rows), "the table was parsed"
    assert gate_flags <= set(rows), f"missing from the flag table: {sorted(gate_flags - set(rows))}"
    parser_flags = {
        option for action in m2m_oauth_cli.build_parser()._actions for option in action.option_strings
    }
    assert set(rows) <= parser_flags, f"not parser flags: {sorted(set(rows) - parser_flags)}"

    purpose = rows["--gh-env"]
    assert "Required with `--set-gh-secrets`" in purpose, purpose
    assert "repository-level" in purpose, purpose
    assert "`<client>-validate`" in purpose, purpose
