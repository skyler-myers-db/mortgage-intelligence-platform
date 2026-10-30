"""A fake Databricks estate for the pre-stop probes: one JSON document, read-only SDK fakes.

``clean_estate()`` is a signed-blue install with nothing for any probe to
refuse: an existing App serving its signed record's deployment, approved
owners, no owner-equivalent grantee but the deployer, App access only on the
record's Gateway, and a Genie turn that completes. A test edits one part of it
to produce one refusal. The document is plain JSON, so
``tests/fixtures/prestop_launcher.py`` can hand the same estate to the real
``deploy.sh`` (tests/unit/test_prestop_refusal_matrix.py).

``FakeWorkspace`` and ``FakeAccount`` model only the read calls the probes
make. Every non-read call fails the test, except ``apps.stop``, which a
matrix test must prove never happens: it marks the App STOPPED in the
document instead. The rollback-record verifier, the first-install journal
read, the record-less Genie pin resolution and the Genie turn are seams the
estate scripts directly (``record``, ``journal``, ``genie_pin``,
``genie_turn``), see ``install``.
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from databricks.sdk.errors import NotFound

from tests.fixtures.client_profiles import IDENTITY_IDS

DEPLOYER = "deployer@example.invalid"
APP_NAME = "mip-app"
APP_CLIENT = "app-client-EXAMPLE"
APP_SCIM = "app-scim-1"
# The synthetic client profile's roles (tests/fixtures/client_profiles.py), so
# the real deploy.sh, which cross-checks each alias against them, can use these.
RUNTIME_CLIENT = IDENTITY_IDS["runtime"]
VERIFIER_CLIENT = IDENTITY_IDS["verifier"]
GOVERNED_SPACE = "space-governed"
BLUE_GATEWAY = "mip-agent-gateway-blue"
BLUE_SUPERVISOR_ENDPOINT = "mas-blue-endpoint"
LEGACY_GATEWAY = "mip-agent-gateway"
CANONICAL = "Mortgage Growth Agent"
_FILTER = re.compile(r'^(\w+) eq "(.*)"$')


def clean_estate() -> dict[str, Any]:
    """A signed-blue install every probe passes."""

    return {
        "deployer": DEPLOYER,
        "app": {
            "name": APP_NAME,
            "id": "app-id-1",
            "client_id": APP_CLIENT,
            "scim_id": APP_SCIM,
            "state": "RUNNING",
            "active_deployment": "deployment-blue",
            "acl": [],
        },
        "principals": {
            DEPLOYER: {"kind": "user", "id": "deployer-id"},
            APP_CLIENT: {"kind": "service_principal", "id": APP_SCIM},
            RUNTIME_CLIENT: {"kind": "service_principal", "id": "runtime-scim"},
            VERIFIER_CLIENT: {"kind": "service_principal", "id": "verifier-scim"},
            "mip-data-owners": {"kind": "group", "id": "group-owners", "members": []},
        },
        "agents": [
            {
                "supervisor_agent_id": "supervisor-blue",
                "display_name": CANONICAL,
                "creator": RUNTIME_CLIENT,
                "endpoint_name": BLUE_SUPERVISOR_ENDPOINT,
                "create_time": "2026-01-02T03:04:05.678Z",
            }
        ],
        "genie": {GOVERNED_SPACE: [_acl_entry("group_name", "admins", "CAN_MANAGE", True)]},
        "genie_errors": [],
        "secret_scopes": ["mip-app-rollback"],
        "metastore": {"id": "metastore-1", "owner": DEPLOYER},
        "catalogs": {
            "mip": {
                "owner": DEPLOYER,
                "schemas": {"audit": DEPLOYER, "gold": DEPLOYER},
                "tables": {"audit.campaign_treatment_snapshot": DEPLOYER},
            }
        },
        "grants": {
            "catalog/mip": [{"principal": APP_CLIENT, "privileges": ["USE_CATALOG"]}],
            "schema/mip.gold": [{"principal": DEPLOYER, "privileges": ["MANAGE"]}],
        },
        "grants_errors": [],
        "serving": {
            BLUE_GATEWAY: {
                "id": "endpoint-blue",
                "acl": [_acl_entry("service_principal_name", APP_CLIENT, "CAN_QUERY")],
            },
            BLUE_SUPERVISOR_ENDPOINT: {"id": "endpoint-supervisor", "acl": []},
        },
        "record": {
            "deployment_id": "deployment-blue",
            "genie_space_id": GOVERNED_SPACE,
            "gateway_endpoint": BLUE_GATEWAY,
            "supervisor_endpoint": BLUE_SUPERVISOR_ENDPOINT,
        },
        "rebase_absence": "absent",
        "journal": "absent",
        "genie_pin": None,
        "genie_turn": "COMPLETED",
        "calls": [],
    }


def rename_record_supervisor(estate: dict[str, Any]) -> dict[str, Any]:
    """The record still pins ``mas-blue-endpoint``, whose Supervisor left the canonical name.

    A runtime Supervisor now holds the canonical name, so ``pin_journal`` (which
    accepts only the canonical-name Supervisor) can never replace the record's.
    """

    estate["agents"][0]["display_name"] = f"{CANONICAL} (retired copy)"
    estate["agents"].append(
        {
            "supervisor_agent_id": "supervisor-green",
            "display_name": CANONICAL,
            "creator": RUNTIME_CLIENT,
            "endpoint_name": "mas-green-endpoint",
            "create_time": "2026-02-02T03:04:05.678Z",
        }
    )
    estate["serving"]["mas-green-endpoint"] = {"id": "endpoint-green", "acl": []}
    return estate


def _acl_entry(field: str, name: str, level: str, inherited: bool | str = False) -> dict[str, Any]:
    permission: dict[str, Any] = {"permission_level": level, "inherited": bool(inherited)}
    if isinstance(inherited, str):
        permission["inherited_from_object"] = [inherited]
    return {field: name, "all_permissions": [permission]}


acl_entry = _acl_entry


def _ns(value: Any) -> Any:
    if isinstance(value, dict):
        return SimpleNamespace(**{key: _ns(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_ns(item) for item in value]
    return value


def _filter(expression: str) -> tuple[str, str]:
    match = _FILTER.fullmatch(expression)
    assert match is not None, f"unexpected SCIM filter {expression!r}"
    return match.group(1), match.group(2)


class _Scim:
    """users / service_principals / groups over the estate's one principal table."""

    _ATTRIBUTE = {"user": "userName", "service_principal": "applicationId", "group": "displayName"}
    _FIELD = {"user": "user_name", "service_principal": "application_id", "group": "display_name"}

    def __init__(self, estate: dict[str, Any], kind: str) -> None:
        self._estate = estate
        self._kind = kind

    def _items(self) -> list[tuple[str, dict[str, Any]]]:
        return [
            (name, row)
            for name, row in self._estate["principals"].items()
            if row["kind"] == self._kind
        ]

    def _record(self, name: str, row: dict[str, Any]) -> SimpleNamespace:
        fields = {
            "id": row["id"],
            "active": True,
            "display_name": "",
            "members": [SimpleNamespace(value=member) for member in row.get("members", [])],
        }
        fields[self._FIELD[self._kind]] = name
        return SimpleNamespace(**fields)

    def list(self, filter: str | None = None, attributes: str | None = None) -> list[Any]:  # noqa: A002
        self._estate["calls"].append(f"GET {self._kind}s?{filter or attributes or ''}")
        rows = self._items()
        if filter is not None:
            attribute, value = _filter(filter)
            assert attribute == self._ATTRIBUTE[self._kind], filter
            rows = [(name, row) for name, row in rows if name == value]
        return [self._record(name, row) for name, row in rows]

    def get(self, principal_id: str) -> Any:
        for name, row in self._items():
            if row["id"] == principal_id:
                return self._record(name, row)
        raise NotFound(f"{self._kind} {principal_id} not found")


class FakeWorkspace:
    """The read calls the pre-stop probes make, over ``estate``."""

    def __init__(self, estate: dict[str, Any]) -> None:
        self.estate = estate
        self.config = SimpleNamespace(
            host="https://workspace.example.invalid",
            authenticate=lambda: {"Authorization": "Bearer token-PLACEHOLDER"},
        )
        self.current_user = SimpleNamespace(me=self._me)
        self.users = _Scim(estate, "user")
        self.service_principals = _Scim(estate, "service_principal")
        self.groups = _Scim(estate, "group")
        self.apps = SimpleNamespace(
            get=self._app, get_permissions=self._app_permissions, stop=self._stop
        )
        self.genie = SimpleNamespace(list_spaces=self._spaces)
        self.api_client = SimpleNamespace(do=self._do)
        self.workspace = SimpleNamespace(get_status=lambda path: SimpleNamespace(object_id=5))
        self.secrets = SimpleNamespace(list_scopes=self._scopes)
        self.metastores = SimpleNamespace(current=self._metastore, get=self._metastore_get)
        self.catalogs = SimpleNamespace(get=self._catalog)
        self.schemas = SimpleNamespace(get=self._schema, list=self._schemas)
        self.tables = SimpleNamespace(get=self._table)
        self.grants = SimpleNamespace(get=self._grants)
        self.serving_endpoints = SimpleNamespace(
            list=self._endpoints, get=self._endpoint, get_permissions=self._endpoint_acl
        )

    def _me(self) -> Any:
        groups = [{"display": "admins"}]
        groups.extend(
            {"display": name, "value": row["id"]}
            for name, row in self.estate["principals"].items()
            if row["kind"] == "group" and "deployer-id" in row.get("members", [])
        )
        return SimpleNamespace(
            user_name=self.estate["deployer"],
            id="deployer-id",
            application_id="",
            groups=groups,
        )

    def _app(self, name: str) -> Any:
        self.estate["calls"].append(f"GET apps/{name}")
        app = self.estate["app"]
        if app is None or app["name"] != name:
            raise NotFound(f"App {name} does not exist")
        return SimpleNamespace(
            name=name,
            id=app["id"],
            service_principal_client_id=app["client_id"],
            service_principal_id=app["scim_id"],
            compute_status=SimpleNamespace(state=app["state"]),
            pending_deployment=SimpleNamespace(deployment_id="pending") if app.get("pending") else None,
            active_deployment=SimpleNamespace(
                deployment_id=app["active_deployment"], status=SimpleNamespace(state="SUCCEEDED")
            ),
        )

    def _app_permissions(self, name: str) -> Any:
        self._app(name)
        return _ns({"access_control_list": self.estate["app"]["acl"]})

    def _stop(self, name: str) -> None:
        self.estate["calls"].append(f"STOP apps/{name}")
        self.estate["app"]["state"] = "STOPPED"

    def _spaces(self, page_token: str | None = None) -> Any:
        assert page_token is None
        titles = self.estate.get("genie_titles", {})
        spaces = [
            SimpleNamespace(space_id=space, title=titles.get(space, "Untitled"))
            for space in self.estate["genie"]
        ]
        return SimpleNamespace(spaces=spaces, next_page_token=None)

    def _do(self, method: str, path: str, **_kwargs: Any) -> Any:
        self.estate["calls"].append(f"{method} {path}")
        assert method == "GET", f"a pre-stop probe attempted {method} {path}"
        space = path.rsplit("/", 1)[1]
        if space in self.estate["genie_errors"]:
            raise RuntimeError(f"PERMISSION_DENIED reading the ACL of {space}")
        # ApiClient.do returns the parsed JSON body: plain dicts, as here.
        return copy.deepcopy({"access_control_list": self.estate["genie"][space]})

    def _scopes(self) -> list[Any]:
        return [SimpleNamespace(name=name) for name in self.estate["secret_scopes"]]

    def _metastore(self) -> Any:
        return SimpleNamespace(metastore_id=self.estate["metastore"]["id"])

    def _metastore_get(self, metastore_id: str) -> Any:
        return SimpleNamespace(metastore_id=metastore_id, owner=self.estate["metastore"]["owner"])

    def _catalog(self, name: str) -> Any:
        if name not in self.estate["catalogs"]:
            raise NotFound(f"catalog {name} not found")
        return SimpleNamespace(name=name, owner=self.estate["catalogs"][name]["owner"])

    def _schema(self, full_name: str) -> Any:
        catalog, schema = full_name.split(".", 1)
        owner = self.estate["catalogs"].get(catalog, {}).get("schemas", {}).get(schema)
        if owner is None:
            raise NotFound(f"schema {full_name} not found")
        return SimpleNamespace(full_name=full_name, owner=owner)

    def _schemas(self, *, catalog_name: str, include_browse: bool = False) -> list[Any]:
        assert include_browse, "a complete schema inventory includes BROWSE-only schemas"
        if f"schemas/{catalog_name}" in self.estate["grants_errors"]:
            raise RuntimeError("PERMISSION_DENIED listing schemas")
        schemas = self.estate["catalogs"][catalog_name]["schemas"].items()
        # The API's visibility rule: every schema only for a metastore admin or the
        # catalog's owner (directly or through a group); anyone else sees its own.
        names = {self.estate["deployer"]} | {group["display"] for group in self._me().groups}
        owners = {self.estate["metastore"]["owner"], self.estate["catalogs"][catalog_name]["owner"]}
        if not names & owners:
            schemas = [(name, owner) for name, owner in schemas if owner == self.estate["deployer"]]
        return [
            SimpleNamespace(full_name=f"{catalog_name}.{schema}", owner=owner)
            for schema, owner in schemas
        ]

    def _table(self, full_name: str) -> Any:
        catalog, rest = full_name.split(".", 1)
        owner = self.estate["catalogs"].get(catalog, {}).get("tables", {}).get(rest)
        if owner is None:
            raise NotFound(f"table {full_name} not found")
        return SimpleNamespace(full_name=full_name, owner=owner)

    def _grants(self, securable_type: Any, full_name: str, **_kwargs: Any) -> Any:
        key = f"{getattr(securable_type, 'value', securable_type)}/{full_name}".lower()
        self.estate["calls"].append(f"GET grants/{key}")
        if key in self.estate["grants_errors"]:
            raise RuntimeError(f"PERMISSION_DENIED reading the grants of {key}")
        return _ns({"privilege_assignments": self.estate["grants"].get(key, [])})

    def _endpoints(self) -> list[Any]:
        return [SimpleNamespace(name=name) for name in self.estate["serving"]]

    def _endpoint(self, name: str) -> Any:
        row = self.estate["serving"].get(name)
        if row is None:
            raise NotFound(f"serving endpoint {name} not found")
        return SimpleNamespace(name=name, id=row["id"], creator=row.get("creator", ""))

    def _endpoint_acl(self, endpoint_id: str) -> Any:
        for row in self.estate["serving"].values():
            if row["id"] == endpoint_id:
                return _ns({"access_control_list": row["acl"]})
        raise NotFound(f"serving endpoint {endpoint_id} not found")


class FakeAccount:
    """Account SCIM over the same principal table (the owner policy's account reads)."""

    def __init__(self, estate: dict[str, Any], client_id: str = "account-client-EXAMPLE") -> None:
        self.config = SimpleNamespace(client_id=client_id)
        self.users = _Scim(estate, "user")
        self.service_principals = _Scim(estate, "service_principal")
        self.groups = _Scim(estate, "group")


def signed_contract(record: dict[str, Any]) -> Any:
    """The verified contract fields the probes read from a signed record."""

    return SimpleNamespace(
        deployment_id=record["deployment_id"],
        genie_space_id=record["genie_space_id"],
        gateway_endpoint=record["gateway_endpoint"],
        supervisor_endpoint=record["supervisor_endpoint"],
    )


def install(estate_path: Path, *, probes: bool) -> None:
    """Replace the SDK and, with ``probes``, the probes' seams with the estate at ``estate_path``.

    ``WorkspaceClient`` and ``AccountClient`` become the fakes. ``record`` scripts
    the signed-record verifier (a dict passes, a string raises that message);
    ``rebase_absence`` scripts the bootstrap absence proof; ``journal`` the
    first-install journal state; ``genie_pin`` the space the pin record names
    (``None``: the signed ``record``'s space when the record verifies, otherwise
    nothing pinned and no title match, so the provisioner creates one);
    ``genie_turn`` the Genie turn's terminal status
    (``BUDGET`` raises a budget error). After each process the document is written
    back, so a stop the probe was never meant to make is visible to the test.
    """

    import atexit

    import databricks.sdk

    estate = json.loads(estate_path.read_text(encoding="utf-8"))
    workspace = FakeWorkspace(estate)
    databricks.sdk.WorkspaceClient = lambda *_a, **_kw: workspace  # type: ignore[misc]
    atexit.register(
        lambda: estate_path.write_text(json.dumps(estate, indent=1), encoding="utf-8")
    )
    if not probes:
        return

    from tools.databricks import (
        app_first_install_journal,
        prestop_genie_budget,
        prestop_probe_suite,
        prestop_rollback_record,
        uc_owner_policy,
    )

    uc_owner_policy.AccountClient = (  # type: ignore[misc]
        lambda *_a, **kw: FakeAccount(estate, kw.get("client_id", ""))
    )

    def signed(_workspace: Any, **_kwargs: Any) -> Any:
        if isinstance(estate["record"], str):
            raise RuntimeError(estate["record"])
        return signed_contract(estate["record"])

    def absent(_workspace: Any, **_kwargs: Any) -> None:
        if estate["rebase_absence"] != "absent":
            raise RuntimeError(
                "a server-owned last-good App rollback record already exists; "
                "refusing the one-time unsigned rebase"
            )

    def turn(_client: Any, _question: str) -> Any:
        if estate["genie_turn"] == "BUDGET":
            raise RuntimeError("HTTP 429 from Genie API: BUDGET_EXCEEDED for this workspace")
        return SimpleNamespace(genie_status=estate["genie_turn"], answer_text="12,345 borrowers")

    prestop_rollback_record.ASSERT_SIGNED = signed  # type: ignore[assignment]
    prestop_rollback_record.ASSERT_ABSENT = absent  # type: ignore[assignment]
    app_first_install_journal.recovery_state = (  # type: ignore[assignment]
        lambda *_a, **_kw: estate["journal"]
    )
    def governed(_workspace: Any, **_kwargs: Any) -> Any:
        return scripted_resolution(estate)

    prestop_genie_budget.ASSERT = turn  # type: ignore[assignment]
    prestop_probe_suite.GOVERNED_SPACE = governed  # type: ignore[assignment]


def scripted_resolution(estate: dict[str, Any]) -> Any:
    """What ``genie_space_pin.resolve`` returns for ``estate`` (its seam in the probes)."""

    from tools.databricks.genie_space_pin import Resolution

    if estate.get("genie_pin"):
        return Resolution(action="use", space_id=estate["genie_pin"], source="pin")
    if isinstance(estate.get("record"), dict) and estate["app"] is not None:
        return Resolution(action="use", space_id=estate["record"]["genie_space_id"], source="record")
    return Resolution(action="create", space_id=None, source="none")


def write_estate(path: Path, estate: dict[str, Any]) -> Path:
    path.write_text(json.dumps(copy.deepcopy(estate), indent=1), encoding="utf-8")
    return path


def estate_from_env() -> Path:
    return Path(os.environ["MIP_TEST_PRESTOP_ESTATE"])
