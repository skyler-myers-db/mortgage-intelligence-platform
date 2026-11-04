"""A fake Unity Catalog for the ownership handover: reads, owner-only updates, nothing else.

``FakeUc`` holds one document (catalogs, schemas, tables, volumes, functions,
registered models and their owners) and models exactly the SDK calls the
handover makes: ``list``/``get``/``read`` and ``update(name, owner=...)``. It
has no delete, create or grant method, so a stray call fails the test, and
it records every update in ``updates``. ``fail_on`` makes the update of one
object raise before it lands; ``land_then_fail`` makes it land and then raise.

Every value is synthetic: example-domain owners and the fixed catalog ``mip``.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

from databricks.sdk.errors import NotFound

LEGACY = "legacy-owner@example.com"
TARGET = "acme-data-owners"
OTHER = "another-team@example.com"


def estate() -> dict[str, Any]:
    """A governed catalog the legacy owner owns, a sync catalog, and objects to leave alone."""

    return {
        "me": LEGACY,
        "catalogs": {
            "mip": {"type": "MANAGED_CATALOG", "owner": LEGACY},
            "mip_app_state": {"type": "MANAGED_ONLINE_CATALOG", "owner": LEGACY, "connection_name": "managed-database-catalog-PLACEHOLDER"},
            "cotality_share": {"type": "DELTASHARING_CATALOG", "owner": LEGACY, "share_name": "share-PLACEHOLDER"},
            "lakehouse_federation": {"type": "FOREIGN_CATALOG", "owner": LEGACY, "connection_name": "conn-PLACEHOLDER"},
            "system": {"type": "SYSTEM_CATALOG", "owner": "System user"},
        },
        "schemas": {
            "mip.gold": LEGACY,
            "mip.silver": LEGACY,
            "mip.audit": OTHER,
            "mip.information_schema": "System user",
            "mip_app_state.mip_sync": LEGACY,
            "mip_app_state.information_schema": LEGACY,
        },
        "tables": {
            "mip.gold.borrower_360": {"owner": LEGACY, "type": "MANAGED"},
            "mip.gold.lead_scores_v": {"owner": LEGACY, "type": "VIEW"},
            "mip.gold.portfolio_metrics": {"owner": LEGACY, "type": "METRIC_VIEW"},
            "mip.gold.owned_elsewhere": {"owner": OTHER, "type": "MANAGED"},
            "mip.silver.parcel_stream": {"owner": LEGACY, "type": "STREAMING_TABLE", "pipeline_id": "pipeline-PLACEHOLDER"},
            "mip.silver.parcel_rollup": {"owner": LEGACY, "type": "MATERIALIZED_VIEW", "pipeline_id": "pipeline-PLACEHOLDER"},
            "mip.audit.action_log": {"owner": OTHER, "type": "MANAGED"},
            "mip.information_schema.tables": {"owner": "System user", "type": "VIEW"},
        },
        "volumes": {"mip.gold.exports": LEGACY},
        "functions": {"mip.gold.fn_lead_score": LEGACY, "mip.gold.fn_rate_spread": LEGACY},
        "models": {"mip.gold.propensity_model": LEGACY},
    }


class _Api:
    def __init__(self, fake: FakeUc, section: str) -> None:
        self._fake = fake
        self._section = section

    def _owner_of(self, name: str) -> str:
        rows = self._fake.doc[self._section]
        if name not in rows:
            raise NotFound(f"{self._section} not found")
        row = rows[name]
        return row["owner"] if isinstance(row, dict) else row

    def get(self, name: str) -> Any:
        return SimpleNamespace(full_name=name, name=name, owner=self._owner_of(name))

    read = get

    def update(self, name: str, *, owner: str) -> Any:
        self._owner_of(name)
        if name in self._fake.fail_on:
            raise RuntimeError("injected update failure")
        rows = self._fake.doc[self._section]
        if isinstance(rows[name], dict):
            rows[name]["owner"] = owner
        else:
            rows[name] = owner
        self._fake.updates.append((self._section, name, owner))
        if name in self._fake.land_then_fail:
            raise RuntimeError("injected failure after the update landed")
        return self.get(name)

    def _children(self, catalog_name: str, schema_name: str) -> list[Any]:
        prefix = f"{catalog_name}.{schema_name}."
        return [self.get(name) for name in sorted(self._fake.doc[self._section]) if name.startswith(prefix)]


class _Catalogs(_Api):
    def get(self, name: str) -> Any:
        row = self._fake.doc["catalogs"].get(name)
        if row is None:
            raise NotFound("catalog not found")
        return SimpleNamespace(
            name=name,
            full_name=name,
            owner=row["owner"],
            catalog_type=SimpleNamespace(value=row["type"]),
            share_name=row.get("share_name"),
            provider_name=row.get("provider_name"),
            connection_name=row.get("connection_name"),
        )


class _Schemas(_Api):
    def list(self, catalog_name: str) -> list[Any]:
        return [
            SimpleNamespace(name=name.split(".", 1)[1], full_name=name, owner=owner)
            for name, owner in sorted(self._fake.doc["schemas"].items())
            if name.split(".", 1)[0] == catalog_name
        ]


class _Tables(_Api):
    def get(self, name: str) -> Any:
        row = self._fake.doc["tables"].get(name)
        if row is None:
            raise NotFound("table not found")
        return SimpleNamespace(
            full_name=name,
            owner=row["owner"],
            table_type=SimpleNamespace(value=row["type"]),
            pipeline_id=row.get("pipeline_id"),
        )

    def list(self, catalog_name: str, schema_name: str) -> list[Any]:
        return self._children(catalog_name, schema_name)


class _Leaves(_Api):
    def list(self, catalog_name: str, schema_name: str) -> list[Any]:
        return self._children(catalog_name, schema_name)


class _Models(_Api):
    def list(self, *, catalog_name: str, schema_name: str) -> list[Any]:
        return self._children(catalog_name, schema_name)


class FakeUc:
    def __init__(self, doc: dict[str, Any] | None = None) -> None:
        self.doc = copy.deepcopy(doc or estate())
        self.updates: list[tuple[str, str, str]] = []
        self.fail_on: set[str] = set()
        self.land_then_fail: set[str] = set()
        self.current_user = SimpleNamespace(me=lambda: SimpleNamespace(user_name=self.doc["me"]))
        self.catalogs = _Catalogs(self, "catalogs")
        self.schemas = _Schemas(self, "schemas")
        self.tables = _Tables(self, "tables")
        self.volumes = _Leaves(self, "volumes")
        self.functions = _Leaves(self, "functions")
        self.registered_models = _Models(self, "models")

    def owners(self) -> dict[str, str]:
        """Every object's owner, for an exact before/after comparison."""

        found: dict[str, str] = {}
        for section in ("catalogs", "schemas", "tables", "volumes", "functions", "models"):
            for name, row in self.doc[section].items():
                found[f"{section}:{name}"] = row["owner"] if isinstance(row, dict) else row
        return found
