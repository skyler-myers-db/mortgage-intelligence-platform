"""A fake Unity Catalog for the ownership handover: reads, owner-only updates, nothing else.

``FakeUc`` holds one document (catalogs, schemas, tables, volumes, functions,
registered models and their owners) and models exactly the SDK calls the
handover makes: ``list``/``get``/``read`` and ``update(name, owner=...)``. It
has no delete, create or grant method, so a stray call fails the test, and
it records every update in ``updates``. ``fail_on`` makes the update of one
object raise before it lands; ``land_then_fail`` makes it land and then raise.

Pipeline datasets behave as spike S4 measured them: an owner update on a
streaming table or materialized view is refused (``PipelineOwnerRefused``,
with the measured message; nothing changes), ``pipelines.get`` answers the
effective ``run_as_user_name`` and a ``spec`` without run_as, and
``set_run_as`` (a pipeline run_as update) re-owns every dataset the pipeline
publishes to the new identity at once, before any run.

Every value is synthetic: example-domain owners and the fixed catalog ``mip``.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

from databricks.sdk.errors import DatabricksError, NotFound

LEGACY = "legacy-owner@example.com"
TARGET = "acme-data-owners"
OTHER = "another-team@example.com"
JOBS = "00000000-0000-4000-8000-0000000000c2"
PIPELINE = "pipeline-PLACEHOLDER"
PIPELINE_TYPES = frozenset({"STREAMING_TABLE", "MATERIALIZED_VIEW"})


class PipelineOwnerRefused(DatabricksError):
    """The owner PATCH spike S4 saw refused for a pipeline dataset (its error class was not recorded)."""


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
            "mip.silver.parcel_stream": {"owner": LEGACY, "type": "STREAMING_TABLE", "pipeline_id": PIPELINE},
            "mip.silver.parcel_rollup": {"owner": LEGACY, "type": "MATERIALIZED_VIEW", "pipeline_id": PIPELINE},
            "mip.audit.action_log": {"owner": OTHER, "type": "MANAGED"},
            "mip.information_schema.tables": {"owner": "System user", "type": "VIEW"},
        },
        "volumes": {"mip.gold.exports": LEGACY},
        "functions": {"mip.gold.fn_lead_score": LEGACY, "mip.gold.fn_rate_spread": LEGACY},
        "models": {"mip.gold.propensity_model": LEGACY},
        # The governed pipeline runs as the legacy owner until the release.
        "pipelines": {PIPELINE: LEGACY},
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
        row = self._fake.doc[self._section][name]
        if isinstance(row, dict) and (row.get("pipeline_id") or row.get("type") in PIPELINE_TYPES):
            kind = "materialized view" if row.get("type") == "MATERIALIZED_VIEW" else "streaming table"
            raise PipelineOwnerRefused(
                f"Change owner on a {kind} managed by a DLT pipeline is not supported, "
                "consider changing the owner of the DLT pipeline itself"
            )
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


class _Pipelines:
    def __init__(self, fake: FakeUc) -> None:
        self._fake = fake

    def get(self, *, pipeline_id: str) -> Any:
        run_as = self._fake.doc.get("pipelines", {}).get(pipeline_id)
        if run_as is None:
            raise NotFound("pipeline not found")
        return SimpleNamespace(pipeline_id=pipeline_id, run_as_user_name=run_as, spec=SimpleNamespace(name="pipeline"))


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
        self.pipelines = _Pipelines(self)

    def set_run_as(self, pipeline_id: str, identity: str) -> None:
        """A pipeline run_as update: every dataset it publishes is the new identity's at once (spike S4)."""

        self.doc["pipelines"][pipeline_id] = identity
        for row in self.doc["tables"].values():
            if row.get("pipeline_id") == pipeline_id:
                row["owner"] = identity

    def owners(self) -> dict[str, str]:
        """Every object's owner, for an exact before/after comparison."""

        found: dict[str, str] = {}
        for section in ("catalogs", "schemas", "tables", "volumes", "functions", "models"):
            for name, row in self.doc[section].items():
                found[f"{section}:{name}"] = row["owner"] if isinstance(row, dict) else row
        return found
