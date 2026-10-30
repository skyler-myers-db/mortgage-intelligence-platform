"""The read-only install checks (M1-PR3): each refusal with a fake SDK, before the lease.

tools/clients/install_checks.py runs inside ``python -m tools.clients.live_checks``
once the workspace and the deployer are the profile's: adoption, the
``mip.install_key`` install marker, the Cotality share (type, provider, share
and the seven tables), the storage root and the account principal's roles.
Every refusal exits 2 and names the check, never a value; a read error
refuses and never reads as absent; every call is a read. The deploy runs the
live checks before the signed lease and every App stop (asserted below on
the command-of-record text).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.errors import PermissionDenied

from tests.fixtures.client_profiles import DEMO_PROFILE, IDENTITY_IDS, demo_profile
from tests.fixtures.deploy_script import deploy_entrypoint_text
from tests.fixtures.live_workspace import LiveAccount, LiveWorkspace, bundle_state
from tools.clients import live_checks
from tools.clients.schema import FIELD_CLASS, FieldClass
from tools.databricks import bundle_state as state_module

CLIENT = DEMO_PROFILE["MIP_CLIENT"]
APP = DEMO_PROFILE["MIP_APP_NAME"]
WAREHOUSE = "a0b1c2d3e4f5a6b7"
_IDENTIFYING = sorted(
    {
        *(value for name, value in DEMO_PROFILE.items() if FIELD_CLASS[name] is FieldClass.IDENTIFYING),
        *IDENTITY_IDS.values(),
        WAREHOUSE,
    }
)


@pytest.fixture(autouse=True)
def _outside_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


def _run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    *,
    profile: dict[str, str] | None = None,
    account: LiveAccount | None = None,
    **workspace_facts: Any,
) -> tuple[int, str, LiveWorkspace, LiveAccount]:
    values = profile or DEMO_PROFILE
    workspace = LiveWorkspace(values, identity_ids=IDENTITY_IDS, **workspace_facts)
    principal = account or LiveAccount(IDENTITY_IDS["account_principal"])
    env_file = tmp_path / "client.env"
    env_file.write_text("".join(f"{name}='{value}'\n" for name, value in values.items()), encoding="utf-8")
    code = live_checks.main([], environ={}, env_file=env_file, client=lambda: workspace, account=lambda: principal)
    out = capsys.readouterr()
    text = out.out + out.err
    assert not any(value in text for value in _IDENTIFYING), "a refusal names checks, never values"
    assert all(call.startswith("GET") for call in workspace.calls + principal.calls), "read-only"
    return code, text, workspace, principal


def _refusals(text: str) -> list[str]:
    return [line.removeprefix("  - ") for line in text.splitlines() if line.startswith("  - ")]


def _adopted(**extra: Any) -> dict[str, Any]:
    """An install this deployer already holds: its App bound in its state, its catalog marked."""

    return {
        "app": SimpleNamespace(name=APP),
        "state": bundle_state(**{"apps.mip_app": APP, "sql_warehouses.mip_serverless_sql": WAREHOUSE}),
        "catalog_properties": {"mip.install_key": CLIENT},
        **extra,
    }


# ------------------------------------------------------------- passing installs


def test_a_first_install_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, text, workspace, account = _run(tmp_path, capsys)

    assert code == 0, text
    assert "adoption, install marker, Cotality share, storage root and account principal hold" in text
    assert workspace.calls[3:] == [
        "GET apps",
        "GET workspace/export (bundle state)",
        "GET catalogs (install)",
        "GET catalogs (share)",
        *["GET tables/exists"] * 7,
    ], "the profile sets MIP_UC_CATALOG_STORAGE_ROOT, so no metastore summary is read"
    assert account.calls == ["GET accounts/scim/ServicePrincipals (id,applicationId,roles)"]


@pytest.mark.parametrize("marker", [{"mip.install_key": CLIENT}, {}, None])
def test_an_adopted_install_passes_with_its_marker_or_before_one_is_written(
    marker: dict[str, str] | None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    facts = _adopted(catalog_properties=marker)
    code, text, workspace, _account = _run(tmp_path, capsys, **facts)

    assert code == 0, text
    assert "GET workspace/get-status (first-install journal)" not in workspace.calls


# ------------------------------------------------------------- 1. adoption


@pytest.mark.parametrize(
    "state",
    [None, bundle_state(), bundle_state(**{"apps.mip_app": "another-app"})],
    ids=["no-state", "state-without-the-app", "state-binding-another-app"],
)
def test_an_existing_app_this_deployer_never_adopted_refuses(
    state: dict[str, Any] | None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, text, _workspace, _account = _run(tmp_path, capsys, app=SimpleNamespace(name=APP), state=state)

    assert code == 2
    assert _refusals(text) == [
        "adoption: the App MIP_APP_NAME names exists, but this deployer's bundle state does not bind "
        "apps.mip_app to it: an existing install this deployer never adopted (adopt it into this "
        "deployer's state first, plan M2-PR6)"
    ]


def test_an_unbound_app_a_first_install_journal_may_own_is_left_to_the_plan_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The pre-lease bundle plan gate verifies the signed journal and refuses not_adopted otherwise."""

    code, text, workspace, _account = _run(tmp_path, capsys, app=SimpleNamespace(name=APP), journal=True)

    assert code == 0, text
    assert "GET workspace/get-status (first-install journal)" in workspace.calls


@pytest.mark.parametrize(
    "body",
    [b"not json", bundle_state() | {"state_version": 3}, {"state_version": 2}, bundle_state() | {"state": []}],
    ids=["not-json", "unknown-version", "no-state-object", "state-not-an-object"],
)
def test_a_state_document_that_is_not_the_reviewed_shape_refuses(
    body: object, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, text, _workspace, _account = _run(tmp_path, capsys, app=SimpleNamespace(name=APP), state=body)

    assert code == 2
    assert any(line.startswith("adoption: could not be read (BundleStateError)") for line in _refusals(text))


def test_a_state_entry_without_a_live_id_refuses() -> None:
    document = bundle_state(**{"apps.mip_app": APP})
    document["state"]["resources.apps.mip_app"]["__id__"] = ""

    with pytest.raises(state_module.BundleStateError, match="has no live id"):
        state_module.bound_id(document, state_module.APP_KEY)


# ------------------------------------------------------------- 2. install marker


def test_a_marker_for_another_install_refuses_even_when_adopted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    facts = _adopted(catalog_properties={"mip.install_key": "another-client"})
    code, text, _workspace, _account = _run(tmp_path, capsys, **facts)

    assert code == 2
    assert _refusals(text) == [
        "install marker: MIP_DEFAULT_CATALOG carries mip.install_key for another install than MIP_CLIENT; "
        "refusing to deploy into another install's catalog"
    ]


def test_an_unmarked_catalog_with_no_app_in_this_deployers_state_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, text, _workspace, _account = _run(tmp_path, capsys, catalog_properties={})

    assert code == 2
    assert _refusals(text) == [
        "install marker: MIP_DEFAULT_CATALOG exists without mip.install_key, and this deployer's state "
        "binds no App: a catalog this install's deploy did not create, which CREATE CATALOG IF NOT "
        "EXISTS would silently reuse (an install from before the marker: adopt it first, plan M2-PR6; "
        "any other catalog: set another MIP_DEFAULT_CATALOG)"
    ]


def test_a_matching_marker_needs_no_app() -> None:
    """The marker alone proves the catalog is this install's (an App not yet created)."""

    workspace = LiveWorkspace(DEMO_PROFILE, identity_ids=IDENTITY_IDS, catalog_properties={"mip.install_key": CLIENT})
    from tools.clients import install_checks
    from tools.clients.environment import RawProfile, resolve

    profile = resolve(RawProfile(values=DEMO_PROFILE))
    assert install_checks.check(profile, workspace, lambda: LiveAccount(IDENTITY_IDS["account_principal"])) == []


# ------------------------------------------------------------- 3. Cotality share


@pytest.mark.parametrize(
    ("share", "refusal"),
    [
        (None, "Cotality share: the source catalog MIP_COTALITY_SOURCE_CATALOG names is absent"),
        (
            SimpleNamespace(
                catalog_type=SimpleNamespace(value="MANAGED_CATALOG"),
                provider_name=DEMO_PROFILE["MIP_COTALITY_SHARE_PROVIDER"],
                share_name=DEMO_PROFILE["MIP_COTALITY_SHARE_NAME"],
            ),
            "Cotality share: the source catalog is not a Delta Sharing catalog (DELTASHARING_CATALOG)",
        ),
        (
            SimpleNamespace(
                catalog_type="DELTASHARING_CATALOG",
                provider_name="another-provider",
                share_name=DEMO_PROFILE["MIP_COTALITY_SHARE_NAME"],
            ),
            "Cotality share: the source catalog is not mounted from MIP_COTALITY_SHARE_PROVIDER",
        ),
        (
            SimpleNamespace(
                catalog_type="DELTASHARING_CATALOG",
                provider_name=DEMO_PROFILE["MIP_COTALITY_SHARE_PROVIDER"],
                share_name="another-share",
            ),
            "Cotality share: the source catalog is not mounted from MIP_COTALITY_SHARE_NAME",
        ),
    ],
    ids=["absent", "not-a-share", "other-provider", "other-share"],
)
def test_a_share_catalog_that_is_not_the_profiles_refuses(
    share: object, refusal: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, text, _workspace, _account = _run(tmp_path, capsys, share=share)

    assert code == 2
    assert refusal in _refusals(text)


def test_an_unset_provider_and_share_are_not_asserted(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    profile = demo_profile(MIP_COTALITY_SHARE_PROVIDER=None, MIP_COTALITY_SHARE_NAME=None)
    share = SimpleNamespace(catalog_type="DELTASHARING_CATALOG", provider_name="any", share_name="any")

    code, text, _workspace, _account = _run(tmp_path, capsys, profile=profile, share=share)

    assert code == 0, text


def test_missing_share_tables_are_named_by_stem(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, text, _workspace, _account = _run(
        tmp_path, capsys, missing_tables=("mls_listing_v1", "owner_transfer_domain_v1")
    )

    assert code == 2
    assert _refusals(text) == [
        "Cotality share: 2 of 7 source tables do not resolve: mls_listing_v1, owner_transfer_domain_v1"
    ]


def test_the_canonical_share_names_are_checked_when_the_profile_sets_none(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    profile = demo_profile(
        MIP_COTALITY_SOURCE_CATALOG=None, MIP_COTALITY_SOURCE_SCHEMA=None, MIP_COTALITY_SOURCE_TABLE_PREFIX=None
    )

    code, text, _workspace, _account = _run(tmp_path, capsys, profile=profile)

    assert code == 0, text


# ------------------------------------------------------------- 4. storage root


def test_a_first_install_needs_a_metastore_root_or_a_configured_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    profile = demo_profile(MIP_UC_CATALOG_STORAGE_ROOT=None)

    code, text, workspace, _account = _run(tmp_path, capsys, profile=profile)
    assert code == 0, text
    assert "GET metastore_summary" in workspace.calls

    code, text, _workspace, _account = _run(tmp_path, capsys, profile=profile, metastore_root="")
    assert code == 2
    assert _refusals(text) == [
        "storage root: the metastore has no default storage root and MIP_UC_CATALOG_STORAGE_ROOT is "
        "unset, so the first install could not create MIP_DEFAULT_CATALOG; set it"
    ]


def test_an_existing_catalog_needs_no_storage_root(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    profile = demo_profile(MIP_UC_CATALOG_STORAGE_ROOT=None)

    code, text, workspace, _account = _run(tmp_path, capsys, profile=profile, metastore_root="", **_adopted())

    assert code == 0, text
    assert "GET metastore_summary" not in workspace.calls


@pytest.mark.parametrize(
    "actual",
    [None, "s3://another-bucket/mip", "gs://another-bucket/mip"],
    ids=["metastore-root", "another-root", "another-cloud"],
)
def test_an_existing_catalog_elsewhere_than_the_configured_root_refuses(
    actual: str | None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert DEMO_PROFILE["MIP_UC_CATALOG_STORAGE_ROOT"], "positive control: the profile sets a root"

    code, text, workspace, _account = _run(tmp_path, capsys, catalog_storage_root=actual, **_adopted())

    assert code == 2
    assert _refusals(text) == [
        "storage root: MIP_DEFAULT_CATALOG exists with another storage root than MIP_UC_CATALOG_STORAGE_ROOT; "
        "a catalog's managed location never moves, so set the catalog's own root or unset the field"
    ]
    assert "GET metastore_summary" not in workspace.calls


def test_an_existing_catalog_at_the_configured_root_passes_a_trailing_slash_aside(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = DEMO_PROFILE["MIP_UC_CATALOG_STORAGE_ROOT"].rstrip("/")
    for actual in (root, root + "/"):
        code, text, _workspace, _account = _run(tmp_path, capsys, catalog_storage_root=actual, **_adopted())
        assert code == 0, text


# ------------------------------------------------------------- 5. account principal


@pytest.mark.parametrize(
    ("account", "refusal"),
    [
        (
            LiveAccount(IDENTITY_IDS["account_principal"], roles=("account_admin",)),
            "MIP_IDENTITY_IDS_JSON.account_principal: holds account admin; it may manage the reserved M2M "
            "identities only (remove the role in the account console)",
        ),
        (
            LiveAccount(IDENTITY_IDS["account_principal"], matches=0),
            "MIP_IDENTITY_IDS_JSON.account_principal: no single account service principal has that application id",
        ),
        (
            LiveAccount(IDENTITY_IDS["account_principal"], matches=2),
            "MIP_IDENTITY_IDS_JSON.account_principal: no single account service principal has that application id",
        ),
    ],
    ids=["account-admin", "absent", "ambiguous"],
)
def test_an_account_principal_that_is_an_admin_or_unresolved_refuses(
    account: LiveAccount, refusal: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, text, _workspace, _account = _run(tmp_path, capsys, account=account)

    assert code == 2
    assert _refusals(text) == [refusal]


def test_other_account_roles_are_not_admin(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    account = LiveAccount(IDENTITY_IDS["account_principal"], roles=("marketplace_admin",))

    code, text, _workspace, _account = _run(tmp_path, capsys, account=account)

    assert code == 0, text


# ------------------------------------------------------------- fail closed


@pytest.mark.parametrize(
    ("call", "check"),
    [
        ("GET apps", "adoption"),
        ("GET workspace/export (bundle state)", "adoption"),
        ("GET catalogs (install)", "install marker"),
        ("GET catalogs (share)", "Cotality share"),
        ("GET tables/exists", "Cotality share table property_domain_v3"),
        ("GET metastore_summary", "storage root"),
    ],
)
def test_a_read_error_refuses_and_never_reads_as_absent(
    call: str, check: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    profile = demo_profile(MIP_UC_CATALOG_STORAGE_ROOT=None)
    denied = PermissionDenied(f"denied for {IDENTITY_IDS['deployer']}")

    code, text, _workspace, _account = _run(tmp_path, capsys, profile=profile, fail={call: denied})

    assert code == 2
    assert f"{check}: could not be read (PermissionDenied); refusing rather than reading it as absent" in _refusals(
        text
    )


def test_an_unreadable_account_principal_refuses(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def denied() -> Any:
        raise PermissionDenied("not allowed to list service principals")

    workspace = LiveWorkspace(DEMO_PROFILE, identity_ids=IDENTITY_IDS)
    env_file = tmp_path / "client.env"
    env_file.write_text("".join(f"{name}='{value}'\n" for name, value in DEMO_PROFILE.items()), encoding="utf-8")

    code = live_checks.main([], environ={}, env_file=env_file, client=lambda: workspace, account=denied)

    assert code == 2
    assert "account principal: could not be read (PermissionDenied)" in capsys.readouterr().err


def test_every_install_refusal_is_reported_together(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, text, _workspace, _account = _run(
        tmp_path,
        capsys,
        profile=demo_profile(MIP_UC_CATALOG_STORAGE_ROOT=None),
        account=LiveAccount(IDENTITY_IDS["account_principal"], roles=("account_admin",)),
        app=SimpleNamespace(name=APP),
        catalog_properties={"mip.install_key": "another-client"},
        share=None,
    )

    assert code == 2
    assert [line.split(":", 1)[0] for line in _refusals(text)] == [
        "adoption",
        "install marker",
        "Cotality share",
        "MIP_IDENTITY_IDS_JSON.account_principal",
    ]


def test_no_install_read_is_made_for_a_workspace_that_is_not_the_profiles(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, text, workspace, account = _run(tmp_path, capsys, workspace_id="4200000099")

    assert code == 2
    assert workspace.calls == ["GET scim/Me (org id)", "GET current-metastore-assignment", "GET scim/Me"]
    assert account.calls == []


def test_under_actions_the_account_principals_scim_id_is_masked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    workspace = LiveWorkspace(DEMO_PROFILE, identity_ids=IDENTITY_IDS)
    env_file = tmp_path / "client.env"
    env_file.write_text("".join(f"{name}='{value}'\n" for name, value in DEMO_PROFILE.items()), encoding="utf-8")
    account = LiveAccount(IDENTITY_IDS["account_principal"])

    assert live_checks.main([], environ={}, env_file=env_file, client=lambda: workspace, account=lambda: account) == 0
    masks = {line.removeprefix("::add-mask::") for line in capsys.readouterr().err.splitlines() if "::add-mask::" in line}
    assert "90000000000000" in masks


# ------------------------------------------------------------- before the lease


def test_the_deploy_runs_the_live_checks_before_the_lease_and_every_app_stop() -> None:
    text = deploy_entrypoint_text()
    checks = text.index('"$PYTHON" -m tools.clients.live_checks; then')

    assert checks < text.index("run_bundle_plan_gate pre-lease")
    assert checks < text.index("-m tools.databricks.app_deployment_lease acquire")
    # Every App stop runs from step 0a on (the lifecycle libraries above only define functions).
    assert checks < text.index("# Step 0a: prove signed-blue state before any workspace mutation")
