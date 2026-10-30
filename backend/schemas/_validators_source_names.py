"""The configured Cotality share's qualified names, refused by the prompt guards.

``backend/services/audit_metadata_value_policy.py`` refuses them as an audit
source asset too, beside its canonical ``cotality_mortgage_data.`` prefix.

The guards' own reviewed patterns refuse the canonical share
(``_PROMPT_JAILBREAK_RE`` in ``backend/schemas/growth_agent.py`` and
``_SCOPE_BYPASS_PROMPT_PATTERNS`` in
``backend/services/genie_prompt_guardrails.py``), and this module never
changes them. An install that mounts the share under other names
(``backend/config/source_names.py``) adds exactly these refusals: the share's
schema-qualified prefix ``catalog.schema.`` and each share table's three-part
name, each part ``re.escape``'d. Each part may carry an SQL identifier quote
and each dot surrounding whitespace, the spellings SQL itself accepts
(`` `acme`.`mortgage`. `` and ``acme . mortgage .``): the canonical growth
guard refuses its bare catalog token, so it refuses those spellings too, and
a configured install must not be weaker. A bare configured token is never a
pattern: a schema called ``mortgage`` must leave every ordinary mortgage
question alone, so a form that is not ``catalog.schema.``-qualified refuses to
compile, and every spelling still needs every part and both dots. The
canonical source adds nothing, and no exemption set is read or widened here.

Runtime settings register the forms (``backend/config/settings.py``), so the
schema layer imports no config. With no provider registered, the guards run
their canonical patterns alone, which is exactly the canonical install.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import lru_cache

_FormsProvider = Callable[[], tuple[str, ...]]
_forms_provider: _FormsProvider | None = None

# ``catalog.schema.`` then an optional table name, in the lowercase identifier
# shapes source_names validates. Both dots and both leading identifiers are
# required, so no bare catalog, schema or table name can pass.
_QUALIFIED_FORM = re.compile(r"[a-z_][a-z0-9_]{0,254}\.[a-z_][a-z0-9_]{0,254}\.[a-z0-9_]{0,320}")

# An identifier quote of either SQL dialect Databricks parses (the backtick,
# and the double quote under ANSI double-quoted identifiers), optional around
# each part, and whitespace around each dot: ``acme . mortgage .`` and
# `` `acme`.`mortgage`. `` name the share as ``acme.mortgage.`` does.
_QUOTE = r'[`"]?'
_DOT = rf"{_QUOTE}\s*\.\s*{_QUOTE}"


def _spellings(form: str) -> str:
    """``form``'s parts, each ``re.escape``'d, joined by a quoted-or-spaced dot."""

    return _QUOTE + _DOT.join(re.escape(part) for part in form.split("."))


def set_configured_source_forms_provider(provider: _FormsProvider | None) -> None:
    """Register the configured share's qualified names without importing settings."""

    global _forms_provider
    _forms_provider = provider


@lru_cache(maxsize=4)
def _compiled(forms: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    if not forms:
        return ()
    if any(_QUALIFIED_FORM.fullmatch(form) is None for form in forms):
        raise ValueError("a configured Cotality source guard form must be catalog.schema.-qualified")
    alternatives = "|".join(_spellings(form) for form in forms)
    # A left boundary only: anything after ``catalog.schema.`` is still the share.
    return (re.compile(rf"(?<![A-Za-z0-9_])(?:{alternatives})", re.IGNORECASE),)


def configured_source_patterns() -> tuple[re.Pattern[str], ...]:
    """The configured share's refusal patterns; ``()`` for the canonical share."""

    provider = _forms_provider
    if provider is None:
        return ()
    return _compiled(tuple(provider()))


def contains_configured_source_reference(text: str) -> bool:
    """True when ``text`` names the configured share in a qualified form."""

    return any(pattern.search(text) for pattern in configured_source_patterns())
