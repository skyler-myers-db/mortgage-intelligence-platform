"""The Cotality source share an install reads: its catalog, schema and table prefix.

The canonical names are the Entrada evaluation share as it is mounted today:
catalog ``cotality_mortgage_data``, schema ``corelogic`` and tables named
``entrada_eval_<stem>``. An install whose share is mounted under other names
overrides them with ``MIP_COTALITY_SOURCE_CATALOG``, ``_SCHEMA`` and
``_TABLE_PREFIX`` (client profile fields, class public: tools/clients/schema.py).
Unset and blank values mean the canonical name. A template value (``<...>`` or
the legacy ``00000000PLACEHOLDER``) is refused, here and by scripts/deploy.sh
before any render, rather than read as unset: a copied template line is a
source someone meant to configure, so every caller, the deploy script or not,
fails closed on it.

A share whose tables carry no prefix sets ``MIP_COTALITY_SOURCE_TABLE_PREFIX``
to ``-`` (``NO_TABLE_PREFIX``). An empty value cannot say so: blank means unset
in ``.env.local``, in GitHub variables and in ``deployment_control_value``, and
unset means the canonical prefix. So every layer that carries the setting
(the env, the client profile, the ``cotality_source_table_prefix`` bundle
variable and the pipeline configuration) spells no prefix as ``-``, and only
a resolved ``CotalitySource`` holds ``""``.

Who reads the names:

* ``tools/render_sql.py`` rewrites the canonical qualified forms in the SQL
  sources, and refuses a rendered tree that still carries a canonical token.
* ``tools/databricks/bundle_env.py`` exports them as the bundle variables
  ``cotality_source_*``; ``databricks.yml`` hands those to the feature
  pipeline's configuration, which the pipeline reads with no fallback.
* ``tools/databricks/provision_genie_space.py`` renders the Genie space spec,
  and ``tools/databricks/ensure_asset_lineage_view.py`` the lineage view.
* The App reads them as settings (``backend/config/settings.py``,
  ``mip_cotality_source_*``, canonical by default): the asset registry and the
  lineage manifest name the configured share tables under their canonical
  ``source_key``, and the prompt guards refuse the configured share's
  ``qualified_forms`` beside their own canonical patterns.
  ``tools/databricks/app_deploy_payload.py`` sends a name to the App only when
  it is set and differs from the canonical one.

``from_env`` reads a mapping the caller has already merged: scripts/deploy.sh
resolves the names shell-first, then from ``.env.local``, and exports them, as
it does for every deployment control.

Standard library only: the renderer runs before the backend's runtime
requirements are installed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

CATALOG_ENV = "MIP_COTALITY_SOURCE_CATALOG"
SCHEMA_ENV = "MIP_COTALITY_SOURCE_SCHEMA"
TABLE_PREFIX_ENV = "MIP_COTALITY_SOURCE_TABLE_PREFIX"
ENV_NAMES = (CATALOG_ENV, SCHEMA_ENV, TABLE_PREFIX_ENV)

# The share tables the silver lift reads, by the name each carries after the
# table prefix.
TABLE_STEMS: tuple[str, ...] = (
    "property_domain_v3",
    "voluntary_lien_status_marketing_v2",
    "mls_listing_v1",
    "heloc_propensity_score_v1",
    "refi_propensity_score_v1",
    "mortgage_domain_v1",
    "owner_transfer_domain_v1",
)

# The configured spelling of "the share's tables carry no prefix". It is not
# a prefix character, so no real prefix can mean it.
NO_TABLE_PREFIX = "-"

# The client profile's shapes for the same fields (tools/clients/shapes.py
# UC_IDENTIFIER and TABLE_PREFIX; a test pins them equal). TABLE_PREFIX is a
# resolved prefix, "" for none; TABLE_PREFIX_SETTING is how one is configured.
UC_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]{0,254}")
TABLE_PREFIX = re.compile(r"[a-z0-9_]{0,64}")
TABLE_PREFIX_SETTING = re.compile(r"[a-z0-9_]{1,64}|-")
_LEGACY_PLACEHOLDER = "00000000PLACEHOLDER"
# A template value: tools/clients/shapes.py's placeholder rule (a test pins it).
_TEMPLATE = re.compile(r"<[^<>\n]+>")


@dataclass(frozen=True)
class CotalitySource:
    """One install's source names; construction validates their shapes."""

    catalog: str
    schema: str
    table_prefix: str

    def __post_init__(self) -> None:
        for field, value, shape in (
            ("catalog", self.catalog, UC_IDENTIFIER),
            ("schema", self.schema, UC_IDENTIFIER),
            ("table_prefix", self.table_prefix, TABLE_PREFIX),
        ):
            if not isinstance(value, str) or shape.fullmatch(value) is None:
                raise ValueError(f"Cotality source {field} must be a lowercase unquoted identifier")

    @property
    def is_canonical(self) -> bool:
        return self == CANONICAL

    def table_name(self, stem: str) -> str:
        """The share table's name, unqualified."""

        if stem not in TABLE_STEMS:
            raise ValueError(f"unknown Cotality source table stem: {stem}")
        return f"{self.table_prefix}{stem}"

    def table(self, stem: str) -> str:
        """The share table's three-part name."""

        return f"{self.catalog}.{self.schema}.{self.table_name(stem)}"

    @property
    def table_prefix_setting(self) -> str:
        """The prefix as configuration spells it: ``NO_TABLE_PREFIX`` for none."""

        return self.table_prefix or NO_TABLE_PREFIX

    def describe(self) -> str:
        """``catalog.schema.prefix*``: the source in one public line."""

        return f"{self.catalog}.{self.schema}.{self.table_prefix}*"


CANONICAL = CotalitySource(
    catalog="cotality_mortgage_data",
    schema="corelogic",
    table_prefix="entrada_eval_",
)


def _configured(env_name: str, value: object) -> str | None:
    """A set value, or ``None`` for unset and blank; a template value refuses.

    The refusal names the setting, never the value.
    """

    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{env_name} must be a string")
    text = value.strip()
    if not text:
        return None
    if text == _LEGACY_PLACEHOLDER or _TEMPLATE.search(text) is not None:
        raise ValueError(f"{env_name} is a template value; set it or leave it unset")
    return text


def resolve(
    *,
    catalog: object = None,
    schema: object = None,
    table_prefix: object = None,
) -> CotalitySource:
    """The source with each set override applied over the canonical name.

    ``table_prefix`` is the setting: ``NO_TABLE_PREFIX`` resolves to ``""``.
    """

    prefix = _configured(TABLE_PREFIX_ENV, table_prefix)
    return CotalitySource(
        catalog=_configured(CATALOG_ENV, catalog) or CANONICAL.catalog,
        schema=_configured(SCHEMA_ENV, schema) or CANONICAL.schema,
        table_prefix=(
            CANONICAL.table_prefix if prefix is None else "" if prefix == NO_TABLE_PREFIX else prefix
        ),
    )


def from_env(environ: Mapping[str, str]) -> CotalitySource:
    """The source from ``MIP_COTALITY_SOURCE_*`` in ``environ``."""

    return resolve(
        catalog=environ.get(CATALOG_ENV),
        schema=environ.get(SCHEMA_ENV),
        table_prefix=environ.get(TABLE_PREFIX_ENV),
    )


def canonical_stem(catalog: str | None, schema: str, object_name: str) -> str | None:
    """The stem when the three parts name a canonical share table, else ``None``.

    Exact and case-sensitive, as the closed-shape render is: the registry and
    the lineage manifest spell the canonical names in lowercase.
    """

    prefix = CANONICAL.table_prefix
    if catalog != CANONICAL.catalog or schema != CANONICAL.schema:
        return None
    if not object_name.startswith(prefix):
        return None
    stem = object_name[len(prefix) :]
    return stem if stem in TABLE_STEMS else None


def qualified_forms(source: CotalitySource) -> tuple[str, ...]:
    """The configured share's qualified names, for the prompt guards.

    ``catalog.schema.`` and each share table's three-part name. Never a bare
    catalog, schema or table name: a schema called ``mortgage`` must not make
    every mortgage question a refusal. Empty for the canonical source, whose
    names the guards' own reviewed patterns already refuse.
    """

    if source.is_canonical:
        return ()
    return (
        f"{source.catalog}.{source.schema}.",
        *(source.table(stem) for stem in TABLE_STEMS),
    )
