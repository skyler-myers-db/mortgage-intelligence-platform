"""The argument grammars of pytest, Playwright and npm, for the nightly step scan (M1-PR8, round 4).

Each parser reads a command line the way the program does, with one rule
that keeps the scan fail closed: an option this module does not know takes
no value, so a value it does take reads as an operand, which the scan then
refuses because it is no suite or spec file.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

# pytest 9 with the plugins CI installs (xdist, cov, hypothesis), read from its own parser:
# argparse with allow_abbrev=False, so a long option is never abbreviated.
PYTEST_VALUE = frozenset(
    {
        "--assert", "--basetemp", "--capture", "--code-highlight", "--color", "--confcutdir", "--config-file",
        "--cov-config", "--cov-context", "--cov-fail-under", "--cov-precision", "--cov-report", "--deselect",
        "--dist", "--doctest-glob", "--doctest-report", "--durations", "--durations-min", "--hypothesis-profile",
        "--hypothesis-seed", "--hypothesis-verbosity", "--ignore", "--ignore-glob", "--import-mode",
        "--junit-prefix", "--junit-xml", "--junitprefix", "--junitxml", "--last-failed-no-failures", "--lfnf",
        "--log-auto-indent", "--log-cli-date-format", "--log-cli-format", "--log-cli-level", "--log-date-format",
        "--log-disable", "--log-file", "--log-file-date-format", "--log-file-format", "--log-file-level",
        "--log-file-mode", "--log-format", "--log-level", "--max-worker-restart", "--maxfail", "--maxprocesses",
        "--maxschedchunk", "--numprocesses", "--override-ini", "--pastebin", "--pdbcls", "--px", "--pythonwarnings",
        "--rootdir", "--rsyncdir", "--rsyncignore", "--show-capture", "--tb", "--testrunuid", "--tx", "--verbosity",
    }
)  # fmt: skip
PYTEST_OPTIONAL_VALUE = frozenset({"--cache-show", "--cov", "--debug"})
PYTEST_SHORT_VALUE = frozenset("Wckmnopr")

# Playwright's `test` command (commander): --project is variadic, -u and --only-changed take an optional value.
PLAYWRIGHT_VALUE = frozenset(
    {
        "--browser", "--config", "--global-timeout", "--grep", "--grep-invert", "--max-failures", "--output",
        "--repeat-each", "--reporter", "--retries", "--shard", "--test-list", "--test-list-invert", "--timeout",
        "--trace", "--tsconfig", "--ui-host", "--ui-port", "--update-source-method", "--workers",
    }
)  # fmt: skip
PLAYWRIGHT_OPTIONAL_VALUE = frozenset({"--only-changed", "--update-snapshots"})
PLAYWRIGHT_VARIADIC = frozenset({"--project"})
PLAYWRIGHT_SHORT = {"c": "--config", "g": "--grep", "j": "--workers", "u": "--update-snapshots"}


@dataclass(frozen=True)
class Parsed:
    operands: tuple[str, ...]
    values: tuple[tuple[str, str], ...]  # (option, value), in order


def _long(word: str) -> tuple[str, str | None]:
    name, equals, value = word.partition("=")
    return name, value if equals else None


def _value_follows(name: str, words: Sequence[str], i: int, required: frozenset[str], optional: frozenset[str]) -> bool:
    """The next word is ``name``'s value: it always takes one, or takes one that is no option."""

    return i < len(words) and (name in required or (name in optional and not words[i].startswith("-")))


def pytest_args(words: Sequence[str]) -> Parsed:
    """pytest's operands and option values, read the way its argparse parser reads them."""

    operands: list[str] = []
    values: list[tuple[str, str]] = []
    i = 0
    while i < len(words):
        word = words[i]
        i += 1
        if word == "--":
            operands += words[i:]
            break
        if word.startswith("--"):
            name, value = _long(word)
            if value is None and _value_follows(name, words, i, PYTEST_VALUE, PYTEST_OPTIONAL_VALUE):
                value, i = words[i], i + 1
            if value is not None:
                values.append((name, value))
        elif word.startswith("-") and len(word) > 1:
            for k, letter in enumerate(word[1:], start=1):
                if letter in PYTEST_SHORT_VALUE:
                    value = word[k + 1 :]
                    if not value and i < len(words):
                        value, i = words[i], i + 1
                    values.append((f"-{letter}", value))
                    break
        else:
            operands.append(word)
    return Parsed(tuple(operands), tuple(values))


def playwright_args(words: Sequence[str]) -> Parsed:
    """``playwright test``'s file filters and option values, read the way commander reads them."""

    operands: list[str] = []
    values: list[tuple[str, str]] = []
    i = 0
    while i < len(words):
        word = words[i]
        i += 1
        if word == "--":
            operands += words[i:]
            break
        if word.startswith("--"):
            name, value = _long(word)
        elif word.startswith("-") and len(word) > 1 and word[1] in PLAYWRIGHT_SHORT:
            name, value = PLAYWRIGHT_SHORT[word[1]], word[2:] or None
        elif word.startswith("-") and len(word) > 1:
            continue
        else:
            operands.append(word)
            continue
        if value is None and _value_follows(name, words, i, PLAYWRIGHT_VALUE, PLAYWRIGHT_OPTIONAL_VALUE):
            value, i = words[i], i + 1
        elif value is None and name in PLAYWRIGHT_VARIADIC:
            while i < len(words) and not words[i].startswith("-"):
                values.append((name, words[i]))
                i += 1
        if value is not None:
            values.append((name, value))
    return Parsed(tuple(operands), tuple(values))


def last(parsed: Parsed, *names: str) -> str | None:
    """The value of the last of ``names`` given (a repeated option keeps its last value)."""

    found = [value for name, value in parsed.values if name in names]
    return found[-1] if found else None


# ---------------------------------------------------------------- npm

NPM_VALUE = frozenset({"--prefix", "-C", "--loglevel", "--registry", "--userconfig", "--globalconfig", "--cache", "--tag", "--otp"})
# Options that send scripts somewhere this scan does not follow.
NPM_UNFOLLOWED = frozenset({"--workspace", "-w", "--workspaces", "-ws", "--script-shell", "--include-workspace-root"})
_INSTALL = ("preinstall", "install", "postinstall", "prepublish", "preprepare", "prepare", "postprepare")
# Each npm command (and alias) that runs the package's own scripts, with the scripts it runs.
NPM_SCRIPTS: dict[str, tuple[str, ...]] = {
    **dict.fromkeys(("test", "t", "tst"), ("pretest", "test", "posttest")),
    **dict.fromkeys(("start",), ("prestart", "start", "poststart")),
    **dict.fromkeys(("stop",), ("prestop", "stop", "poststop")),
    **dict.fromkeys(("restart",), ("prerestart", "restart", "stop", "start", "postrestart")),
    **dict.fromkeys(
        ("ci", "clean-install", "ic", "install-clean", "isntall-clean", "install", "i", "add", "in", "ins", "inst", "insta", "instal", "isnt", "isnta", "isntal", "isntall", "rebuild", "rb", "link", "ln", "update", "up", "upgrade", "udpate", "dedupe", "ddp", "uninstall", "unlink", "remove", "rm", "r", "un"),
        _INSTALL,
    ),
    **dict.fromkeys(("install-test", "it", "install-ci-test", "cit", "clean-install-test", "sit"), (*_INSTALL, "pretest", "test", "posttest")),
    **dict.fromkeys(("pack", "publish"), ("prepack", "prepare", "postpack", "prepublishOnly", "publish", "postpublish")),
    **dict.fromkeys(("version", "verison"), ("preversion", "version", "postversion")),
}  # fmt: skip
NPM_RUN = frozenset({"run", "run-script", "rum", "urn"})
# The script each of those commands names: it takes the arguments, and --ignore-scripts keeps it.
NPM_MAIN = {**dict.fromkeys(("test", "t", "tst"), "test"), "start": "start", "stop": "stop", "restart": "restart"}
NPM_EXEC = frozenset({"exec", "x"})
# npm commands that run none of the package's scripts.
NPM_NO_SCRIPTS = frozenset(
    {
        "access", "adduser", "audit", "bugs", "cache", "completion", "config", "c", "deprecate", "diff", "dist-tag",
        "docs", "doctor", "edit", "explain", "find-dupes", "fund", "get", "help", "help-search", "hook", "init",
        "login", "logout", "ls", "list", "ll", "la", "org", "outdated", "owner", "ping", "pkg", "prefix", "profile",
        "prune", "query", "repo", "root", "sbom", "search", "set", "shrinkwrap", "star", "stars", "team", "token",
        "unpublish", "unstar", "view", "whoami",
    }
)  # fmt: skip


# npm exec's (npx's) own options that take a value: the package to install, a command line to run.
NPM_EXEC_VALUE = frozenset({"--package", "-p", "--call", "-c"})


@dataclass(frozen=True)
class NpmCommand:
    prefix: str | None  # --prefix / -C, relative to the step's directory
    command: str | None  # the npm command, None for none (npm --version)
    script: str | None  # run's script name, exec's program
    args: tuple[str, ...]  # what npm passes on to a script, or exec's program
    call: str | None  # exec --call / -c: a shell command line
    ignore_scripts: bool
    unfollowed: tuple[str, ...]  # options this scan does not follow


def npm_args(words: Sequence[str]) -> NpmCommand:
    """npm's command, script and arguments (the words after ``npm``; npx is ``npm exec``)."""

    prefix: str | None = None
    command: str | None = None
    script: str | None = None
    call: str | None = None
    args: list[str] = []
    ignore_scripts = False
    unfollowed: list[str] = []
    i = 0
    while i < len(words):
        word = words[i]
        i += 1
        if command in NPM_EXEC and script is not None:
            args.append(word)  # the program's own command line, options and all
            continue
        if word == "--":
            if command in NPM_EXEC and script is None and i < len(words):
                script, i = words[i], i + 1
            args += words[i:]
            break
        if word.startswith("-") and len(word) > 1:
            name, value = _long(word)
            takes = NPM_VALUE | (NPM_EXEC_VALUE if command in NPM_EXEC else frozenset())
            if value is None and name in takes and i < len(words):
                value, i = words[i], i + 1
            if name in ("--prefix", "-C"):
                prefix = value
            elif name in ("--call", "-c") and command in NPM_EXEC:
                call = value
            ignore_scripts = ignore_scripts or (name == "--ignore-scripts" and value in (None, "true"))
            if name in NPM_UNFOLLOWED:
                unfollowed.append(name)
        elif command is None:
            command = word
        elif command in NPM_RUN | NPM_EXEC and script is None:
            script = word
        else:
            args.append(word)
    return NpmCommand(prefix, command, script, tuple(args), call, ignore_scripts, tuple(unfollowed))
