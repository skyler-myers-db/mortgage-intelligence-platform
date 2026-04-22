"""Unit tests for ``tools/databricks/provision_m2m_oauth.py``.

The tool makes live Databricks + GitHub mutations, so these tests mock
out both sides and verify the *order + shape* of calls. We are not
exercising the SDK itself — we are pinning the contract the tool relies
on so a downstream SDK rename does not silently break the nightly setup.

Coverage:

* Argument parsing + ``--dry-run`` exits 0 without touching the SDK.
* Happy path: SP doesn't exist → create → grant CAN_USE → mint secret.
* Idempotent path: SP already exists → skip create, reuse, no mint
  unless ``--rotate``.
* ``--rotate`` on an existing SP mints a new secret.
* ``--set-gh-secrets`` invokes ``gh secret set`` with the secret on
  stdin (never argv), and emits ``::add-mask::`` ahead of the call.
* Admin-permission error paths surface a pointed SystemExit.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
# Load `tools/databricks/provision_m2m_oauth.py` directly via importlib
# rather than sys.path hacking. Prepending `tools/` to sys.path would
# shadow the real `databricks` SDK package (because `tools/databricks/`
# is also a Python package), breaking every test that invokes SDK code.
#
# test_provision_genie_space.py does `sys.path.insert(0, "tools")` and
# runs before us alphabetically in the same pytest session, which leaves
# a cached `databricks` module pointing at `tools/databricks/`. Purge
# that stale cache so our module's `from databricks.sdk...` imports
# resolve to the real SDK package.
_TOOLS_DIR = str(REPO_ROOT / "tools")
while _TOOLS_DIR in sys.path:
    sys.path.remove(_TOOLS_DIR)
for _cached in list(sys.modules):
    if _cached == "databricks" or _cached.startswith("databricks.") and "sdk" not in _cached:
        # Drop only the shadow entries ("databricks" root + our tools
        # subpackages), NEVER the real SDK submodules under
        # "databricks.sdk.*" that other tests may already have loaded.
        mod = sys.modules.get(_cached)
        if mod is not None and getattr(mod, "__file__", "") and _TOOLS_DIR in (
            getattr(mod, "__file__", "") or ""
        ):
            sys.modules.pop(_cached, None)
# If "databricks" itself is still the tools-shadow, drop it so the next
# `from databricks.sdk...` goes back through the finder.
_db = sys.modules.get("databricks")
if _db is not None and _TOOLS_DIR in (getattr(_db, "__file__", "") or ""):
    sys.modules.pop("databricks", None)

_PMO_PATH = REPO_ROOT / "tools" / "databricks" / "provision_m2m_oauth.py"
_MODNAME = "mip_provision_m2m_oauth"
_spec = importlib.util.spec_from_file_location(_MODNAME, _PMO_PATH)
assert _spec is not None and _spec.loader is not None
pmo = importlib.util.module_from_spec(_spec)
# Register in sys.modules BEFORE executing: the module uses @dataclass
# which consults sys.modules[cls.__module__] to resolve forward refs.
sys.modules[_MODNAME] = pmo
_spec.loader.exec_module(pmo)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Shared fixture: a mock WorkspaceClient that records every call.
# ---------------------------------------------------------------------------


def _make_client(
    *,
    existing_sp: SimpleNamespace | None = None,
    create_returns: SimpleNamespace | None = None,
    mint_secret_value: str = "dose_fake_secret_value",
) -> MagicMock:
    """Return a MagicMock shaped like the subset of WorkspaceClient we use."""
    client = MagicMock(name="WorkspaceClient")
    # service_principals.list(filter=...) -> iterable
    client.service_principals.list.return_value = iter(
        [existing_sp] if existing_sp is not None else []
    )
    # service_principals.create(...) -> a ServicePrincipal
    if create_returns is not None:
        client.service_principals.create.return_value = create_returns
    # service_principal_secrets_proxy.create(...) -> response with .secret
    client.service_principal_secrets_proxy.create.return_value = SimpleNamespace(
        id="secret-id-xyz",
        secret=mint_secret_value,
        secret_hash="deadbeef",
    )
    # apps.set_permissions(...) -> ignored
    client.apps.set_permissions.return_value = None
    return client


def _sp(display_name: str = "mip-nightly-ci-sp") -> SimpleNamespace:
    return SimpleNamespace(
        id="1234",
        application_id="app-id-abc",
        display_name=display_name,
    )


# ---------------------------------------------------------------------------
# CLI / arg-parse coverage
# ---------------------------------------------------------------------------


def test_help_works(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        pmo.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--sp-name" in out
    assert "--set-gh-secrets" in out
    assert "--rotate" in out


def test_dry_run_does_not_touch_sdk() -> None:
    """--dry-run must exit 0 without importing databricks-sdk at runtime."""
    # We use `sys.modules.pop("databricks.sdk", None)` trick would over-reach.
    # Instead, patch the WorkspaceClient factory at the point of use and
    # assert it was never called.
    with patch.object(pmo, "provision") as mock_provision:
        rc = pmo.main(["--dry-run", "--sp-name", "mip-test"])
    assert rc == 0
    mock_provision.assert_not_called()


def test_app_name_resolves_from_bundle() -> None:
    """Default app name should read mip-app out of databricks.yml."""
    # Hard-coded real repo value (databricks.yml resources.apps.mip_app.name).
    resolved = pmo._load_app_name_from_bundle()
    assert resolved == "mip-app"


def test_gh_repo_inference_shape() -> None:
    """Inferred repo matches owner/name from `git remote get-url origin`."""
    repo = pmo._infer_gh_repo()
    # On CI runners w/ actions/checkout@v4 the remote is set; locally the
    # repo under test has an origin. We don't pin owner/name because the
    # repo is renameable; we just require the shape.
    if repo is not None:
        assert "/" in repo
        assert " " not in repo


# ---------------------------------------------------------------------------
# Happy path: create new SP end-to-end
# ---------------------------------------------------------------------------


def test_happy_path_creates_sp_grants_and_mints() -> None:
    new_sp = _sp()
    client = _make_client(existing_sp=None, create_returns=new_sp)

    result = pmo.provision(
        sp_name="mip-nightly-ci-sp",
        app_name="mip-app",
        grant_can_use=True,
        gh_repo=None,
        set_gh_secrets=False,
        rotate=False,
        app_url="https://mip-app-test.aws.databricksapps.com",
        client_factory=lambda: client,
    )

    # 1) list called with SCIM filter
    client.service_principals.list.assert_called_once()
    _, list_kwargs = client.service_principals.list.call_args
    assert "filter" in list_kwargs
    assert "mip-nightly-ci-sp" in list_kwargs["filter"]

    # 2) create called once (SP didn't exist)
    client.service_principals.create.assert_called_once()

    # 3) set_permissions called with CAN_USE + service_principal_name=app-id
    client.apps.set_permissions.assert_called_once()
    _, perm_kwargs = client.apps.set_permissions.call_args
    assert perm_kwargs["app_name"] == "mip-app"
    acl = perm_kwargs["access_control_list"]
    assert len(acl) == 1
    assert acl[0].service_principal_name == "app-id-abc"
    # permission_level is the SDK enum; we compare by .value to avoid
    # importing the enum here just to type-match.
    assert getattr(acl[0].permission_level, "value", acl[0].permission_level) == "CAN_USE"

    # 4) mint secret called for fresh SP, returns the secret unchanged
    client.service_principal_secrets_proxy.create.assert_called_once_with(
        service_principal_id="1234"
    )

    assert result.created_sp is True
    assert result.granted_can_use is True
    assert result.client_id == "app-id-abc"
    assert result.client_secret == "dose_fake_secret_value"
    assert result.secret_written_to_gh is False  # gh opt-in not requested


# ---------------------------------------------------------------------------
# Idempotent path: SP already exists, no rotate → skip mint
# ---------------------------------------------------------------------------


def test_existing_sp_without_rotate_skips_mint() -> None:
    existing = _sp()
    client = _make_client(existing_sp=existing)

    result = pmo.provision(
        sp_name="mip-nightly-ci-sp",
        app_name="mip-app",
        grant_can_use=True,
        gh_repo=None,
        set_gh_secrets=False,
        rotate=False,
        app_url="https://example",
        client_factory=lambda: client,
    )

    client.service_principals.create.assert_not_called()
    client.service_principal_secrets_proxy.create.assert_not_called()
    # Grant is still applied — idempotent on the workspace side.
    client.apps.set_permissions.assert_called_once()

    assert result.created_sp is False
    assert result.client_secret is None


def test_existing_sp_with_rotate_mints_new_secret() -> None:
    existing = _sp()
    client = _make_client(existing_sp=existing, mint_secret_value="rotated_value")

    result = pmo.provision(
        sp_name="mip-nightly-ci-sp",
        app_name="mip-app",
        grant_can_use=True,
        gh_repo=None,
        set_gh_secrets=False,
        rotate=True,
        app_url="https://example",
        client_factory=lambda: client,
    )

    client.service_principals.create.assert_not_called()
    client.service_principal_secrets_proxy.create.assert_called_once_with(
        service_principal_id="1234"
    )
    assert result.created_sp is False
    assert result.client_secret == "rotated_value"


# ---------------------------------------------------------------------------
# --no-grant-can-use path skips the apps.set_permissions call.
# ---------------------------------------------------------------------------


def test_no_grant_can_use_skips_apps_set_permissions() -> None:
    new_sp = _sp()
    client = _make_client(existing_sp=None, create_returns=new_sp)

    result = pmo.provision(
        sp_name="mip-nightly-ci-sp",
        app_name="mip-app",
        grant_can_use=False,
        gh_repo=None,
        set_gh_secrets=False,
        rotate=False,
        app_url="https://example",
        client_factory=lambda: client,
    )

    client.apps.set_permissions.assert_not_called()
    assert result.granted_can_use is False


# ---------------------------------------------------------------------------
# gh secret upload
# ---------------------------------------------------------------------------


def test_set_gh_secrets_requires_gh_repo() -> None:
    new_sp = _sp()
    client = _make_client(existing_sp=None, create_returns=new_sp)

    with pytest.raises(SystemExit) as exc:
        pmo.provision(
            sp_name="mip-nightly-ci-sp",
            app_name="mip-app",
            grant_can_use=True,
            gh_repo=None,  # unresolved
            set_gh_secrets=True,
            rotate=False,
            app_url="https://example",
            client_factory=lambda: client,
        )
    assert "--gh-repo" in str(exc.value)


def test_set_gh_secrets_masks_and_pipes_via_stdin(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When gh upload is requested inside Actions: every call uses stdin +
    prints ``::add-mask::`` so downstream echoes are redacted. Outside
    Actions the mask directive is intentionally omitted — see the
    ``_local_run`` companion test below.
    """
    # Pretend we're inside a GitHub-hosted runner so the mask directive fires.
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    new_sp = _sp()
    client = _make_client(existing_sp=None, create_returns=new_sp, mint_secret_value="s3cr3t")

    calls: list[dict] = []

    def fake_run(cmd, input=None, check=True, capture_output=True, timeout=None, **kwargs):
        calls.append({"cmd": cmd, "input": input})
        # gh auth status path
        if cmd[:3] == ["gh", "auth", "status"]:
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        # gh secret set path
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    with patch.object(pmo.subprocess, "run", side_effect=fake_run), patch.object(
        pmo, "_which", return_value="/usr/local/bin/gh"
    ):
        result = pmo.provision(
            sp_name="mip-nightly-ci-sp",
            app_name="mip-app",
            grant_can_use=True,
            gh_repo="acme/repo",
            set_gh_secrets=True,
            rotate=False,
            app_url="https://mip-app-test.aws.databricksapps.com",
            client_factory=lambda: client,
        )

    # Separate the auth check from the secret writes.
    secret_calls = [c for c in calls if c["cmd"][:3] == ["gh", "secret", "set"]]
    assert len(secret_calls) == 3
    names = [c["cmd"][3] for c in secret_calls]
    assert set(names) == {"DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET", "MIP_APP_URL"}

    # Secret must be in stdin (bytes), never in argv.
    client_secret_call = next(c for c in secret_calls if c["cmd"][3] == "DATABRICKS_CLIENT_SECRET")
    assert client_secret_call["input"] == b"s3cr3t"
    assert "s3cr3t" not in " ".join(client_secret_call["cmd"])

    # Mask directive was emitted on stdout for every secret.
    out = capsys.readouterr().out
    assert out.count("::add-mask::") == 3


def test_set_gh_secrets_does_not_mask_outside_actions(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outside GitHub Actions the ``::add-mask::`` directive has no redact
    effect, so writing the plaintext secret even once to stdout is a
    leak vector (shell history, CI mirrors that aren't GHA, etc.).
    This test pins the Copilot 2026-04-22 fix: masking only inside
    Actions, plaintext never echoed on dev laptops.
    """
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

    new_sp = _sp()
    client = _make_client(existing_sp=None, create_returns=new_sp, mint_secret_value="s3cr3t")

    def fake_run(cmd, input=None, check=True, capture_output=True, timeout=None, **kwargs):
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    with patch.object(pmo.subprocess, "run", side_effect=fake_run), patch.object(
        pmo, "_which", return_value="/usr/local/bin/gh"
    ):
        pmo.provision(
            sp_name="mip-nightly-ci-sp",
            app_name="mip-app",
            grant_can_use=True,
            gh_repo="acme/repo",
            set_gh_secrets=True,
            rotate=False,
            app_url="https://mip-app-test.aws.databricksapps.com",
            client_factory=lambda: client,
        )

    out = capsys.readouterr().out
    # Belt-and-braces: no mask directive and no plaintext secret leak.
    assert "::add-mask::" not in out
    assert "s3cr3t" not in out


def test_set_gh_secrets_gracefully_skips_when_gh_unavailable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """gh not installed → warn, emit fallback commands, don't raise."""
    new_sp = _sp()
    client = _make_client(existing_sp=None, create_returns=new_sp)

    with patch.object(pmo, "_which", return_value=None):
        result = pmo.provision(
            sp_name="mip-nightly-ci-sp",
            app_name="mip-app",
            grant_can_use=True,
            gh_repo="acme/repo",
            set_gh_secrets=True,
            rotate=False,
            app_url="https://example",
            client_factory=lambda: client,
        )

    err = capsys.readouterr().err
    assert "gh CLI unavailable" in err
    assert "gh secret set DATABRICKS_CLIENT_ID" in err
    assert "gh secret set DATABRICKS_CLIENT_SECRET" in err
    assert result.secret_written_to_gh is False


# ---------------------------------------------------------------------------
# Admin permission errors surface a pointed message.
# ---------------------------------------------------------------------------


def test_admin_permission_error_points_at_runbook() -> None:
    client = _make_client(existing_sp=None, create_returns=_sp())
    client.service_principals.create.side_effect = PermissionError("403 Forbidden")

    with pytest.raises(SystemExit) as exc:
        pmo.provision(
            sp_name="mip-nightly-ci-sp",
            app_name="mip-app",
            grant_can_use=True,
            gh_repo=None,
            set_gh_secrets=False,
            rotate=False,
            app_url="https://example",
            client_factory=lambda: client,
        )
    message = str(exc.value)
    assert "requires workspace-admin auth" in message
    assert "docs/security/m2m-oauth-setup.md" in message


def test_app_not_found_prompts_bundle_deploy() -> None:
    new_sp = _sp()
    client = _make_client(existing_sp=None, create_returns=new_sp)
    client.apps.set_permissions.side_effect = RuntimeError("App mip-app NOT FOUND in workspace")

    with pytest.raises(SystemExit) as exc:
        pmo.provision(
            sp_name="mip-nightly-ci-sp",
            app_name="mip-app",
            grant_can_use=True,
            gh_repo=None,
            set_gh_secrets=False,
            rotate=False,
            app_url="https://example",
            client_factory=lambda: client,
        )
    assert "bundle deploy -t dev" in str(exc.value)


# ---------------------------------------------------------------------------
# Mint contract: a response missing `.secret` is a hard failure.
# ---------------------------------------------------------------------------


def test_mint_missing_secret_raises() -> None:
    new_sp = _sp()
    client = _make_client(existing_sp=None, create_returns=new_sp, mint_secret_value="")
    # Clear the .secret attribute by returning a namespace without it.
    client.service_principal_secrets_proxy.create.return_value = SimpleNamespace(
        id="x", secret=None
    )

    with pytest.raises(SystemExit) as exc:
        pmo.provision(
            sp_name="mip-nightly-ci-sp",
            app_name="mip-app",
            grant_can_use=True,
            gh_repo=None,
            set_gh_secrets=False,
            rotate=False,
            app_url="https://example",
            client_factory=lambda: client,
        )
    assert "no .secret" in str(exc.value)
