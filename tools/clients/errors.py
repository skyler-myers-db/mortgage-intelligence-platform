"""Refusals that name fields and rules, never values."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from pydantic import ValidationError

_VALUE_ERROR_PREFIX = "Value error, "


class ProfileRefusal(ValueError):
    """A client profile was refused.

    Every line names a field and the rule it broke. None carries the refused
    value, so the lines are safe to print in a public CI log before any mask
    has been registered.
    """

    def __init__(self, lines: Sequence[str]) -> None:
        self.lines = tuple(lines)
        super().__init__("; ".join(self.lines))


class BootstrapRefusal(ProfileRefusal):
    """The account bootstrap refused (tools/clients/bootstrap_account.py).

    Same contract as a profile refusal: each line names an input, a role or
    a step and the rule it broke, never an identifier or a credential.
    """


def _location(loc: Iterable[object], error_type: str) -> str:
    parts = [str(part) for part in loc]
    if error_type == "extra_forbidden" and len(parts) > 1:
        # A JSON field's unexpected key may itself be an identifier: name its
        # parent and say a key is unexpected, never the key.
        parts[-1] = "<unexpected key>"
    return ".".join(parts)


def refusal_lines(error: ValidationError) -> list[str]:
    """One line per Pydantic error: its location and message, never its input."""

    lines: list[str] = []
    for item in error.errors(include_url=False, include_context=False, include_input=False):
        message = str(item["msg"])
        if message.startswith(_VALUE_ERROR_PREFIX):
            message = message[len(_VALUE_ERROR_PREFIX) :]
        location = _location(item["loc"], str(item["type"]))
        for line in message.splitlines() or [message]:
            if not line.strip():
                continue
            # Cross-field rules already lead with the field they name.
            lines.append(line if not location else f"{location}: {line}")
    return lines
