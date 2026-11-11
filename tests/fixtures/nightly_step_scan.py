"""What a nightly.yml step runs (M1-PR8, rounds 3 and 4).

``nightly_write_scan`` attributes a write path to the test that holds it; this
module finds what a step runs, so that the scan reads all of it. Every string
the workflow holds (a ``run:``, an inline script, a ``github-script`` body, an
env value) is scanned for a write path. A step's ``run:`` text is read in the
step's shell and directory (``shell`` and ``working-directory``, or the job's
or workflow's ``defaults.run``): under ``python`` it is a Python script, under
bash or sh it is split into simple commands (``shell_commands``), and under
any other shell it is a finding. A ``cd`` or ``pushd`` moves the commands
after it until its subshell ends; after one to a directory this module cannot
name, every path is a finding. Each command is read by its program's name
(``"$VENV/bin/python"`` is python), after assignments and the wrappers
``env``, ``sudo``, ``timeout``, ``nohup``, ``exec``, ``nice``, ``coverage run``
and ``uv run``:

* python (any ``python3.N``): inline code (``-c``, a here-document, a
  here-string) is parsed and its imports followed, and code that does not
  parse is a finding; ``-m`` runs pytest, a listed installer or stdlib
  utility, or a ``tools``/``tests`` module; a script must be a repo file.
* bash and sh: inline code (``-c``, a here-document, a here-string) is split
  in turn; a script must be a repo file.
* node: a script, and a ``--require``/``--import`` module given by path, must
  be a repo file; inline code, ``--test`` and ``--run`` are findings.
* pytest (or ``python -m pytest``): every operand must be a suite file under
  tests/integration; a directory, a module, ``@file`` or no operand at all
  (testpaths, ``-k`` or ``-m`` alone) is a finding. Its ``-p`` plugins load.
* ``playwright test`` (also through ``npx`` or ``npm exec``, as
  ``playwright@<version>`` or ``@playwright/test``) runs from frontend, and
  every file filter must end in ``.spec.ts`` and select a spec under
  tests/e2e (a filter is a regex on the spec's path, so it may select
  several); no filter at all is a finding.
* npm: the package.json scripts a command runs (``run``, ``test``, an
  install's lifecycle scripts unless ``--ignore-scripts``), with their
  ``pre``/``post`` scripts, are split in turn from the package's directory.
* a repo file run by path or sourced (``.``, ``source``), the text ``eval`` and
  ``trap`` run, and a program one program hands another after ``--``.

A program whose name is a variable (``"$pybin"``), a file outside the repo,
``make``, yarn, pnpm and bun, and text this module cannot split are findings;
an installed program in node_modules is read by its name. Each repo file a
step runs goes to ``nightly_write_scan.whole_module_findings``, which follows
its own imports and scripts. Not read: a program that builds its own command
line (``xargs``, ``find -exec``), an installed program's config file, and an
environment one step writes to ``$GITHUB_ENV`` for a later one.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from tests.fixtures import nightly_runners as runners
from tests.fixtures import nightly_write_scan as scan
from tests.fixtures import shell_commands as sh

_PYTHON = re.compile(r"python(?:3(?:\.\d+)?)?")
_SHELLS = frozenset({"bash", "sh", "dash"})
_WORKSPACE = re.compile(r"^(?:\$GITHUB_WORKSPACE|\$\{GITHUB_WORKSPACE\}|\$\{\{\s*github\.workspace\s*\}\})/")
_ASSIGNMENT = re.compile(r"[A-Za-z_]\w*\+?=.*", re.S)
_RESERVED = frozenset({"if", "then", "else", "elif", "fi", "do", "done", "while", "until", "!", "{", "}", "esac", "time"})
# Wrappers that run the rest of their words as a command, with their options that take a value.
_WRAPPERS: dict[str, frozenset[str]] = {
    "sudo": frozenset({"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T"}),
    "env": frozenset({"-u", "--unset", "-C", "--chdir"}),
    "timeout": frozenset({"-s", "--signal", "-k", "--kill-after"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "exec": frozenset({"-a"}),
    "nohup": frozenset(),
    "command": frozenset(),
    "builtin": frozenset(),
}
_COVERAGE_VALUE = frozenset({"--rcfile", "--source", "--omit", "--include", "--concurrency", "--context", "--data-file", "--debug", "--save-signal"})
_UV_VALUE = frozenset(
    {
        "--with", "--with-editable", "--with-requirements", "--python", "-p", "--project", "--directory", "--package",
        "--extra", "--group", "--only-group", "--no-group", "--env-file", "--index", "--default-index", "--index-url",
        "--extra-index-url", "--find-links", "-f",
    }
)  # fmt: skip
_PYTHON_VALUE = frozenset("WX")
_SHELL_VALUE = frozenset("oO")
_NODE_PRELOAD = frozenset({"-r", "--require", "--import", "--loader", "--experimental-loader"})
_NODE_INLINE = frozenset({"-e", "--eval", "-p", "--print"})
_NODE_VALUE = frozenset({"--env-file", "-C", "--conditions", "--input-type", "--title", "--test-reporter", "--test-reporter-destination"})
# Package managers other than npm (the repo pins npm): what they run is not read.
_OTHER_PACKAGE_MANAGERS = frozenset({"yarn", "pnpm", "pnpx", "bun", "bunx"})
# Programs a ``--`` may hand the rest of a command line to (``validation_session operators -- python -m ...``).
_HANDOFF = frozenset({"bash", "sh", "dash", "node", "pytest", "py.test", "npx", "npm", "playwright", "coverage", "uv"})
# What a step may run with -m that is not repo code: the installer, and stdlib utilities that run none.
RUN_MODULES = frozenset({"pip", "json.tool", "venv", "ensurepip", "site", "sysconfig", "platform"})
_PLUGIN = re.compile(r"(?<![\w-])-p\s*((?:tests|tools)(?:\.\w+)+)")
SUITE = re.compile(r"tests/integration/[a-z0-9_]+\.py")
# pytest matches a --deselect as a node-id prefix: ``./tests/...`` or a parameter id deselects no test listed here.
_DESELECT = re.compile(r"(tests/integration/[a-z0-9_]+\.py)::([A-Za-z_]\w*)")
_SPEC_FILTER = re.compile(r"([\w./-]+\.spec\.ts)(?::\d+){0,2}")


@dataclass(frozen=True)
class PytestRun:
    suites: tuple[str, ...]  # repo-relative suite files
    deselected: frozenset[tuple[str, str]]  # (suite, test)


@dataclass(frozen=True)
class PlaywrightRun:
    specs: tuple[str, ...]  # relative to frontend
    grep: str | None
    grep_invert: str | None


@dataclass
class StepScan:
    findings: list[str] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)  # repo files the step runs or loads
    pytest: list[PytestRun] = field(default_factory=list)
    playwright: list[PlaywrightRun] = field(default_factory=list)


@dataclass(frozen=True)
class _Code:
    kind: str  # inline | module | script | stdin | unread
    value: str = ""
    rest: tuple[str, ...] = ()
    preloads: tuple[str, ...] = ()


def workflow_string_findings(value: Any, where: str = "") -> list[str]:
    """A write path in any string the workflow holds, wherever it sits (a named step by its name)."""

    if isinstance(value, str):
        return [f"{where}: {match.group()} in the workflow text" for match in scan.LEAD_STATE_WRITE.finditer(value)]
    if isinstance(value, dict):
        return [finding for key, each in value.items() for finding in workflow_string_findings(each, f"{where}.{key}")]
    if isinstance(value, list):
        labels = [each.get("name", i) if isinstance(each, dict) else i for i, each in enumerate(value)]
        return [finding for label, each in zip(labels, value, strict=True) for finding in workflow_string_findings(each, f"{where}[{label}]")]
    return []


def step_context(workflow: dict[str, Any], job: dict[str, Any], step: dict[str, Any]) -> dict[str, str | None]:
    """The shell and directory a step's ``run:`` runs in: its own, else the job's, else the workflow's ``defaults.run``."""

    def default(key: str) -> str | None:
        for holder in (job, workflow):
            value = ((holder.get("defaults") or {}).get("run") or {}).get(key)
            if value is not None:
                return str(value)
        return None

    return {"shell": step.get("shell", default("shell")), "working_directory": step.get("working-directory", default("working-directory"))}


# ---------------------------------------------------------------- command lines


def _program(words: list[str]) -> list[str]:
    """``words`` from the program they run: assignments, reserved words and wrappers skipped."""

    i = 0
    while i < len(words):
        word, base = words[i], PurePosixPath(words[i]).name
        if _ASSIGNMENT.fullmatch(word) or word in _RESERVED:
            i += 1
        elif base in _WRAPPERS:
            i += 1
            while i < len(words) and (words[i].startswith("-") or (base == "env" and _ASSIGNMENT.fullmatch(words[i]))):
                i += 2 if words[i] in _WRAPPERS[base] else 1
            i += 1 if base == "timeout" else 0  # its duration
        else:
            break
    return words[i:]


def _skip_options(words: list[str], takes_value: frozenset[str], keep: str | None = None) -> list[str]:
    i = 0
    while i < len(words) and words[i].startswith("-") and words[i] != keep:
        if words[i] == "--":
            return words[i + 1 :]
        i += 2 if words[i] in takes_value else 1
    return words[i:]


def _python_args(words: list[str]) -> _Code:
    """CPython's command line: ``-c`` and ``-m`` take the next word (or the rest of their cluster)."""

    i = 0
    while i < len(words):
        word = words[i]
        i += 1
        if word == "-":
            return _Code("stdin")
        if word == "--":
            break
        if word.startswith("--"):
            i += 1 if word == "--check-hash-based-pycs" else 0
        elif word.startswith("-") and len(word) > 1:
            for k, letter in enumerate(word[1:], start=1):
                attached = word[k + 1 :]
                if letter in "cm":
                    value = attached or (words[i] if i < len(words) else "")
                    i += 0 if attached else 1
                    return _Code("inline" if letter == "c" else "module", value, tuple(words[i:]))
                if letter in _PYTHON_VALUE:
                    i += 0 if attached else 1
                    break
        else:
            return _Code("script", word, tuple(words[i:]))
    return _Code("script", words[i], tuple(words[i + 1 :])) if i < len(words) else _Code("stdin")


def _shell_args(words: list[str]) -> _Code:
    """bash's and sh's command line: ``-c`` makes the first operand the code, ``-s`` reads stdin."""

    inline = stdin = False
    i = 0
    while i < len(words):
        word = words[i]
        if word in ("-", "--"):
            i += 1
            break
        if word.startswith("--"):
            i += 2 if word in ("--rcfile", "--init-file") else 1
        elif word[0] in "-+" and len(word) > 1:
            inline = inline or (word[0] == "-" and "c" in word)
            stdin = stdin or (word[0] == "-" and "s" in word)
            i += 1 + sum(letter in _SHELL_VALUE for letter in word[1:])
        else:
            break
    operand = words[i] if i < len(words) else None
    if inline:
        return _Code("inline", operand or "")
    if operand is None or stdin:
        return _Code("stdin")
    return _Code("script", operand, tuple(words[i + 1 :]))


def _node_args(words: list[str]) -> _Code:
    """node's command line: ``-e``/``-p`` run inline code, ``-r``/``--import`` preload a module."""

    preloads: list[str] = []
    i = 0
    while i < len(words):
        word = words[i]
        i += 1
        name, sep, value = word.partition("=")
        if word == "-":
            return _Code("stdin", preloads=tuple(preloads))
        if word == "--":
            break
        if name in _NODE_INLINE:
            return _Code("inline", value if sep else (words[i] if i < len(words) else ""))
        if name in ("--test", "--run"):
            return _Code("unread", name)
        if name in _NODE_PRELOAD | _NODE_VALUE:
            if not sep:
                value, i = (words[i] if i < len(words) else ""), i + 1
            if name in _NODE_PRELOAD:
                preloads.append(value)
        elif not word.startswith("-"):
            return _Code("script", word, tuple(words[i:]), tuple(preloads))
    if i < len(words):
        return _Code("script", words[i], tuple(words[i + 1 :]), tuple(preloads))
    return _Code("stdin", preloads=tuple(preloads))


def _npx_program(package: str) -> str:
    """The program ``npx <package>`` runs: ``playwright@1.50`` and ``@playwright/test`` run playwright."""

    name = package[: package.rfind("@")] if package.rfind("@") > 0 else package
    return "playwright" if name == "@playwright/test" else name


def _is_interpreter(base: str) -> bool:
    return bool(_PYTHON.fullmatch(base)) or base in _SHELLS or base == "node"


def _interpreter_code(base: str, words: list[str]) -> _Code:
    if _PYTHON.fullmatch(base):
        return _python_args(words)
    return _shell_args(words) if base in _SHELLS else _node_args(words)


def _reads_stdin(words: list[str]) -> bool:
    base = PurePosixPath(words[0]).name
    return _is_interpreter(base) and _interpreter_code(base, words[1:]).kind == "stdin"


# ---------------------------------------------------------------- the scan


class _Scan:
    def __init__(self, root: Path, where: str) -> None:
        self.root = Path(os.path.normpath(root))
        self.where = where
        self.out = StepScan()
        self.npm_seen: set[tuple[Path, str, tuple[str, ...]]] = set()

    def finding(self, text: str) -> None:
        self.out.findings.append(f"{self.where}: {text}")

    # ---------------------------------------------------------------- paths

    def directory(self, wd: str | None) -> Path | None:
        if wd is None:
            return self.root
        if "$" in _WORKSPACE.sub("", wd):
            return None
        path = Path(os.path.normpath(self.root / _WORKSPACE.sub("", wd)))
        return path if path.is_relative_to(self.root) else None

    def resolve(self, operand: str, wd: str | None) -> Path | None:
        """The repo path ``operand`` names from ``wd``; None for an expansion, a path outside the repo or in node_modules."""

        relative = _WORKSPACE.sub("", operand)
        base = self.root if relative != operand else self.directory(wd)
        if base is None or "$" in relative or relative.startswith(("/", "~")):
            return None
        path = Path(os.path.normpath(base / relative))
        if not path.is_relative_to(self.root) or "node_modules" in path.relative_to(self.root).parts:
            return None
        return path

    def change_directory(self, wd: str | None, words: list[str]) -> str:
        """The directory after ``cd``/``pushd`` (repo-relative), or an unknown one every path then fails to resolve in."""

        operands = [word for word in words[1:] if not word.startswith("-")]
        target = operands[0] if words[0] != "popd" and operands else None
        path = self.resolve(target, wd) if target is not None else None
        return path.relative_to(self.root).as_posix() if path is not None else "$UNKNOWN_DIRECTORY"

    def run_file(self, program: str, operand: str, wd: str | None) -> None:
        path = self.resolve(operand, wd)
        if path is not None and path.is_file():
            self.out.files.append(path)
        else:
            self.finding(f"{program} runs {operand}, which this scan cannot read")

    # ---------------------------------------------------------------- code

    def python_code(self, code: str, what: str) -> None:
        modules = scan.heredoc_modules(self.root, code)
        if modules is None:
            self.finding(f"{what}: inline Python this scan cannot parse")
        else:
            self.out.files.extend(sorted(modules))

    def shell_text(self, text: str, wd: str | None) -> None:
        outside, documents = scan.split_heredocs(text)
        try:
            commands = sh.commands("\n".join(outside))
        except sh.ShellSyntaxError as error:
            self.finding(f"shell text this scan cannot split ({error})")
            return
        fed = False  # a here-document feeds this pipeline
        # The directory each subshell moved to: a ``cd`` holds until its own subshell ends, and a
        # command runs where its innermost subshell, or the one around that, last moved.
        directories: dict[tuple[int, ...], str | None] = {(0,): wd}
        opened_in: list[str | None] = []
        here = wd
        for command in commands:
            fed = command.heredoc or (fed and command.piped_in)
            if not command.substituted:  # a $( ) command runs where the command that holds it does
                here = next(directories[command.scope[:k]] for k in range(len(command.scope), 0, -1) if command.scope[:k] in directories)
            words = _program(command.words)
            if words[:1] in (["cd"], ["pushd"], ["popd"]) and not command.substituted:
                directories[command.scope] = self.change_directory(here, words)
            if command.heredoc:
                opened_in.append(here)
            self.command(command, here, fed)
        # Each here-document runs where its opening line does (one per line, in order); else where the step starts.
        for (opening, body), here in zip(documents, opened_in if len(opened_in) == len(documents) else [wd] * len(documents), strict=True):
            self.document(opening, body, here)

    def document(self, opening: str, body: str, wd: str | None) -> None:
        """A here-document is the code of the interpreter that reads it, through a pipe too; else it is data."""

        try:
            line = sh.commands(opening)
        except sh.ShellSyntaxError as error:
            self.finding(f"shell text this scan cannot split ({error})")
            return
        owner = next((k for k, command in enumerate(line) if command.heredoc), len(line))
        reader = None
        for command in line[owner:]:
            words = _program(command.words)
            if words and _reads_stdin(words):
                reader = PurePosixPath(words[0]).name
                break
            if not command.pipes_out:
                break
        if reader is not None and _PYTHON.fullmatch(reader):
            self.python_code(body, f"a here-document ({opening.strip()})")
        elif reader in _SHELLS:
            self.shell_text(body, wd)
        elif reader is not None:
            self.finding(f"{reader} reads a here-document this scan cannot read ({opening.strip()})")
        else:
            self.out.files.extend(sorted(scan.heredoc_modules(self.root, body) or ()))  # data: an import it names loads anyway

    # ---------------------------------------------------------------- commands

    def command(self, command: sh.Command, wd: str | None, fed: bool) -> None:
        words = _program(command.words)
        if not words:
            return
        program, base = words[0], PurePosixPath(words[0]).name
        if "$" in base:
            self.finding(f"runs {program}, which this scan cannot resolve")
        elif _is_interpreter(base):
            self.interpreter(command, base, words[1:], wd, fed)
        elif base in ("pytest", "py.test"):
            self.pytest(words[1:], wd)
        elif base == "playwright" and words[1:2] == ["test"]:
            self.playwright(words[2:], wd)
        elif base in ("npm", "npx"):
            self.npm(words[1:] if base == "npm" else ["exec", *words[1:]], wd)
        elif base in _OTHER_PACKAGE_MANAGERS:
            self.finding(f"{base} runs package scripts or programs this scan does not read")
        elif base == "coverage" and words[1:2] == ["run"]:
            rest = _skip_options(words[2:], _COVERAGE_VALUE, keep="-m")
            if rest[:1] == ["-m"]:
                self.module("coverage run", rest[1] if len(rest) > 1 else "", rest[2:], wd)
            else:
                self.run_file("coverage run", rest[0] if rest else "", wd)
        elif base == "uv" and words[1:2] == ["run"]:
            rest = _skip_options(words[2:], _UV_VALUE)
            self.command(sh.Command(rest, command.stdin, command.herestring, command.heredoc, command.piped_in), wd, fed)
        elif base in (".", "source") and len(words) > 1:
            self.run_file(base, words[1], wd)
        elif base in ("eval", "trap") and len(words) > 1:
            self.shell_text(" ".join(words[1:2] if base == "trap" else words[1:]), wd)
        elif base == "make":
            self.finding("make runs recipes this scan does not read")
        elif "/" in program and "node_modules" not in PurePosixPath(program).parts:
            self.run_file("the step", program, wd)  # an installed program in node_modules is read by name, above
        if base not in ("npm", "npx", "pytest", "py.test", "playwright") and "--" in words[1:]:
            rest = words[words.index("--", 1) + 1 :]
            if rest and (PurePosixPath(rest[0]).name in _HANDOFF or _PYTHON.fullmatch(PurePosixPath(rest[0]).name)):
                self.command(sh.Command(rest), wd, False)

    def interpreter(self, command: sh.Command, base: str, words: list[str], wd: str | None, fed: bool) -> None:
        code = _interpreter_code(base, words)
        if code.kind == "inline" and _PYTHON.fullmatch(base):
            self.python_code(code.value, f"{base} -c")
        elif code.kind == "inline" and base in _SHELLS:
            self.shell_text(code.value, wd)
        elif code.kind in ("inline", "unread"):
            self.finding(f"{base} {code.value if code.kind == 'unread' else '-e'} runs code this scan cannot read")
        elif code.kind == "module":
            self.module(base, code.value, list(code.rest), wd)
        elif code.kind == "script":
            self.run_file(base, code.value, wd)
        elif command.herestring is not None and _PYTHON.fullmatch(base):
            self.python_code(command.herestring, f"{base} <<<")
        elif command.herestring is not None and base in _SHELLS:
            self.shell_text(command.herestring, wd)
        elif command.herestring is not None:
            self.finding(f"{base} <<< runs code this scan cannot read")
        elif command.stdin is not None:
            self.run_file(base, command.stdin, wd)
        elif command.piped_in and not fed:
            self.finding(f"{base} runs code from a pipe this scan cannot read")
        for preload in code.preloads:
            if preload.startswith((".", "/", "$")):
                self.run_file(base, preload, wd)

    def module(self, program: str, name: str, rest: list[str], wd: str | None) -> None:
        if name == "pytest":
            self.pytest(rest, wd)
        elif name.startswith(("tools.", "tests.")) and (path := scan._module_path(self.root, name)) is not None:
            self.out.files.append(path)
        elif name.startswith(("tools.", "tests.")):
            self.finding(f"{program} -m {name} runs {name}, which is no module")
        elif name not in RUN_MODULES:
            self.finding(f"{program} -m {name} runs a module this scan does not read")

    # ---------------------------------------------------------------- runners

    def pytest(self, words: list[str], wd: str | None) -> None:
        parsed = runners.pytest_args(words)
        for name, value in parsed.values:
            if name == "-p" and not value.startswith("no:"):
                if (module := scan._module_path(self.root, value)) is not None:
                    self.out.files.append(module)
                elif value.startswith(("tests.", "tools.")):
                    self.finding(f"pytest -p {value} loads {value}, which is no module")
        suites: list[str] = []
        for operand in parsed.operands:
            path = self.resolve(operand.partition("::")[0], wd)
            relative = path.relative_to(self.root).as_posix() if path is not None else ""
            if path is not None and path.is_file() and SUITE.fullmatch(relative):
                suites.append(relative)
            else:
                self.finding(f"pytest selects {operand}, which is no suite file under tests/integration this scan reads")
        if not parsed.operands:
            self.finding("pytest names no suite file (testpaths, -k or -m alone select what this scan does not read)")
        deselected = {(match[1], match[2]) for name, value in parsed.values if name == "--deselect" and (match := _DESELECT.fullmatch(value))}
        self.out.pytest.append(PytestRun(tuple(suites), frozenset(deselected)))

    def playwright(self, words: list[str], wd: str | None) -> None:
        frontend = self.root / "frontend"
        if self.directory(wd) != frontend:
            self.finding("playwright test runs outside frontend, whose config and specs this scan reads")
            return
        parsed = runners.playwright_args(words)
        candidates = sorted(path.relative_to(frontend).as_posix() for path in (frontend / "tests" / "e2e").rglob("*.spec.ts"))
        specs: list[str] = []
        for operand in parsed.operands:
            match = _SPEC_FILTER.fullmatch(operand)
            selected = [spec for spec in candidates if match and re.search(match[1], f"/frontend/{spec}", re.IGNORECASE)]
            if not selected:
                self.finding(f"playwright test selects {operand}, which is no spec filter this scan reads")
            specs += [spec for spec in selected if spec not in specs]
        if not parsed.operands:
            self.finding("playwright test names no spec (it runs every spec under testDir)")
        self.out.playwright.append(PlaywrightRun(tuple(specs), runners.last(parsed, "--grep"), runners.last(parsed, "--grep-invert")))

    def npm(self, words: list[str], wd: str | None) -> None:
        parsed = runners.npm_args(words)
        for name in parsed.unfollowed:
            self.finding(f"npm {name} runs scripts this scan does not follow")
        if parsed.command in runners.NPM_EXEC:
            if parsed.call is not None:
                self.shell_text(parsed.call, wd)
            elif parsed.script is not None:
                self.command(sh.Command([_npx_program(parsed.script), *parsed.args]), wd, False)
            return
        if parsed.command is None or parsed.command in runners.NPM_NO_SCRIPTS or (parsed.command in runners.NPM_RUN and parsed.script is None):
            return
        if parsed.command not in runners.NPM_SCRIPTS and parsed.command not in runners.NPM_RUN:
            self.finding(f"npm {parsed.command} is no npm command this scan knows")
            return
        package = self.resolve(parsed.prefix, wd) if parsed.prefix is not None else self.directory(wd)
        if package is None or not (package / "package.json").is_file():
            self.finding(f"npm runs a package this scan cannot read ({parsed.prefix or wd or '.'})")
            return
        # The script a command names (run's, test's, start's) takes its arguments, and still runs under
        # --ignore-scripts, which drops only the pre/post and lifecycle scripts.
        main = parsed.script if parsed.command in runners.NPM_RUN else runners.NPM_MAIN.get(parsed.command)
        names = (f"pre{main}", main, f"post{main}") if parsed.command in runners.NPM_RUN else runners.NPM_SCRIPTS[parsed.command]
        scripts = json.loads((package / "package.json").read_text(encoding="utf-8")).get("scripts") or {}
        for name in names:
            args = parsed.args if name == main else ()
            if name not in scripts or (parsed.ignore_scripts and name != main) or (package, name, args) in self.npm_seen:
                continue
            self.npm_seen.add((package, name, args))  # a script that runs itself runs once here
            text = " ".join([scripts[name], *(shlex.quote(arg) for arg in args)])
            self.shell_text(text, package.relative_to(self.root).as_posix())


def step_findings(root: Path, where: str, run: str, *, shell: str | None = None, working_directory: str | None = None) -> StepScan:
    """What one step's ``run:`` text runs: its findings, the repo files it runs, its pytest and Playwright runs."""

    step = _Scan(root, where)
    # A tools script, tools module or pytest plugin the text names anywhere (an argument one program
    # passes another, a stale comment) loads when it exists; one a command runs must exist (above).
    step.out.files += [step.root / name for name in scan.TOOL_PATH.findall(run) if (step.root / name).is_file()]
    step.out.files += [module for name in scan.TOOL_MODULE.findall(run) + _PLUGIN.findall(run) if (module := scan._module_path(step.root, name)) is not None]
    program = PurePosixPath((shell or "bash").split()[0]).name
    if _PYTHON.fullmatch(program):
        step.python_code(run, f"a shell: {shell} step")
    elif program in _SHELLS:
        step.shell_text(run, working_directory)
    else:
        step.finding(f"runs under shell {shell}, which this scan cannot read")
    return step.out
