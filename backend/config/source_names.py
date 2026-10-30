"""The Cotality source share an install reads: its catalog, schema and table prefix.

The canonical names are the Entrada evaluation share as it is mounted today:
catalog ``cotality_mortgage_data``, schema ``corelogic`` and tables named
``entrada_eval_<stem>``. An install whose share is mounted under other names
overrides them with ``MIP_COTALITY_SOURCE_CATALOG``, ``_SCHEMA`` and
``_TABLE_PREFIX`` (client profile fields, class public: tools/clients/schema.py).
Unset, blank and template values mean the canonical name (the ``_is_real``
rule of tools/databricks/bundle_env.py); scripts/deploy.sh refuses a set value
that is not a lowercase identifier, a template included, before any render.

Who reads the names:

* ``tools/render_sql.py`` rewrites the canonical qualified forms in the SQL
  sources, and refuses a rendered tree that still carries a canonical token.
* ``tools/databricks/bundle_env.py`` exports them as the bundle variables
  ``cotality_source_*``; ``databricks.yml`` hands those to the feature
  pipeline's configuration, which the pipeline reads with no fallback.
* ``tools/databricks/provision_genie_space.py`` renders the Genie space spec,
  and ``tools/databricks/ensure_asset_lineage_view.py`` the lineage view.

``from_env`` reads a mapping the caller has already merged: scripts/deploy.sh
resolves the names shell-first, then from ``.env.local``, and exports them, as
it does for every deployment control. The App's registry, lineage and guards
still name the canonical source; they move onto these names, through App
settings, separately.

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

# The client profile's shapes for the same fields (tools/clients/shapes.py
# UC_IDENTIFIER and TABLE_PREFIX; a test pins them equal).
UC_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]{0,254}")
TABLE_PREFIX = re.compile(r"[a-z0-9_]{0,64}")
_LEGACY_PLACEHOLDER = "00000000PLACEHOLDER"


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

    def describe(self) -> str:
        """``catalog.schema.prefix*``: the source in one public line."""

        return f"{self.catalog}.{self.schema}.{self.table_prefix}*"


CANONICAL = CotalitySource(
    catalog="cotality_mortgage_data",
    schema="corelogic",
    table_prefix="entrada_eval_",
)


def _configured(value: object) -> str | None:
    """A set value, or ``None`` for unset, blank and template values."""

    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text == _LEGACY_PLACEHOLDER or (text.startswith("<") and text.endswith(">")):
        return None
    return text


def resolve(
    *,
    catalog: object = None,
    schema: object = None,
    table_prefix: object = None,
) -> CotalitySource:
    """The source with each set override applied over the canonical name."""

    return CotalitySource(
        catalog=_configured(catalog) or CANONICAL.catalog,
        schema=_configured(schema) or CANONICAL.schema,
        table_prefix=_configured(table_prefix) or CANONICAL.table_prefix,
    )


def from_env(environ: Mapping[str, str]) -> CotalitySource:
    """The source from ``MIP_COTALITY_SOURCE_*`` in ``environ``."""

    return resolve(
        catalog=environ.get(CATALOG_ENV),
        schema=environ.get(SCHEMA_ENV),
        table_prefix=environ.get(TABLE_PREFIX_ENV),
    )
