"""Refuse a job run whose parameters are not the bundle's own (M2-PR5, plan section 4.3).

The App holds CAN_MANAGE_RUN on four bundle jobs, and ``run_now`` lets the
caller replace a Python task's parameters. A compromised App could therefore
point a job at another catalog or Lakebase instance, or turn on an operator
flag. Every App-triggerable entrypoint calls :func:`enforce` first, before any
SQL statement, Spark session or Lakebase connection, and it refuses (exit 5)
unless:

* each pinned option equals this install's pin, read from
  ``install_pins.json`` in this module's own folder. tools/databricks/bundle_env.py
  renders that file from the client profile before every bundle call, and the
  bundle syncs it into the deployer-owned bundle root beside this module, so
  the App cannot write it;
* every other option keeps its parser default, except the ones the bundle
  itself passes with a free value (``--mode`` of the FRED job, say).

A missing, unreadable or malformed pins file refuses too: an unproven run is
never a run. Refusal lines name options and fields, never a value.

Stdlib only: the serverless job environments add no package for this.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path
from typing import NamedTuple, NoReturn

PINS_FILE = Path(__file__).resolve().parent / "install_pins.json"
PINS_VERSION = 1
EXIT_REFUSED = 5
PREFIX = "[install-pins]"
_FIELDS = ("version", "catalog", "lakebase_instance", "lakebase_database", "data_owner_group")
# The shapes the client profile validates (tools/clients/shapes.py), restated
# here so a job needs no repository import.
_SHAPES: Mapping[str, re.Pattern[str]] = {
    "catalog": re.compile(r"[a-z_][a-z0-9_]{0,254}"),
    "lakebase_instance": re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"),
    "lakebase_database": re.compile(r"[a-z_][a-z0-9_]{0,254}"),
    "data_owner_group": re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9 _.-]{0,254}[A-Za-z0-9])?"),
}


class InstallPinsRefused(RuntimeError):
    """The run is not one the bundle defined; ``lines`` say why, value-free."""

    def __init__(self, lines: Sequence[str]) -> None:
        super().__init__("; ".join(lines))
        self.lines = tuple(lines)


class InstallPins(NamedTuple):
    # A NamedTuple, not a dataclass: entrypoints load this module by path
    # without registering it in sys.modules, which a dataclass would need.
    catalog: str
    lakebase_instance: str
    lakebase_database: str
    data_owner_group: str


def load(path: Path | None = None) -> InstallPins:
    """The pins file, exactly the reviewed shape, or ``InstallPinsRefused``."""

    source = path or PINS_FILE
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise InstallPinsRefused([f"{source.name} is missing beside the job sources"]) from None
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise InstallPinsRefused([f"{source.name} is unreadable ({type(exc).__name__})"]) from None
    if not isinstance(document, dict) or set(document) != set(_FIELDS):
        raise InstallPinsRefused([f"{source.name} must hold exactly the fields {', '.join(_FIELDS)}"])
    if isinstance(document["version"], bool) or document["version"] != PINS_VERSION:
        raise InstallPinsRefused([f"{source.name} version must be {PINS_VERSION}"])
    bad = [
        name
        for name, shape in _SHAPES.items()
        if not isinstance(document[name], str) or shape.fullmatch(document[name]) is None
    ]
    if bad:
        raise InstallPinsRefused([f"{source.name} field {name} is malformed" for name in bad])
    return InstallPins(**{name: document[name] for name in _SHAPES})


def check(
    parser: argparse.ArgumentParser,
    argv: Sequence[str] | None,
    *,
    expected: Callable[[InstallPins], Mapping[str, str]],
    free: Collection[str] = (),
    path: Path | None = None,
) -> InstallPins:
    """Parse ``argv`` and judge it against the pins; ``InstallPinsRefused`` on any mismatch.

    ``expected(pins)`` maps an argparse dest to the value the install pins;
    ``free`` names the dests the bundle passes with a free value. Every other
    option must equal its parser default. Parsing comes first, so argparse's
    own refusals (exit 2) also precede any SQL.
    """

    args = vars(parser.parse_args(argv))
    pins = load(path)
    pinned = dict(expected(pins))
    lines: list[str] = []
    for dest, value in sorted(args.items()):
        option = "--" + dest.replace("_", "-")
        if dest in pinned:
            if value != pinned[dest]:
                lines.append(f"{option} differs from this install's pin")
        elif dest not in free and value != parser.get_default(dest):
            lines.append(f"{option} is not a parameter the bundle passes")
    unknown = sorted(set(pinned) - set(args))
    if unknown:
        lines.append(f"the pin map names unknown option(s) {', '.join(unknown)}")
    if lines:
        raise InstallPinsRefused(lines)
    return pins


def refuse(prog: str, refusal: InstallPinsRefused) -> NoReturn:
    for line in refusal.lines:
        print(f"{PREFIX} {prog} REFUSED: {line}", file=sys.stderr)
    print(
        f"{PREFIX} {prog} ran no SQL and opened no connection; a run of this job takes the "
        "bundle's own parameters only (jobs/_install_pins.py)",
        file=sys.stderr,
    )
    sys.exit(EXIT_REFUSED)


def enforce(
    parser: argparse.ArgumentParser,
    argv: Sequence[str] | None,
    *,
    expected: Callable[[InstallPins], Mapping[str, str]],
    free: Collection[str] = (),
    path: Path | None = None,
) -> InstallPins:
    """:func:`check`, exiting 5 with value-free lines on a refusal."""

    try:
        return check(parser, argv, expected=expected, free=free, path=path)
    except InstallPinsRefused as refusal:
        refuse(parser.prog, refusal)

