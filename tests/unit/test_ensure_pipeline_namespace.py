from __future__ import annotations

from types import SimpleNamespace

import pytest
from databricks.sdk.errors import PermissionDenied, ResourceAlreadyExists, ResourceDoesNotExist

from tools.databricks import oauth_credential_creation
from tools.databricks.campaign_treatment_identity_probe import target_group_membership_probe
from tools.databricks.ensure_pipeline_namespace import ensure_pipeline_namespace


@pytest.fixture(autouse=True)
def _disable_credential_inventory_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        oauth_credential_creation,
        "_STABILITY_INTERVAL_SECONDS",
        0,
    )


def _catalog(
    *,
    owner: str = "deployer@example.com",
    catalog_type: str = "MANAGED_CATALOG",
) -> object:
    return SimpleNamespace(
        name="mip_customer",
        full_name="mip_customer",
        catalog_type=catalog_type,
        owner=owner,
        metastore_id="metastore-1",
    )


def _schema(
    *,
    owner: str = "deployer@example.com",
    metastore_id: str = "metastore-1",
) -> object:
    return SimpleNamespace(
        name="silver",
        full_name="mip_customer.silver",
        catalog_name="mip_customer",
        catalog_type="MANAGED_CATALOG",
        owner=owner,
        metastore_id=metastore_id,
    )


class _ObjectApi:
    def __init__(self, value: object | None, created: object, *, conflict: bool = False) -> None:
        self.value = value
        self.created = created
        self.conflict = conflict
        self.create_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def get(self, _name: str) -> object:
        if self.value is None:
            raise ResourceDoesNotExist("missing")
        return self.value

    def create(self, *args: object, **kwargs: object) -> object:
        self.create_calls.append((args, kwargs))
        if self.conflict:
            # Another creator won: the catalog is theirs, without this call's properties.
            self.value = self.created
            raise ResourceAlreadyExists("concurrent create")
        # The API stores the properties and the storage root sent in the create call.
        self.value = SimpleNamespace(
            **{**vars(self.created), "properties": kwargs.get("properties"), "storage_root": kwargs.get("storage_root")}
        )
        return self.value


class _DirectoryApi:
    def __init__(self, entries: list[object]) -> None:
        self.entries = entries

    def list(self, **_: object) -> list[object]:
        return list(self.entries)


def _mirrored_account(workspace: object) -> object:
    def users_list(**kwargs: object) -> list[object]:
        return [
            SimpleNamespace(
                id=getattr(item, "id", None),
                user_name=getattr(item, "user_name", None),
                active=True,
            )
            for item in workspace.users.list(**kwargs)
        ]

    def service_principals_list(**kwargs: object) -> list[object]:
        return [
            SimpleNamespace(
                id=getattr(item, "id", None),
                application_id=getattr(item, "application_id", None),
                active=True,
            )
            for item in workspace.service_principals.list(**kwargs)
        ]

    def group_get(group_id: str) -> object:
        matches = [
            item
            for item in workspace.groups.list()
            if getattr(item, "id", None) == group_id
        ]
        assert len(matches) == 1
        return matches[0]

    return SimpleNamespace(
        config=SimpleNamespace(client_id="account-client"),
        users=SimpleNamespace(list=users_list),
        service_principals=SimpleNamespace(list=service_principals_list),
        groups=SimpleNamespace(
            list=lambda **kwargs: workspace.groups.list(**kwargs),
            get=group_get,
        ),
    )


def _workspace(
    *,
    catalog: object | None = None,
    schema: object | None = None,
    catalog_conflict: bool = False,
    schema_conflict: bool = False,
    current_id: str = "deployer-id",
    users: list[object] | None = None,
    service_principals: list[object] | None = None,
    groups: list[object] | None = None,
    current_metastore_id: str = "metastore-1",
) -> tuple[object, _ObjectApi, _ObjectApi]:
    catalogs = _ObjectApi(catalog, _catalog(), conflict=catalog_conflict)
    schemas = _ObjectApi(schema, _schema(), conflict=schema_conflict)
    workspace = SimpleNamespace(
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(
                id=current_id,
                user_name="deployer@example.com",
                application_id=None,
            )
        ),
        config=SimpleNamespace(host="https://workspace.example"),
        metastores=SimpleNamespace(
            current=lambda: SimpleNamespace(metastore_id=current_metastore_id)
        ),
        catalogs=catalogs,
        schemas=schemas,
        users=_DirectoryApi(
            users
            if users is not None
            else [SimpleNamespace(id="deployer-id", user_name="deployer@example.com")]
        ),
        service_principals=_DirectoryApi(
            service_principals
            if service_principals is not None
            else [
                SimpleNamespace(
                    id="runtime-scim-id",
                    application_id="runtime-app-client",
                    active=True,
                )
            ]
        ),
        groups=_DirectoryApi(groups or []),
    )
    return workspace, catalogs, schemas


def _ensure(
    workspace: object,
    *,
    catalog: str = "mip_customer",
    approved: set[str] | None = None,
    forbidden: set[str] | None = None,
    account_factory: object | None = None,
    group_membership_probe: object | None = None,
) -> tuple[bool, bool]:
    kwargs: dict[str, object] = {
        "account_factory": account_factory or (lambda: _mirrored_account(workspace)),
        "assert_single_writer": lambda: None,
    }
    if group_membership_probe is not None:
        kwargs["group_membership_probe"] = group_membership_probe
    return ensure_pipeline_namespace(
        catalog=catalog,
        approved_owner_principals=approved,
        forbidden_owner_principals=forbidden or {"runtime-app-client"},
        workspace=workspace,  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


def test_fresh_namespace_creates_only_catalog_and_silver_schema() -> None:
    workspace, catalogs, schemas = _workspace()

    created = _ensure(workspace)

    assert created == (True, True)
    assert catalogs.create_calls == [
        (
            ("mip_customer",),
            {"comment": "Mortgage Intelligence Platform - Module 0 catalog."},
        )
    ]
    assert schemas.create_calls == [
        (
            ("silver", "mip_customer"),
            {"comment": "1:1 typed source-lift tables for Module 0."},
        )
    ]


def test_a_profile_storage_root_is_the_created_catalogs_managed_location() -> None:
    """M1-PR3: MIP_UC_CATALOG_STORAGE_ROOT reaches the create; an existing catalog is never altered."""

    workspace, catalogs, _schemas = _workspace()

    created = ensure_pipeline_namespace(
        catalog="mip_customer",
        forbidden_owner_principals={"runtime-app-client"},
        workspace=workspace,  # type: ignore[arg-type]
        account_factory=lambda: _mirrored_account(workspace),  # type: ignore[arg-type]
        assert_single_writer=lambda: None,
        storage_root="s3://bucket-alpha/mip",
    )

    assert created == (True, True)
    assert catalogs.create_calls == [
        (
            ("mip_customer",),
            {
                "comment": "Mortgage Intelligence Platform - Module 0 catalog.",
                "storage_root": "s3://bucket-alpha/mip",
            },
        )
    ]
    existing, catalogs, _schemas = _workspace(catalog=_catalog(), schema=_schema())
    assert ensure_pipeline_namespace(
        catalog="mip_customer",
        forbidden_owner_principals={"runtime-app-client"},
        workspace=existing,  # type: ignore[arg-type]
        account_factory=lambda: _mirrored_account(existing),  # type: ignore[arg-type]
        assert_single_writer=lambda: None,
        storage_root="s3://bucket-alpha/mip",
    ) == (False, False)
    assert catalogs.create_calls == []


def test_existing_exact_namespace_is_an_idempotent_noop() -> None:
    workspace, catalogs, schemas = _workspace(catalog=_catalog(), schema=_schema())

    created = _ensure(workspace)

    assert created == (False, False)
    assert catalogs.create_calls == []
    assert schemas.create_calls == []


def test_concurrent_creates_require_exact_authoritative_readback() -> None:
    workspace, _, _ = _workspace(catalog_conflict=True, schema_conflict=True)

    assert _ensure(workspace) == (False, False)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "mip.customer",
        "mip-customer",
        "`mip`",
        "MIP_CUSTOMER",
        "m" * 256,
    ],
)
def test_invalid_catalog_is_rejected_before_any_workspace_call(value: str) -> None:
    with pytest.raises(ValueError, match="Invalid catalog identifier"):
        ensure_pipeline_namespace(
            catalog=value,
            forbidden_owner_principals={"runtime-app-client"},
            workspace=object(),  # type: ignore[arg-type]
        )


def test_noncanonical_schema_is_rejected_before_any_workspace_call() -> None:
    with pytest.raises(ValueError, match="Invalid schema identifier"):
        ensure_pipeline_namespace(
            catalog="mip_customer",
            schema="Silver",
            forbidden_owner_principals={"runtime-app-client"},
            workspace=object(),  # type: ignore[arg-type]
        )


def test_foreign_catalog_is_rejected_before_schema_mutation() -> None:
    workspace, _, schemas = _workspace(catalog=_catalog(catalog_type="FOREIGN_CATALOG"))

    with pytest.raises(RuntimeError, match="managed Unity Catalog"):
        _ensure(workspace)

    assert schemas.create_calls == []


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("application_id", "RUNTIME-APP-CLIENT"),
        ("id", " runtime-scim-id "),
        ("active", False),
    ],
)
def test_noncanonical_forbidden_target_precedes_namespace_mutation(
    attribute: str,
    value: object,
) -> None:
    target = SimpleNamespace(
        id="runtime-scim-id",
        application_id="runtime-app-client",
        active=True,
    )
    setattr(target, attribute, value)
    workspace, catalogs, schemas = _workspace(service_principals=[target])

    with pytest.raises(RuntimeError, match="active workspace service principal"):
        _ensure(workspace)

    assert catalogs.create_calls == []
    assert schemas.create_calls == []


def test_unapproved_existing_owner_is_rejected() -> None:
    workspace, _, _ = _workspace(catalog=_catalog(owner="unexpected@example.com"), schema=_schema())

    with pytest.raises(RuntimeError, match="approved-owner contract"):
        _ensure(workspace)


def test_explicit_approved_group_owner_is_accepted() -> None:
    owner_group = SimpleNamespace(id="owner-group-id", display_name="mip-owners")
    workspace, _, _ = _workspace(
        catalog=_catalog(owner="mip-owners"),
        schema=_schema(owner="mip-owners"),
        groups=[owner_group],
    )
    account = SimpleNamespace(
        config=SimpleNamespace(client_id="account-client"),
        users=_DirectoryApi(
            [
                SimpleNamespace(
                    id="deployer-id",
                    user_name="deployer@example.com",
                    active=True,
                )
            ]
        ),
        service_principals=_DirectoryApi(
            [
                SimpleNamespace(
                    id="runtime-account-id",
                    application_id="runtime-app-client",
                    active=True,
                )
            ]
        ),
        groups=SimpleNamespace(
            list=lambda **_: [owner_group],
            get=lambda _id: owner_group,
        ),
    )

    assert _ensure(
        workspace,
        approved={"mip-owners"},
        account_factory=lambda: account,
        group_membership_probe=lambda *_: False,
    ) == (False, False)


def test_catalog_schema_metastore_mismatch_is_rejected() -> None:
    workspace, _, _ = _workspace(catalog=_catalog(), schema=_schema(metastore_id="metastore-2"))

    with pytest.raises(RuntimeError, match="current workspace metastore"):
        _ensure(workspace)


def test_current_workspace_metastore_is_authoritative() -> None:
    workspace, _, _ = _workspace(
        catalog=_catalog(),
        schema=_schema(),
        current_metastore_id="different-metastore",
    )

    with pytest.raises(RuntimeError, match="current workspace metastore"):
        _ensure(workspace)


def test_current_deployer_requires_immutable_identity() -> None:
    workspace, catalogs, _ = _workspace(current_id="")

    with pytest.raises(RuntimeError, match="immutable id"):
        _ensure(workspace)

    assert catalogs.create_calls == []


def test_current_deployer_name_must_resolve_to_same_immutable_id() -> None:
    workspace, catalogs, _ = _workspace(current_id="different-current-id")

    with pytest.raises(RuntimeError, match="different immutable principal"):
        _ensure(workspace)

    assert catalogs.create_calls == []


def test_configured_runtime_owner_is_forbidden_even_when_allowlisted() -> None:
    workspace, _, _ = _workspace(
        catalog=_catalog(),
        schema=_schema(owner="runtime-app-client"),
    )

    with pytest.raises(RuntimeError, match="cannot own governed UC objects"):
        _ensure(workspace, approved={"runtime-app-client"})


def test_existing_target_app_owner_is_explicitly_forbidden() -> None:
    target = SimpleNamespace(
        id="target-app-scim-id",
        application_id="target-app-client",
        active=True,
    )
    workspace, _, _ = _workspace(
        catalog=_catalog(owner="target-app-client"),
        schema=_schema(owner="target-app-client"),
        service_principals=[
            SimpleNamespace(
                id="runtime-scim-id",
                application_id="runtime-app-client",
                active=True,
            ),
            target,
        ],
    )

    with pytest.raises(RuntimeError, match="cannot own governed UC objects"):
        _ensure(
            workspace,
            approved={"target-app-client"},
            forbidden={"runtime-app-client", "target-app-client"},
        )


def test_ambiguous_configured_owner_resolution_is_rejected() -> None:
    ambiguous_user = SimpleNamespace(id="user-id", user_name="ambiguous-owner")
    ambiguous_sp = SimpleNamespace(id="sp-id", application_id="ambiguous-owner")
    workspace, _, _ = _workspace(
        catalog=_catalog(owner="ambiguous-owner"),
        schema=_schema(owner="ambiguous-owner"),
        users=[
            SimpleNamespace(id="deployer-id", user_name="deployer@example.com"),
            ambiguous_user,
        ],
        service_principals=[
            SimpleNamespace(
                id="runtime-scim-id",
                application_id="runtime-app-client",
                active=True,
            ),
            ambiguous_sp,
        ],
    )

    with pytest.raises(RuntimeError, match="exactly one principal"):
        _ensure(workspace, approved={"ambiguous-owner"})


def test_forbidden_group_membership_is_rejected_with_credential_proof() -> None:
    owner_group = SimpleNamespace(id="owner-group-id", display_name="mip-owners")
    workspace, _, _ = _workspace(
        catalog=_catalog(owner="mip-owners"),
        schema=_schema(owner="mip-owners"),
        groups=[owner_group],
    )
    account = SimpleNamespace(
        config=SimpleNamespace(client_id="account-client"),
        users=_DirectoryApi(
            [
                SimpleNamespace(
                    id="deployer-id",
                    user_name="deployer@example.com",
                    active=True,
                )
            ]
        ),
        service_principals=_DirectoryApi(
            [
                SimpleNamespace(
                    id="runtime-account-id",
                    application_id="runtime-app-client",
                    active=True,
                )
            ]
        ),
        groups=SimpleNamespace(
            list=lambda **_: [owner_group],
            get=lambda _id: owner_group,
        ),
    )

    with pytest.raises(RuntimeError, match="member of approved owner group"):
        _ensure(
            workspace,
            approved={"mip-owners"},
            account_factory=lambda: account,
            group_membership_probe=lambda *_: True,
        )


def test_inconclusive_identity_group_proof_precedes_namespace_mutation() -> None:
    owner_group = SimpleNamespace(id="owner-group-id", display_name="mip-owners")
    workspace, _, schemas = _workspace(
        catalog=_catalog(owner="mip-owners"),
        schema=None,
        groups=[owner_group],
    )

    class Secrets:
        def create(self, _sp_id: str, *, lifetime: str) -> object:
            assert lifetime == "300s"
            return SimpleNamespace(id="temporary-secret-id", secret="temporary-value")

        def delete(self, _sp_id: str, _secret_id: str) -> None:
            return None

    account = SimpleNamespace(
        config=SimpleNamespace(client_id="account-client"),
        users=_DirectoryApi(
            [
                SimpleNamespace(
                    id="deployer-id",
                    user_name="deployer@example.com",
                    active=True,
                )
            ]
        ),
        service_principals=_DirectoryApi(
            [
                SimpleNamespace(
                    id="runtime-account-id",
                    application_id="runtime-app-client",
                    active=True,
                )
            ]
        ),
        service_principal_secrets=Secrets(),
        groups=SimpleNamespace(
            list=lambda **_: [owner_group],
            get=lambda _id: owner_group,
        ),
    )

    def denied_probe(
        account_client: object,
        account_sp_id: str,
        application_id: str,
        group_id: str,
        group_name: str,
    ) -> bool:
        return target_group_membership_probe(
            account_client,  # type: ignore[arg-type]
            account_sp_id,
            application_id,
            group_id,
            group_name,
            expected_workspace_scim_id=account_sp_id,
            workspace_host="https://workspace.example",
            assert_single_writer=lambda: None,
            workspace_factory=lambda **_: SimpleNamespace(
                api_client=SimpleNamespace(
                    do=lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionDenied("denied"))
                )
            ),  # type: ignore[arg-type]
        )

    with pytest.raises(RuntimeError, match="membership proof failed"):
        _ensure(
            workspace,
            approved={"mip-owners"},
            account_factory=lambda: account,
            group_membership_probe=denied_probe,
        )

    assert schemas.create_calls == []


# ------------------------------------------------------------- install marker (M1-PR3)


def _ensure_install(
    workspace: object, *, install_key: str = "summit", storage_root: str | None = None
) -> tuple[bool, bool]:
    return ensure_pipeline_namespace(
        catalog="mip_customer",
        forbidden_owner_principals={"runtime-app-client"},
        workspace=workspace,  # type: ignore[arg-type]
        account_factory=lambda: _mirrored_account(workspace),  # type: ignore[arg-type]
        assert_single_writer=lambda: None,
        install_key=install_key,
        storage_root=storage_root,
    )


def test_a_created_catalog_carries_the_install_marker_from_its_create_call() -> None:
    workspace, catalogs, _schemas = _workspace()

    assert _ensure_install(workspace) == (True, True)

    assert catalogs.create_calls == [
        (
            ("mip_customer",),
            {
                "comment": "Mortgage Intelligence Platform - Module 0 catalog.",
                "properties": {"mip.install_key": "summit"},
            },
        )
    ]
    assert catalogs.value.properties == {"mip.install_key": "summit"}


def test_a_first_install_interrupted_after_the_catalog_create_passes_the_pre_lease_check() -> None:
    """The next run's install check reads the very catalog this create left behind."""

    from tests.fixtures.client_profiles import DEMO_PROFILE, IDENTITY_IDS
    from tests.fixtures.live_workspace import LiveAccount, LiveWorkspace, bundle_state
    from tools.clients import install_checks
    from tools.clients.environment import RawProfile, resolve

    profile = resolve(RawProfile(values=DEMO_PROFILE))
    workspace, catalogs, _schemas = _workspace()
    _ensure_install(workspace, install_key=profile.client, storage_root=profile.catalog_storage_root)
    # The catalog now exists: unmarked it would read as {} (exists), never as absent.
    left_behind = catalogs.value.properties or {}
    created_at = catalogs.value.storage_root
    account = LiveAccount(IDENTITY_IDS["account_principal"])
    warehouse_only = bundle_state(**{"sql_warehouses.mip_serverless_sql": "a0b1c2d3e4f5a6b7"})
    for facts in (
        {},  # stopped before the non-App bundle apply
        {"state": warehouse_only},  # after it, before the App create
        {"state": warehouse_only, "app": SimpleNamespace(name=profile.app_name), "journal": True},
    ):
        next_run = LiveWorkspace(
            DEMO_PROFILE,
            identity_ids=IDENTITY_IDS,
            catalog_properties=left_behind,
            catalog_storage_root=created_at,
            **facts,
        )
        assert install_checks.check(profile, next_run, lambda: account) == [], facts


def test_a_catalog_another_creator_won_refuses_before_the_schema() -> None:
    workspace, _catalogs, schemas = _workspace(catalog_conflict=True)

    with pytest.raises(RuntimeError, match="another creator won the create"):
        _ensure_install(workspace)

    assert schemas.create_calls == []


def test_an_existing_catalog_marked_for_another_install_refuses() -> None:
    marked = SimpleNamespace(**{**vars(_catalog()), "properties": {"mip.install_key": "another-client"}})
    workspace, catalogs, schemas = _workspace(catalog=marked)

    with pytest.raises(RuntimeError, match="for another install"):
        _ensure_install(workspace)

    assert catalogs.create_calls == [] and schemas.create_calls == []


@pytest.mark.parametrize("properties", [None, {}, {"mip.install_key": "summit"}])
def test_an_existing_catalog_unmarked_or_marked_for_this_install_is_never_altered(
    properties: dict[str, str] | None,
) -> None:
    existing = SimpleNamespace(**{**vars(_catalog()), "properties": properties})
    workspace, catalogs, _schemas = _workspace(catalog=existing, schema=_schema())

    # _ObjectApi has no update: any alteration of the catalog would fail here.
    assert _ensure_install(workspace) == (False, False)
    assert catalogs.create_calls == []


@pytest.mark.parametrize("value", ["", "Summit", "s", "summit_1", "-summit", "s" * 32])
def test_an_install_key_that_is_not_a_client_key_is_rejected_before_any_call(value: str) -> None:
    with pytest.raises(ValueError, match="Invalid install key"):
        ensure_pipeline_namespace(
            catalog="mip_customer",
            forbidden_owner_principals={"runtime-app-client"},
            workspace=object(),  # type: ignore[arg-type]
            install_key=value,
        )


def test_the_deploy_stamps_the_profiles_client_key() -> None:
    from tests.fixtures.deploy_script import deploy_entrypoint_text

    text = deploy_entrypoint_text()
    block = text[text.index("_PIPELINE_NAMESPACE_ARGS=(") :]
    block = block[: block.index(")")]
    assert '--install-key "$CLIENT_KEY"' in block
