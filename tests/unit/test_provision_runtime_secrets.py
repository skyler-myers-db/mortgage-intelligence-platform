from __future__ import annotations

import base64
import hashlib
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tools.databricks import provision_runtime_secrets as subject


def test_direct_execution_resolves_repository_imports() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(subject.REPO / "tools/databricks/provision_runtime_secrets.py"),
            "--help",
        ],
        cwd="/tmp",
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Provision Databricks Secret keys" in completed.stdout
    assert "--check" in completed.stdout


def _test_secret(label: str) -> str:
    """A distinct, high-entropy synthetic value (43 URL-safe base64 characters).

    Low-entropy fixtures (``label-xxxx…``) let a suffix or middle-slice leak
    pass the window check below; these share no 8-character window.
    """

    digest = hashlib.sha256(f"mip-test-runtime-secret:{label}".encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


MASK_SECRET = _test_secret("mask")
OTHER_MASK_SECRET = _test_secret("other-mask")
CURRENT_SECRET = _test_secret("current")
OTHER_CURRENT_SECRET = _test_secret("other-current")
PREVIOUS_SECRET = _test_secret("previous")
ALL_TEST_SECRETS = (
    MASK_SECRET,
    OTHER_MASK_SECRET,
    CURRENT_SECRET,
    OTHER_CURRENT_SECRET,
    PREVIOUS_SECRET,
)
LEAK_WINDOW = 8
INSTALL_HOST = "https://dbc-synthetic-install.cloud.databricks.com"
OTHER_HOST = "https://dbc-synthetic-other.cloud.databricks.com"
DEFAULT_WRITTEN_KEYS = tuple(subject.ENV_TO_KEY.values())
SALESFORCE_KEYS = (
    "salesforce-client-secret",
    "salesforce-password",
    "salesforce-security-token",
)


@pytest.fixture(autouse=True)
def _hermetic_secret_config(monkeypatch) -> None:
    monkeypatch.setattr(subject, "ENV_LOCAL", subject.REPO / ".missing-test-env")
    for name in (
        *subject.ENV_TO_KEY,
        subject.PREVIOUS_KID_ENV,
        subject.HOST_ENV,
        subject.GITHUB_ACTIONS_ENV,
        "DATABRICKS_CONFIG_PROFILE",
    ):
        monkeypatch.delenv(name, raising=False)
    # deploy.sh exports the host it bound the deployer to; CLI tests resolve it.
    monkeypatch.setenv(subject.DEPLOY_HOST_ENV, INSTALL_HOST)


class _Secrets:
    def __init__(
        self,
        *,
        scope_exists: bool = False,
        keys: tuple[str, ...] = (),
        stored: dict[str, str] | None = None,
    ) -> None:
        self.scope_exists = scope_exists or bool(stored)
        self.values: dict[str, str | None] = {key: None for key in keys}
        self.values.update(stored or {})
        self.created_scopes: list[str] = []
        self.gets: list[str] = []
        self.puts: list[tuple[str, str, str]] = []
        self.deletes: list[tuple[str, str]] = []

    @property
    def keys(self) -> set[str]:
        return set(self.values)

    def list_scopes(self):
        return [SimpleNamespace(name=subject.DEFAULT_SCOPE)] if self.scope_exists else []

    def create_scope(self, *, scope: str) -> None:
        assert scope == subject.DEFAULT_SCOPE
        self.created_scopes.append(scope)
        self.scope_exists = True

    def list_secrets(self, *, scope: str):
        assert scope == subject.DEFAULT_SCOPE
        return [SimpleNamespace(key=key) for key in sorted(self.values)]

    def get_secret(self, *, scope: str, key: str):
        assert scope == subject.DEFAULT_SCOPE
        self.gets.append(key)
        value = self.values[key]
        assert value is not None, f"test stored no value for {key}"
        return SimpleNamespace(key=key, value=base64.b64encode(value.encode()).decode())

    def put_secret(self, *, scope: str, key: str, string_value: str) -> None:
        self.puts.append((scope, key, string_value))
        self.values[key] = string_value

    def delete_secret(self, *, scope: str, key: str) -> None:
        self.deletes.append((scope, key))
        self.values.pop(key, None)


def _client(secrets_api: _Secrets, host: str | None = INSTALL_HOST) -> SimpleNamespace:
    return SimpleNamespace(secrets=secrets_api, config=SimpleNamespace(host=host))


def _set_inputs(monkeypatch, *, mask: str = MASK_SECRET) -> None:
    """The optional first-install inputs for the mask and the current key."""

    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", mask)
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_CURRENT", CURRENT_SECRET)


def _put_keys(secrets_api: _Secrets) -> list[str]:
    return [key for _scope, key, _value in secrets_api.puts]


def _assert_names_only(text: str) -> None:
    """No configured value, in any 8-character slice, and no hex run of any kind."""

    assert not re.search(r"[0-9a-fA-F]{16,}", text), "no hex value of any kind"
    for value in ALL_TEST_SECRETS:
        for start in range(len(value) - LEAK_WINDOW + 1):
            window = value[start : start + LEAK_WINDOW]
            assert window not in text, f"an {LEAK_WINDOW}-character slice of a value leaked"


def test_a_first_install_writes_the_configured_inputs_and_no_previous_by_default(
    monkeypatch,
) -> None:
    _set_inputs(monkeypatch)
    secrets_api = _Secrets()

    result = subject.provision_runtime_secrets(client=_client(secrets_api))

    assert secrets_api.scope_exists is True
    assert result.written == DEFAULT_WRITTEN_KEYS
    assert result.kept == ()
    assert _put_keys(secrets_api) == list(DEFAULT_WRITTEN_KEYS)
    disabled_value = next(
        value for _scope, key, value in secrets_api.puts if key == "genie-action-previous"
    )
    assert disabled_value.startswith(subject.DISABLED_PREVIOUS_PREFIX)
    assert disabled_value not in {MASK_SECRET, CURRENT_SECRET}
    assert (subject.DEFAULT_SCOPE, "cotality-id-mask-v1", MASK_SECRET) in secrets_api.puts
    assert (subject.DEFAULT_SCOPE, "genie-action-current", CURRENT_SECRET) in secrets_api.puts
    assert secrets_api.deletes == []
    assert secrets_api.gets == [], "presence only: no value is read"


def test_provisions_previous_only_for_explicit_rotation_grace(monkeypatch) -> None:
    _set_inputs(monkeypatch)
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_PREVIOUS", PREVIOUS_SECRET)
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_PREVIOUS_KID", "v1")
    secrets_api = _Secrets()

    result = subject.provision_runtime_secrets(client=_client(secrets_api))

    assert result.written == DEFAULT_WRITTEN_KEYS
    assert (
        subject.DEFAULT_SCOPE,
        "genie-action-previous",
        PREVIOUS_SECRET,
    ) in secrets_api.puts


def test_an_existing_previous_key_is_kept_when_grace_is_not_configured(monkeypatch) -> None:
    """A deploy no longer re-disables it: retirement is the explicit --retire-previous."""
    _set_inputs(monkeypatch)
    secrets_api = _Secrets(
        scope_exists=True,
        keys=("genie-action-previous",),
    )

    result = subject.provision_runtime_secrets(client=_client(secrets_api))

    assert result.kept == ("genie-action-previous",)
    assert result.written == tuple(key for key in DEFAULT_WRITTEN_KEYS if key != "genie-action-previous")
    assert "genie-action-previous" not in _put_keys(secrets_api)
    assert secrets_api.values["genie-action-previous"] is None, "never touched"
    assert secrets_api.deletes == [] and secrets_api.gets == []


def test_explicit_retire_previous_does_not_require_other_secret_values() -> None:
    secrets_api = _Secrets(
        scope_exists=True,
        keys=("genie-action-previous",),
    )

    existed = subject.retire_previous_secret(client=_client(secrets_api))

    assert existed is True
    assert secrets_api.puts[-1][1] == "genie-action-previous"
    assert secrets_api.puts[-1][2].startswith(subject.DISABLED_PREVIOUS_PREFIX)
    assert secrets_api.deletes == []


def test_explicit_retire_creates_disabled_binding_when_scope_is_absent() -> None:
    secrets_api = _Secrets()

    existed = subject.retire_previous_secret(client=_client(secrets_api))

    assert existed is False
    assert secrets_api.scope_exists is True
    assert secrets_api.puts[-1][1] == "genie-action-previous"
    assert secrets_api.puts[-1][2].startswith(subject.DISABLED_PREVIOUS_PREFIX)


def test_a_placeholder_input_counts_as_unset_so_the_absent_key_is_generated(monkeypatch) -> None:
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", "REDACTED")
    secrets_api = _Secrets()

    subject.provision_runtime_secrets(client=_client(secrets_api))

    mask = next(value for _scope, key, value in secrets_api.puts if key == "cotality-id-mask-v1")
    assert re.fullmatch(r"[0-9a-f]{64}", mask)


def test_rejects_present_but_weak_runtime_secret(monkeypatch) -> None:
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", "x")
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_CURRENT", CURRENT_SECRET)

    with pytest.raises(ValueError, match="at least 32"):
        subject.provision_runtime_secrets(client=_client(_Secrets()))


def test_rejects_duplicate_rotation_values(monkeypatch) -> None:
    _set_inputs(monkeypatch)
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_PREVIOUS", CURRENT_SECRET)
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_PREVIOUS_KID", "v1")

    with pytest.raises(ValueError, match="must differ"):
        subject.provision_runtime_secrets(client=_client(_Secrets()))


def test_rejects_previous_secret_without_explicit_rotation_kid(monkeypatch) -> None:
    _set_inputs(monkeypatch)
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_PREVIOUS", PREVIOUS_SECRET)

    with pytest.raises(ValueError, match="MIP_GENIE_ACTION_SECRET_PREVIOUS_KID"):
        subject.provision_runtime_secrets(client=_client(_Secrets()))


def test_provisions_optional_salesforce_credentials_without_logging_values(monkeypatch) -> None:
    _set_inputs(monkeypatch)
    monkeypatch.setenv("SALESFORCE_CLIENT_SECRET", "sf-client-secret")
    monkeypatch.setenv("SALESFORCE_PASSWORD", "sf-password")
    monkeypatch.setenv("SALESFORCE_SECURITY_TOKEN", "sf-token")
    secrets_api = _Secrets()

    subject.provision_runtime_secrets(client=_client(secrets_api))

    assert (subject.DEFAULT_SCOPE, "salesforce-client-secret", "sf-client-secret") in secrets_api.puts
    assert (subject.DEFAULT_SCOPE, "salesforce-password", "sf-password") in secrets_api.puts
    assert (subject.DEFAULT_SCOPE, "salesforce-security-token", "sf-token") in secrets_api.puts


# ------------------------------------------------- the Cotality ID mask (presence only)


@pytest.mark.parametrize("configured", (MASK_SECRET, OTHER_MASK_SECRET), ids=("same-input", "other-input"))
def test_an_existing_mask_is_kept_unread_whatever_the_input(monkeypatch, configured: str) -> None:
    """Re-keying would orphan every stored B-... id; a deploy never reads or rewrites the mask."""
    _set_inputs(monkeypatch, mask=configured)
    secrets_api = _Secrets(stored={"cotality-id-mask-v1": MASK_SECRET})

    result = subject.provision_runtime_secrets(client=_client(secrets_api))

    assert secrets_api.gets == [], "the stored mask is never read"
    assert "cotality-id-mask-v1" not in _put_keys(secrets_api)
    assert secrets_api.values["cotality-id-mask-v1"] == MASK_SECRET
    assert result.kept == ("cotality-id-mask-v1",)
    assert result.written == tuple(key for key in DEFAULT_WRITTEN_KEYS if key != "cotality-id-mask-v1")
    assert (
        "cotality-id-mask-v1: kept unread; MIP_COTALITY_ID_MASK_SECRET is set but never replaces "
        "an existing key"
    ) in result.notes
    assert secrets_api.created_scopes == []


# ------------------------------------------------- existing Salesforce keys


def test_existing_salesforce_keys_are_kept_when_their_inputs_are_unset(monkeypatch) -> None:
    _set_inputs(monkeypatch)
    secrets_api = _Secrets(scope_exists=True, keys=SALESFORCE_KEYS)

    result = subject.provision_runtime_secrets(client=_client(secrets_api))

    assert not set(SALESFORCE_KEYS) & set(_put_keys(secrets_api))
    assert result.kept == SALESFORCE_KEYS
    assert all(secrets_api.values[key] is None for key in SALESFORCE_KEYS), "never touched"


def test_only_absent_salesforce_keys_get_a_disabled_sentinel(monkeypatch) -> None:
    _set_inputs(monkeypatch)
    secrets_api = _Secrets(scope_exists=True, keys=("salesforce-password",))

    result = subject.provision_runtime_secrets(client=_client(secrets_api))

    written = {key: value for _scope, key, value in secrets_api.puts}
    assert "salesforce-password" not in written and result.kept == ("salesforce-password",)
    for key in ("salesforce-client-secret", "salesforce-security-token"):
        assert written[key].startswith(subject.DISABLED_PREVIOUS_PREFIX)


def test_a_configured_salesforce_value_still_replaces_an_existing_key(monkeypatch) -> None:
    _set_inputs(monkeypatch)
    monkeypatch.setenv("SALESFORCE_PASSWORD", "sf-password-rotated")
    secrets_api = _Secrets(scope_exists=True, keys=SALESFORCE_KEYS)

    result = subject.provision_runtime_secrets(client=_client(secrets_api))

    assert (subject.DEFAULT_SCOPE, "salesforce-password", "sf-password-rotated") in secrets_api.puts
    assert result.kept == ("salesforce-client-secret", "salesforce-security-token")


# ------------------------------------------------- the read-only --check mode


def test_check_never_creates_the_scope_or_writes(monkeypatch) -> None:
    _set_inputs(monkeypatch)
    secrets_api = _Secrets()

    plan = subject.check_runtime_secrets(client=_client(secrets_api))

    assert plan.write_keys == DEFAULT_WRITTEN_KEYS and plan.scope_exists is False
    assert secrets_api.created_scopes == [] and secrets_api.puts == []
    assert MASK_SECRET not in repr(plan) and CURRENT_SECRET not in repr(plan)


def test_cli_refusal_exits_2_with_names_only(monkeypatch, capsys) -> None:
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", MASK_SECRET[:20])
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_CURRENT", CURRENT_SECRET)
    secrets_api = _Secrets()
    monkeypatch.setattr(subject, "WorkspaceClient", lambda: _client(secrets_api))

    for argv in (["--check"], []):
        assert subject.main(argv) == subject.EXIT_REFUSED == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert (
        captured.err.count(
            f"[runtime-secrets] refused on {INSTALL_HOST}: MIP_COTALITY_ID_MASK_SECRET must contain "
            "at least 32 UTF-8 bytes"
        )
        == 2
    )
    _assert_names_only(captured.err)
    assert secrets_api.puts == [] and secrets_api.created_scopes == []


def test_cli_check_reports_key_names_and_writes_nothing(monkeypatch, capsys) -> None:
    _set_inputs(monkeypatch)
    secrets_api = _Secrets(
        stored={"cotality-id-mask-v1": MASK_SECRET},
        keys=SALESFORCE_KEYS,
    )
    monkeypatch.setattr(subject, "WorkspaceClient", lambda: _client(secrets_api))

    assert subject.main(["--check"]) == 0

    out = capsys.readouterr().out
    assert out == (
        f"runtime secret plan for mip-runtime on {INSTALL_HOST}: "
        "write genie-action-current, genie-action-previous; "
        "keep cotality-id-mask-v1, salesforce-client-secret, salesforce-password, "
        "salesforce-security-token (read-only check, nothing written)\n"
        "runtime secret note: cotality-id-mask-v1: kept unread; MIP_COTALITY_ID_MASK_SECRET is set "
        "but never replaces an existing key\n"
        "runtime secret note: genie-action-current: absent, so written once from "
        "MIP_GENIE_ACTION_SECRET_CURRENT\n"
        "runtime secret note: genie-action-previous: absent, so written once as a disabled sentinel\n"
    )
    _assert_names_only(out)
    assert secrets_api.puts == [] and secrets_api.gets == []


# ------------------------------------------- genie-action-current (presence only)


def test_an_existing_current_key_is_kept_and_never_compared(monkeypatch) -> None:
    """A rotation is --rotate-genie-action, never a deploy side effect."""
    _set_inputs(monkeypatch)
    secrets_api = _Secrets(stored={"genie-action-current": OTHER_CURRENT_SECRET})

    result = subject.provision_runtime_secrets(client=_client(secrets_api))

    assert "genie-action-current" in result.kept
    assert "genie-action-current" not in _put_keys(secrets_api)
    assert secrets_api.values["genie-action-current"] == OTHER_CURRENT_SECRET
    assert secrets_api.gets == []


# ------------------------------------------- the workspace the CLI is about to call
#
# Run by hand, WorkspaceClient() resolves the SDK default chain (process env,
# then the ~/.databrickscfg DEFAULT profile), which can name another, shared
# workspace (post-W5 L3 verifier item L3-G2). Every CLI mode proves the
# resolved host is the install's before its first workspace call.


class _Untouchable:
    """A Secrets API that fails the test on any call."""

    def __getattr__(self, name: str):
        raise AssertionError(f"workspace call {name} before the host was proven")


def _resolving(monkeypatch, host: str | None) -> None:
    monkeypatch.setattr(
        subject,
        "WorkspaceClient",
        lambda: _client(_Untouchable(), host=host),  # type: ignore[arg-type]
    )


def _env_local(monkeypatch, tmp_path, text: str) -> None:
    env_local = tmp_path / ".env.local"
    env_local.write_text(text, encoding="utf-8")
    monkeypatch.setattr(subject, "ENV_LOCAL", env_local)


HOST_SOURCE_LABELS = {
    "shell-host": "DATABRICKS_HOST (shell)",
    "deploy-host": "MIP_DATABRICKS_WORKSPACE_HOST (shell)",
    "env-local-host": "DATABRICKS_HOST (.env.local)",
}


@pytest.mark.parametrize(
    "argv",
    (["--check"], [], ["--retire-previous"], ["--rotate-genie-action"]),
    ids=("check", "write", "retire", "rotate"),
)
@pytest.mark.parametrize("source", tuple(HOST_SOURCE_LABELS))
def test_a_workspace_other_than_the_install_refuses_before_any_call(
    monkeypatch, tmp_path, capsys, argv: list[str], source: str
) -> None:
    _set_inputs(monkeypatch)
    if source == "shell-host":
        monkeypatch.setenv(subject.HOST_ENV, INSTALL_HOST)
        monkeypatch.delenv(subject.DEPLOY_HOST_ENV)
    elif source == "env-local-host":
        monkeypatch.delenv(subject.DEPLOY_HOST_ENV)
        _env_local(monkeypatch, tmp_path, f"DATABRICKS_HOST={INSTALL_HOST}\n")
    # The SDK fell back to a profile that names another workspace.
    _resolving(monkeypatch, OTHER_HOST)

    assert subject.main(argv) == subject.EXIT_REFUSED == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        f"[runtime-secrets] refused: the Databricks SDK resolved workspace {OTHER_HOST}, "
        f"but {HOST_SOURCE_LABELS[source]} names {INSTALL_HOST}. Pin the install's identity "
        "(for example DATABRICKS_CONFIG_PROFILE=<install profile>) and run again. "
        "Nothing was read or written.\n"
    )


def test_every_configured_install_host_must_agree(monkeypatch, capsys) -> None:
    """deploy.sh's bound host agreeing does not excuse a shell host naming another."""
    _set_inputs(monkeypatch)
    monkeypatch.setenv(subject.HOST_ENV, OTHER_HOST)
    _resolving(monkeypatch, INSTALL_HOST)

    assert subject.main(["--check"]) == 2

    err = capsys.readouterr().err
    assert f"DATABRICKS_HOST (shell) names {OTHER_HOST}" in err
    assert "MIP_DATABRICKS_WORKSPACE_HOST" not in err, "only the disagreeing source is named"


def test_no_configured_install_host_refuses(monkeypatch, capsys) -> None:
    _set_inputs(monkeypatch)
    monkeypatch.delenv(subject.DEPLOY_HOST_ENV)
    _resolving(monkeypatch, INSTALL_HOST)

    assert subject.main(["--check"]) == 2

    assert capsys.readouterr().err == (
        "[runtime-secrets] refused: no install workspace host is configured to check "
        f"{INSTALL_HOST} against. Set DATABRICKS_HOST in .env.local (deploy.sh documents it as required) "
        "or in the shell. Nothing was read or written.\n"
    )


@pytest.mark.parametrize("resolved", (None, "", "https://"), ids=("none", "empty", "no-hostname"))
def test_an_unresolved_workspace_host_refuses(monkeypatch, capsys, resolved: str | None) -> None:
    _set_inputs(monkeypatch)
    _resolving(monkeypatch, resolved)

    assert subject.main(["--check"]) == 2

    assert "the Databricks SDK resolved no workspace host" in capsys.readouterr().err


@pytest.mark.parametrize(
    "configured",
    (
        "dbc-synthetic-install.cloud.databricks.com",
        "https://DBC-Synthetic-Install.cloud.databricks.com/",
        "https://dbc-synthetic-install.cloud.databricks.com:443",
        "  https://dbc-synthetic-install.cloud.databricks.com/?o=1  ",
    ),
    ids=("no-scheme", "case-and-slash", "default-port", "padding-and-query"),
)
def test_an_equivalent_spelling_of_the_install_host_is_accepted(
    monkeypatch, capsys, configured: str
) -> None:
    _set_inputs(monkeypatch)
    monkeypatch.setenv(subject.HOST_ENV, configured)
    secrets_api = _Secrets()
    monkeypatch.setattr(subject, "WorkspaceClient", lambda: _client(secrets_api))

    assert subject.main(["--check"]) == 0

    assert capsys.readouterr().out.startswith(
        f"runtime secret plan for mip-runtime on {INSTALL_HOST}: "
    )


@pytest.mark.parametrize(
    ("resolved", "configured"),
    (
        (f"{INSTALL_HOST}.other.example", INSTALL_HOST),
        (INSTALL_HOST, f"{INSTALL_HOST}.other.example"),
        (INSTALL_HOST, INSTALL_HOST.replace("https://", "http://")),
        (INSTALL_HOST.replace("https://", "http://"), INSTALL_HOST),
    ),
    ids=("resolved-extends-configured", "configured-extends-resolved", "http-configured", "http-resolved"),
)
def test_a_lookalike_of_the_install_host_refuses(
    monkeypatch, capsys, resolved: str, configured: str
) -> None:
    """Hosts compare by strict equality: no prefix match, and the scheme counts."""
    _set_inputs(monkeypatch)
    monkeypatch.setenv(subject.DEPLOY_HOST_ENV, configured)
    _resolving(monkeypatch, resolved)

    assert subject.main(["--check"]) == 2

    err = capsys.readouterr().err
    assert f"the Databricks SDK resolved workspace {resolved}, " in err
    assert f"MIP_DATABRICKS_WORKSPACE_HOST (shell) names {configured}." in err


def test_a_refused_host_prints_neither_user_info_nor_path(monkeypatch, capsys) -> None:
    _set_inputs(monkeypatch)
    monkeypatch.setenv(
        subject.HOST_ENV, "https://operator:synthetic-tok3n@other.example:8443/x?o=7"
    )
    monkeypatch.setenv(subject.DEPLOY_HOST_ENV, "https://bad-port.example:notaport")
    _resolving(monkeypatch, INSTALL_HOST)

    assert subject.main([]) == 2

    err = capsys.readouterr().err
    assert "DATABRICKS_HOST (shell) names https://other.example:8443" in err
    assert "MIP_DATABRICKS_WORKSPACE_HOST (shell) names an unparsable host" in err
    for fragment in ("operator", "synthetic-tok3n", "/x", "o=7", "notaport"):
        assert fragment not in err


def test_the_write_and_retire_summaries_name_the_resolved_host(monkeypatch, capsys) -> None:
    _set_inputs(monkeypatch)
    secrets_api = _Secrets()
    monkeypatch.setattr(subject, "WorkspaceClient", lambda: _client(secrets_api))

    assert subject.main([]) == 0
    assert subject.main(["--retire-previous"]) == 0

    out = capsys.readouterr().out
    assert out.startswith(
        f"runtime secret bindings ready in mip-runtime on {INSTALL_HOST}: 6 keys "
        "(written: cotality-id-mask-v1, genie-action-current, genie-action-previous, "
        "salesforce-client-secret, salesforce-password, salesforce-security-token; kept: none)\n"
        "runtime secret note: cotality-id-mask-v1: absent, so written once from "
        "MIP_COTALITY_ID_MASK_SECRET\n"
    )
    assert out.endswith(f"previous runtime verification key in mip-runtime on {INSTALL_HOST}: replaced\n")
    assert out.count("runtime secret note: ") == 6


def test_the_unbound_default_client_is_host_checked_too(monkeypatch) -> None:
    """A programmatic caller without a client gets the same proof as the CLI."""
    _set_inputs(monkeypatch)
    _resolving(monkeypatch, OTHER_HOST)

    for call in (
        subject.check_runtime_secrets,
        subject.provision_runtime_secrets,
        subject.retire_previous_secret,
        subject.rotate_genie_action_secret,
    ):
        with pytest.raises(subject.WorkspaceHostRefused, match="resolved workspace"):
            call()
