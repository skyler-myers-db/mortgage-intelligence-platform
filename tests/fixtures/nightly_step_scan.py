"""What a nightly.yml step runs besides its pytest suites and Playwright specs (M1-PR8, round 3).

``nightly_write_scan`` attributes a write path to the test that holds it; this
module finds the rest of what a step runs, so that the scan reads all of it.
It fails closed:

* every string the workflow holds (a ``run:``, an inline ``python - <<'PY'``
  script, a ``github-script`` body, an env value) is scanned for a write path;
* the ``tools`` scripts a step names by path, the ``tools`` modules it runs
  with ``-m``, the plugins a ``pytest -p`` names and the modules an inline
  Python script imports are loaded, and from there ``whole_module_findings``
  follows each file's own imports and scripts; a module that is no file is a
  finding;
* a command a step starts with ``python``, ``bash``, ``sh`` or ``node`` runs
  inline code (``-c``, ``-``, a here-document), a ``tools`` module, an
  installer or suite runner (``-m pip``, ``-m pytest``) or a repo file this
  scan then reads. Anything else is a finding: this scan cannot read it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tests.fixtures import nightly_write_scan as scan

# Where a command starts on a shell line, and the words that may come before its program.
_COMMAND_START = re.compile(r"&&|\|\||[;|(]|\$\(")
_COMMAND_PREFIX = re.compile(r"(?:sudo|exec|nohup|env|time|if|then|else|elif|do|while|until|!|timeout\s+\S+|[A-Za-z_]\w*=\S*)\s+")
_INTERPRETER = re.compile(r"(python3?|bash|sh|node)((?:\s+--?[A-Za-z][\w-]*)*)(?:\s+(\S+))?(?:\s|$)")
_WORKSPACE = re.compile(r"^(?:\./|\$GITHUB_WORKSPACE/|\$\{GITHUB_WORKSPACE\}/|\$\{\{\s*github\.workspace\s*\}\}/)")
_PLUGIN = re.compile(r"(?<![\w-])-p\s*((?:tests|tools)(?:\.\w+)+)")
# What a step may run with -m that is not repo code: the installer and the suite runner.
RUN_MODULES = frozenset({"pip", "pytest"})


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


def _command(root: Path, where: str, segment: str) -> tuple[list[str], list[Path]]:
    """(findings, the repo file it runs) for one command, when an interpreter starts it."""

    command = segment.strip()
    while (prefix := _COMMAND_PREFIX.match(command)) is not None:
        command = command[prefix.end() :]
    match = _INTERPRETER.match(command)
    if match is None:
        return [], []
    interpreter, flags, operand = match[1], match[2].split(), match[3]
    if "-c" in flags or operand is None or operand == "-" or operand.startswith("<"):
        return [], []  # inline code, in the text this scan reads
    operand = operand.rstrip(");").strip("'\"")
    if "-m" in flags:
        if operand in RUN_MODULES or operand.startswith("tools."):
            return [], []  # a tools module is loaded with the step's other modules
        return [f"{where}: {interpreter} -m {operand} runs a module this scan does not read"], []
    target = root / _WORKSPACE.sub("", operand)
    if target.is_file():
        return [], [target]
    return [f"{where}: {interpreter} runs {operand}, which this scan cannot read"], []


def step_findings(root: Path, where: str, run: str) -> tuple[list[str], list[Path]]:
    """(findings, the repo files the step runs) for one step's ``run:`` text."""

    findings: list[str] = []
    # A tools script the text names, run or passed on; one an interpreter starts must be a file (below).
    files = [root / name for name in scan.TOOL_PATH.findall(run) if (root / name).is_file()]
    for name in scan.TOOL_MODULE.findall(run) + _PLUGIN.findall(run):
        if (module := scan._module_path(root, name)) is not None:
            files.append(module)
        else:
            findings.append(f"{where}: runs {name}, which is no module")
    outside, documents = scan.split_heredocs(run)
    for opening, body in documents:
        modules = scan.heredoc_modules(root, body)
        if modules is None and re.search(r"\bpython3?\b", opening):
            findings.append(f"{where}: an inline Python script this scan cannot parse ({opening.strip()})")
        files.extend(sorted(modules or ()))
    for line in "\n".join(outside).replace("\\\n", " ").splitlines():
        for segment in _COMMAND_START.split(line):
            found, ran = _command(root, where, segment)
            findings += found
            files += ran
    return findings, files
