"""A fake deployer workspace and account for tools/clients/live_checks.py (M1-PR2, M1-PR3).

``LiveWorkspace`` answers the reads the live checks make, records each one in
``calls``, and refuses any attribute nobody stubbed, so a check that tries a
write (or an unreviewed read) fails the test instead of passing silently. By
default it is a healthy first install of the profile it is given: the
workspace, metastore and deployer are the profile's; no App, no bundle state
and no install catalog exist yet; the Cotality share catalog is a Delta
Sharing catalog from the profile's provider and share with all seven tables;
the metastore has a storage root; an install catalog, when a test makes one
exist, has the profile's storage root; and the account principal holds no
account admin. Each keyword overrides one of those facts.
"""

from __future__ import annotations

import io
import json
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

from databricks.sdk.errors import NotFound

from backend.config import source_names

_UNSET = object()


def bundle_state(**bound: str) -> dict[str, Any]:
    """A direct-engine state document binding ``resources.<key>`` to each id given."""

    return {
        "state_version": 2,
        "cli_version": "1.10.0",
        "lineage": "00000000-0000-4000-8000-0000000000aa",
        "serial": 7,
        "state": {f"resources.{key}": {"__id__": live_id, "state": {}} for key, live_id in bound.items()},
    }


class _Strict(SimpleNamespace):
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"the live checks called an unstubbed SDK surface: {name}")


class LiveWorkspace:
    """A fake deployer ``WorkspaceClient`` for one profile (see the module docstring)."""

    def __init__(
        self,
        profile: Mapping[str, str],
        *,
        identity_ids: Mapping[str, str],
        app: object = None,
        state: object = None,
        journal: bool = False,
        catalog_properties: object = None,
        catalog_storage_root: object = _UNSET,
        share: object = _UNSET,
        missing_tables: tuple[str, ...] = (),
        metastore_root: str = "s3://metastore-root-alpha/root",
        fail: Mapping[str, BaseException] | None = None,
        **identity: str,
    ) -> None:
        live = {
            "host": profile["DATABRICKS_HOST"],
            "workspace_id": profile["MIP_EXPECT_WORKSPACE_ID"],
            "metastore_id": profile["MIP_EXPECT_METASTORE_ID"],
            "metastore_workspace_id": profile["MIP_EXPECT_WORKSPACE_ID"],
            "principal": identity_ids["deployer"],
            **identity,
        }
        self.calls: list[str] = []
        self.config = SimpleNamespace(host=live["host"])
        self._live = live
        self._profile = dict(profile)
        self._app = app
        self._state = state
        self._journal = journal
        self._catalog_properties = catalog_properties
        # An existing install catalog was created where the profile says, unless a test says otherwise.
        self._catalog_storage_root = (
            profile.get("MIP_UC_CATALOG_STORAGE_ROOT") if catalog_storage_root is _UNSET else catalog_storage_root
        )
        self._share = self._default_share() if share is _UNSET else share
        self._missing = set(missing_tables)
        self._metastore_root = metastore_root
        self._fail = dict(fail or {})
        self.metastores = _Strict(current=self._current, summary=self._summary)
        self.current_user = _Strict(me=self._me)
        self.apps = _Strict(get=self._app_get)
        self.workspace = _Strict(download=self._download, get_status=self._get_status)
        self.catalogs = _Strict(get=self._catalog_get)
        self.tables = _Strict(exists=self._table_exists)

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"the live checks called an unstubbed SDK surface: {name}")

    # --- identity (M1-PR2) ---------------------------------------------------

    def get_workspace_id(self) -> int:
        self.calls.append("GET scim/Me (org id)")
        return int(self._live["workspace_id"])

    def _current(self) -> object:
        self.calls.append("GET current-metastore-assignment")
        return SimpleNamespace(
            metastore_id=self._live["metastore_id"],
            workspace_id=int(self._live["metastore_workspace_id"]),
        )

    def _me(self) -> object:
        self.calls.append("GET scim/Me")
        return SimpleNamespace(user_name=self._live["principal"])

    # --- install (M1-PR3) ----------------------------------------------------

    def _raise(self, call: str) -> None:
        self.calls.append(call)
        if call in self._fail:
            raise self._fail[call]

    def _app_get(self, name: str) -> object:
        self._raise("GET apps")
        if self._app is None:
            raise NotFound("no such app")
        return self._app

    def _download(self, path: str) -> object:
        self._raise("GET workspace/export (bundle state)")
        assert path.endswith("/state/resources.json"), path
        if self._state is None:
            raise NotFound("no state")
        body = self._state if isinstance(self._state, bytes) else json.dumps(self._state).encode()
        return io.BytesIO(body)

    def _get_status(self, path: str) -> object:
        self._raise("GET workspace/get-status (first-install journal)")
        if not self._journal:
            raise NotFound("no journal")
        return SimpleNamespace(path=path)

    def _default_share(self) -> object:
        return SimpleNamespace(
            catalog_type=SimpleNamespace(value="DELTASHARING_CATALOG"),
            provider_name=self._profile.get("MIP_COTALITY_SHARE_PROVIDER"),
            share_name=self._profile.get("MIP_COTALITY_SHARE_NAME"),
            properties={},
        )

    def _source(self) -> source_names.CotalitySource:
        return source_names.resolve(
            catalog=self._profile.get(source_names.CATALOG_ENV),
            schema=self._profile.get(source_names.SCHEMA_ENV),
            table_prefix=self._profile.get(source_names.TABLE_PREFIX_ENV),
        )

    def _catalog_get(self, name: str) -> object:
        if name == self._profile["MIP_DEFAULT_CATALOG"]:
            self._raise("GET catalogs (install)")
            if self._catalog_properties is None:
                raise NotFound("no such catalog")
            return SimpleNamespace(properties=self._catalog_properties, storage_root=self._catalog_storage_root)
        assert name == self._source().catalog, name
        self._raise("GET catalogs (share)")
        if self._share is None:
            raise NotFound("no such catalog")
        return self._share

    def _table_exists(self, full_name: str) -> object:
        self._raise("GET tables/exists")
        source = self._source()
        stems = {source.table(stem): stem for stem in source_names.TABLE_STEMS}
        assert full_name in stems, full_name
        return SimpleNamespace(table_exists=stems[full_name] not in self._missing)

    def _summary(self) -> object:
        self._raise("GET metastore_summary")
        return SimpleNamespace(storage_root=self._metastore_root)


class LiveAccount:
    """A fake account client: the account principal with ``roles``."""

    def __init__(self, application_id: str, *, roles: tuple[str, ...] = (), matches: int = 1) -> None:
        self.calls: list[str] = []
        self._records = [
            SimpleNamespace(
                id=f"9000000000000{n}",
                application_id=application_id,
                roles=[SimpleNamespace(value=role) for role in roles],
            )
            for n in range(matches)
        ]
        self.service_principals = _Strict(list=self._list)

    def _list(self, *, filter: str, attributes: str) -> list[object]:
        self.calls.append(f"GET accounts/scim/ServicePrincipals ({attributes})")
        return list(self._records)
