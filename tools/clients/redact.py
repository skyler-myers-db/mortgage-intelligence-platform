"""Public-log hygiene: runner masks for identifiers, handles for foreign names.

``emit_mask`` registers a value with the GitHub runner the moment it is
resolved, before any other use (plan §4.1): a runtime-derived warehouse,
Genie, App, deployment, workspace or metastore id, or a service principal's
application or SCIM id. It writes the ``::add-mask::`` command to stderr, so
a ``$(...)`` capture of the tool's stdout neither swallows the command nor
receives it as data, and it does nothing outside GitHub Actions.

``foreign`` replaces another team's object, owner or email with a stable
``foreign#n`` handle when ``MIP_REDACT_FOREIGN_NAMES=1`` (plan §4.16). It never
registers a runner mask: foreign names include common catalog names such as
``main`` or ``samples``, and a mask would blank every unrelated log line that
holds the same word. The handle-to-name mapping belongs in the workspace
PlanReport only, never in a public log.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from typing import TextIO

FOREIGN_REDACTION_ENV = "MIP_REDACT_FOREIGN_NAMES"


def in_github_actions(environ: Mapping[str, str] | None = None) -> bool:
    return (os.environ if environ is None else environ).get("GITHUB_ACTIONS") == "true"


def emit_mask(
    value: str,
    *,
    stream: TextIO | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Register ``value`` (each of its lines) as a runner mask; return it unchanged."""

    if value and in_github_actions(environ):
        out = sys.stderr if stream is None else stream
        for line in value.splitlines():
            if line.strip():
                out.write(f"::add-mask::{line}\n")
        out.flush()
    return value


def foreign_redaction_enabled(environ: Mapping[str, str] | None = None) -> bool:
    return (os.environ if environ is None else environ).get(FOREIGN_REDACTION_ENV) == "1"


class ForeignNames:
    """Stable ``foreign#n`` handles, numbered in first-seen order."""

    def __init__(self, *, enabled: bool | None = None) -> None:
        self._enabled = enabled
        self._handles: dict[str, str] = {}

    @property
    def enabled(self) -> bool:
        return foreign_redaction_enabled() if self._enabled is None else self._enabled

    def handle(self, name: str) -> str:
        """``name`` itself, or its handle when redaction is on."""

        if not self.enabled:
            return name
        if name not in self._handles:
            self._handles[name] = f"foreign#{len(self._handles) + 1}"
        return self._handles[name]

    def mapping(self) -> dict[str, str]:
        """Handle to name, for the workspace PlanReport only."""

        return {handle: name for name, handle in self._handles.items()}


_PROCESS_NAMES = ForeignNames()


def foreign(name: str) -> str:
    """The process-wide handle for another team's object, owner or email."""

    return _PROCESS_NAMES.handle(name)


def foreign_mapping() -> dict[str, str]:
    return _PROCESS_NAMES.mapping()
