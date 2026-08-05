"""Strict env-file output for split agentic provisioning phases."""

from __future__ import annotations

import shlex
from pathlib import Path

from tools.databricks.agentic_resource_contract import ProvisionedResources


def _read_existing_values(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise RuntimeError("agentic env merge requires an existing env file")
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        name, separator, value = raw.partition("=")
        if (
            not separator
            or not name
            or not name.replace("_", "").isalnum()
            or name != name.upper()
            or name in values
            or "\x00" in value
        ):
            raise RuntimeError("existing agentic env file is malformed")
        values[name] = value
    return values


def _write_values(path: Path, values: dict[str, str]) -> None:
    text = "\n".join(f"{name}={value}" for name, value in values.items()) + "\n"
    path.write_text(text, encoding="utf-8")
    print(f"[agentic] wrote env file: {path}")


def merge_agentic_env_values(path: Path, updates: dict[str, str]) -> None:
    """Replace or append shell-safe values without creating duplicate keys."""

    values = _read_existing_values(path)
    for name, value in updates.items():
        if (
            not name
            or not name.replace("_", "").isalnum()
            or name != name.upper()
            or "\x00" in value
            or "\n" in value
        ):
            raise RuntimeError("generated agentic env update is malformed")
        values[name] = shlex.quote(value)
    _write_values(path, values)


def write_agentic_env(
    path: Path,
    resources: ProvisionedResources,
    *,
    merge: bool = False,
) -> None:
    values: dict[str, str] = {}
    if merge:
        try:
            values = _read_existing_values(path)
        except RuntimeError as exc:
            if not path.is_file():
                raise RuntimeError("--merge-out-env requires an existing env file") from exc
            raise
    for line in resources.env_lines():
        name, separator, value = line.partition("=")
        if not separator or not name or "\x00" in value:
            raise RuntimeError("generated agentic env line is malformed")
        values[name] = value
    _write_values(path, values)
