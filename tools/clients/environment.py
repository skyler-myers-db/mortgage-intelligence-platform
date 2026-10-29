"""Resolve a client profile from GitHub contexts or from the local environment.

**CI.** ``--vars-json`` carries ``toJSON(vars)`` and ``--secrets-json``
``toJSON(secrets)``, each from one step's env (``env:NAME``) or a file. They
are the only inputs; the ambient environment and ``.env.local`` are not read.

* An identifying or secret field (or alias) found in ``vars`` is refused: a
  variable prints in clear in step env and in the run log.
* A public field found in ``secrets`` is refused: the runner would mask its
  value, and a masked catalog or scope name blanks unrelated log text.
* Unknown keys are ignored. Both contexts hold repository-wide entries, and
  ``secrets`` always holds ``github_token``.

**Local.** A non-empty shell value beats ``.env.local``, as
``deployment_control_value`` in scripts/lib/deploy_identity_lifecycle.sh does.

**Aliases.** A field and its aliases must agree; an alias alone supplies a
scalar field. An identity alias (``DATABRICKS_CLIENT_ID`` and friends) never
supplies a role, and must equal that role in ``MIP_IDENTITY_IDS_JSON``.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values
from pydantic import ValidationError

from tools.clients.errors import ProfileRefusal, refusal_lines
from tools.clients.schema import (
    ALIASES,
    FIELD_CLASS,
    IDENTITY_ALIASES,
    ClientProfile,
    FieldClass,
    name_class,
)

KNOWN_NAMES = frozenset(
    {*FIELD_CLASS, *IDENTITY_ALIASES, *(alias for names in ALIASES.values() for alias in names)}
)
ENV_SOURCE_PREFIX = "env:"


@dataclass(frozen=True)
class RawProfile:
    """Profile fields by canonical name, plus identity aliases to cross-check."""

    values: Mapping[str, str]
    identity_aliases: Mapping[str, str] = field(default_factory=dict)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def read_context(source: str, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """A ``toJSON(vars)`` or ``toJSON(secrets)`` object from ``env:NAME`` or a file."""

    if source.lstrip().startswith(("{", "[")):
        raise ProfileRefusal(["pass a context as env:NAME or a file path, never JSON on the command line"])
    if source.startswith(ENV_SOURCE_PREFIX):
        name = source.removeprefix(ENV_SOURCE_PREFIX)
        text = (os.environ if environ is None else environ).get(name, "")
        label = f"environment variable {name}"
    else:
        path = Path(source)
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        label = f"file {path.name}"
    try:
        parsed = json.loads(text) if text.strip() else None
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict):
        raise ProfileRefusal([f"{label}: must hold a JSON object of names to values"])
    return {str(name): value for name, value in parsed.items() if isinstance(value, str)}


def _canonicalize(found: Mapping[str, str]) -> RawProfile:
    values = {name: value for name, value in found.items() if name in FIELD_CLASS}
    refusals: list[str] = []
    for canonical, aliases in ALIASES.items():
        present = [name for name in (canonical, *aliases) if name in found]
        if len({found[name] for name in present}) > 1:
            refusals.append(f"{', '.join(present)}: must agree")
        elif present:
            values[canonical] = found[present[0]]
    if refusals:
        raise ProfileRefusal(refusals)
    identity = {name: value for name, value in found.items() if name in IDENTITY_ALIASES}
    return RawProfile(values=values, identity_aliases=identity)


def from_github(vars_context: Mapping[str, object], secrets_context: Mapping[str, object]) -> RawProfile:
    """The profile from the deploy environment's variables and secrets."""

    refusals: list[str] = []
    found: dict[str, str] = {}
    for name, value in sorted(vars_context.items()):
        text = _text(value)
        if name not in KNOWN_NAMES or not text:
            continue
        kind = name_class(name)
        if kind is FieldClass.PUBLIC:
            found[name] = text
        else:
            refusals.append(f"{name}: is {kind}; store it as an environment secret, never a variable")
    for name, value in sorted(secrets_context.items()):
        text = _text(value)
        if name not in KNOWN_NAMES or not text:
            continue
        if name_class(name) is FieldClass.PUBLIC:
            refusals.append(
                f"{name}: is public; store it as an environment variable, or the runner "
                "masks it and blanks unrelated log text"
            )
        else:
            found[name] = text
    if refusals:
        raise ProfileRefusal(refusals)
    return _canonicalize(found)


def from_local(environ: Mapping[str, str], env_file: Path | None) -> RawProfile:
    """The profile from the shell and ``.env.local``; a non-empty shell value wins."""

    dotenv: dict[str, str] = {}
    if env_file is not None and env_file.is_file():
        dotenv = {
            name: value
            for name, value in dotenv_values(env_file, interpolate=False).items()
            if value is not None
        }
    found: dict[str, str] = {}
    for name in KNOWN_NAMES:
        text = _text(environ.get(name)) or _text(dotenv.get(name))
        if text:
            found[name] = text
    return _canonicalize(found)


def resolve(raw: RawProfile, *, allow_placeholders: bool = False) -> ClientProfile:
    """Validate ``raw`` into a profile, or raise a value-free ``ProfileRefusal``."""

    try:
        profile = ClientProfile.model_validate(
            dict(raw.values), context={"allow_placeholders": allow_placeholders}
        )
    except ValidationError as exc:
        # ``from None``: a chained ValidationError would print its inputs.
        raise ProfileRefusal(refusal_lines(exc)) from None
    refusals = []
    for name, value in sorted(raw.identity_aliases.items()):
        role = IDENTITY_ALIASES[name]
        expected = getattr(profile.identity_ids, role)
        if expected is None or expected.casefold() != value.casefold():
            refusals.append(f"{name}: must equal MIP_IDENTITY_IDS_JSON.{role}")
    if refusals:
        raise ProfileRefusal(refusals)
    return profile
