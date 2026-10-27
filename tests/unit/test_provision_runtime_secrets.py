from __future__ import annotations

import base64
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
    return f"{label}-" + ("x" * 32)


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
DEFAULT_WRITTEN_KEYS = tuple(subject.ENV_TO_KEY.values())
SALESFORCE_KEYS = (
    "salesforce-client-secret",
    "salesforce-password",
    "salesforce-security-token",
)


@pytest.fixture(autouse=True)
def _hermetic_secret_config(monkeypatch) -> None:
    monkeypatch.setattr(subject, "ENV_LOCAL", subject.REPO / ".missing-test-env")
    for name in (*subject.ENV_TO_KEY, subject.PREVIOUS_KID_ENV):
        monkeypatch.delenv(name, raising=False)


class _Secrets:
    def __init__(
        self,
        *,
        scope_exists: bool = False,
        keys: tuple[str, ...] = (),
        stored: dict[str, str] | None = None,
        get_error: Exception | None = None,
        raw_stored: dict[str, str] | None = None,
    ) -> None:
        self.scope_exists = scope_exists or bool(stored) or bool(raw_stored)
        self.values: dict[str, str | None] = {key: None for key in keys}
        self.values.update(stored or {})
        self.raw_stored = dict(raw_stored or {})
        self.values.update({key: None for key in self.raw_stored})
        self.get_error = get_error
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
        if self.get_error is not None:
            raise self.get_error
        if key in self.raw_stored:
            return SimpleNamespace(key=key, value=self.raw_stored[key])
        value = self.values[key]
        assert value is not None, f"test stored no value for {key}"
        return SimpleNamespace(key=key, value=base64.b64encode(value.encode()).decode())

    def put_secret(self, *, scope: str, key: str, string_value: str) -> None:
        self.puts.append((scope, key, string_value))
        self.values[key] = string_value

    def delete_secret(self, *, scope: str, key: str) -> None:
        self.deletes.append((scope, key))
        self.values.pop(key, None)


def _client(secrets_api: _Secrets) -> SimpleNamespace:
    return SimpleNamespace(secrets=secrets_api)


def _required(monkeypatch, *, mask: str = MASK_SECRET) -> None:
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", mask)
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_CURRENT", CURRENT_SECRET)


def _put_keys(secrets_api: _Secrets) -> list[str]:
    return [key for _scope, key, _value in secrets_api.puts]


def _record_fingerprints(monkeypatch) -> list[bytes]:
    """Every salted fingerprint the comparison computes, so a test can prove none leaks."""

    digests: list[bytes] = []
    real = subject._salted_fingerprint

    def recording(value: bytes, salt: bytes) -> bytes:
        digest = real(value, salt)
        digests.append(digest)
        return digest

    monkeypatch.setattr(subject, "_salted_fingerprint", recording)
    return digests


def _assert_names_only(text: str, digests: list[bytes]) -> None:
    """No configured or stored value and no fingerprint, in any common encoding."""

    assert digests, "the comparison ran"
    for digest in digests:
        for form in (
            digest.hex(),
            base64.b64encode(digest).decode(),
            base64.urlsafe_b64encode(digest).decode(),
            repr(digest)[2:],
        ):
            assert form[:16] not in text
    assert not re.search(r"[0-9a-fA-F]{16,}", text), "no hex digest of any kind"
    for value in ALL_TEST_SECRETS:
        assert value not in text and value[:12] not in text


def test_provisions_required_values_without_previous_by_default(
    monkeypatch,
) -> None:
    _required(monkeypatch)
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
    assert secrets_api.deletes == []
    assert secrets_api.gets == [], "nothing to compare in a fresh scope"


def test_provisions_previous_only_for_explicit_rotation_grace(monkeypatch) -> None:
    _required(monkeypatch)
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


def test_retires_existing_previous_key_when_grace_is_not_configured(monkeypatch) -> None:
    _required(monkeypatch)
    secrets_api = _Secrets(
        scope_exists=True,
        keys=("genie-action-previous",),
    )

    result = subject.provision_runtime_secrets(client=_client(secrets_api))

    assert result.written == DEFAULT_WRITTEN_KEYS
    assert _put_keys(secrets_api) == list(DEFAULT_WRITTEN_KEYS)
    previous_value = next(
        value for _scope, key, value in secrets_api.puts if key == "genie-action-previous"
    )
    assert previous_value.startswith(subject.DISABLED_PREVIOUS_PREFIX)
    assert secrets_api.deletes == []


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


def test_rejects_missing_or_placeholder_required_values(monkeypatch) -> None:
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", "REDACTED")

    with pytest.raises(ValueError, match="MIP_COTALITY_ID_MASK_SECRET"):
        subject.provision_runtime_secrets(client=_client(_Secrets()))


def test_rejects_present_but_weak_runtime_secret(monkeypatch) -> None:
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", "x")
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_CURRENT", CURRENT_SECRET)

    with pytest.raises(ValueError, match="at least 32"):
        subject.provision_runtime_secrets(client=_client(_Secrets()))


def test_rejects_duplicate_rotation_values(monkeypatch) -> None:
    _required(monkeypatch)
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_PREVIOUS", CURRENT_SECRET)
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_PREVIOUS_KID", "v1")

    with pytest.raises(ValueError, match="must differ"):
        subject.provision_runtime_secrets(client=_client(_Secrets()))


def test_rejects_previous_secret_without_explicit_rotation_kid(monkeypatch) -> None:
    _required(monkeypatch)
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_PREVIOUS", PREVIOUS_SECRET)

    with pytest.raises(ValueError, match="MIP_GENIE_ACTION_SECRET_PREVIOUS_KID"):
        subject.provision_runtime_secrets(client=_client(_Secrets()))


def test_provisions_optional_salesforce_credentials_without_logging_values(monkeypatch) -> None:
    _required(monkeypatch)
    monkeypatch.setenv("SALESFORCE_CLIENT_SECRET", "sf-client-secret")
    monkeypatch.setenv("SALESFORCE_PASSWORD", "sf-password")
    monkeypatch.setenv("SALESFORCE_SECURITY_TOKEN", "sf-token")
    secrets_api = _Secrets()

    subject.provision_runtime_secrets(client=_client(secrets_api))

    assert (subject.DEFAULT_SCOPE, "salesforce-client-secret", "sf-client-secret") in secrets_api.puts
    assert (subject.DEFAULT_SCOPE, "salesforce-password", "sf-password") in secrets_api.puts
    assert (subject.DEFAULT_SCOPE, "salesforce-security-token", "sf-token") in secrets_api.puts


# ------------------------------------------------- the Cotality ID-mask guard


def test_an_unchanged_mask_is_kept_and_never_rewritten(monkeypatch) -> None:
    _required(monkeypatch)
    # The runtime strips surrounding whitespace, so a stored trailing newline
    # is the same effective mask.
    secrets_api = _Secrets(stored={"cotality-id-mask-v1": f"{MASK_SECRET}\n"})

    result = subject.provision_runtime_secrets(client=_client(secrets_api))

    assert secrets_api.gets == ["cotality-id-mask-v1"]
    assert "cotality-id-mask-v1" not in _put_keys(secrets_api)
    assert result.kept == ("cotality-id-mask-v1",)
    assert result.written == tuple(key for key in DEFAULT_WRITTEN_KEYS if key != "cotality-id-mask-v1")
    assert secrets_api.created_scopes == []


def test_a_changed_mask_refuses_before_any_write_and_prints_no_value(monkeypatch) -> None:
    _required(monkeypatch)
    secrets_api = _Secrets(
        stored={"cotality-id-mask-v1": OTHER_MASK_SECRET, "genie-action-current": CURRENT_SECRET}
    )
    digests = _record_fingerprints(monkeypatch)

    with pytest.raises(subject.RuntimeSecretChangeRefused) as refused:
        subject.provision_runtime_secrets(client=_client(secrets_api))

    assert secrets_api.puts == [] and secrets_api.created_scopes == []
    assert secrets_api.gets == ["cotality-id-mask-v1"], "the refusal precedes every other read"
    message = str(refused.value)
    assert "mip-runtime/cotality-id-mask-v1 holds a different value" in message
    assert "Nothing was written." in message
    _assert_names_only(message, digests)


@pytest.mark.parametrize(
    ("stored", "error", "reason"),
    (
        (None, PermissionError("denied"), "PermissionError"),
        ({"cotality-id-mask-v1": "not base64 !!"}, None, "Error"),
        ({"cotality-id-mask-v1": base64.b64encode(b"\xff\xfe").decode()}, None, "UnicodeDecodeError"),
        # An answer without a value is unreadable, never "a different value".
        ({"cotality-id-mask-v1": ""}, None, "EmptySecretValue"),
    ),
)
def test_a_mask_that_cannot_be_read_refuses_before_any_write(
    monkeypatch,
    stored: dict[str, str] | None,
    error: Exception | None,
    reason: str,
) -> None:
    _required(monkeypatch)
    secrets_api = _Secrets(
        keys=("cotality-id-mask-v1",),
        scope_exists=True,
        raw_stored=stored,
        get_error=error,
    )

    with pytest.raises(subject.RuntimeSecretChangeRefused, match="could not be read") as refused:
        subject.provision_runtime_secrets(client=_client(secrets_api))

    assert f"({reason})" in str(refused.value)
    # get-secret answers outside a notebook only for a workspace admin
    # (CLI v1.10.0 help: BAD_REQUEST for a normal user), so READ alone is not enough.
    assert "only to a workspace admin with READ on secret scope mip-runtime" in str(refused.value)
    assert secrets_api.puts == []


def test_a_sdk_error_code_names_the_unreadable_reason(monkeypatch) -> None:
    _required(monkeypatch)
    denied = RuntimeError("no read")
    denied.error_code = "PERMISSION_DENIED"  # type: ignore[attr-defined]
    secrets_api = _Secrets(keys=("cotality-id-mask-v1",), scope_exists=True, get_error=denied)

    with pytest.raises(subject.RuntimeSecretChangeRefused, match=r"\(PERMISSION_DENIED\)"):
        subject.provision_runtime_secrets(client=_client(secrets_api))


# ------------------------------------------------- existing Salesforce keys


def test_existing_salesforce_keys_are_kept_when_their_inputs_are_unset(monkeypatch) -> None:
    _required(monkeypatch)
    secrets_api = _Secrets(scope_exists=True, keys=SALESFORCE_KEYS)

    result = subject.provision_runtime_secrets(client=_client(secrets_api))

    assert not set(SALESFORCE_KEYS) & set(_put_keys(secrets_api))
    assert result.kept == SALESFORCE_KEYS
    assert all(secrets_api.values[key] is None for key in SALESFORCE_KEYS), "never touched"


def test_only_absent_salesforce_keys_get_a_disabled_sentinel(monkeypatch) -> None:
    _required(monkeypatch)
    secrets_api = _Secrets(scope_exists=True, keys=("salesforce-password",))

    result = subject.provision_runtime_secrets(client=_client(secrets_api))

    written = {key: value for _scope, key, value in secrets_api.puts}
    assert "salesforce-password" not in written and result.kept == ("salesforce-password",)
    for key in ("salesforce-client-secret", "salesforce-security-token"):
        assert written[key].startswith(subject.DISABLED_PREVIOUS_PREFIX)


def test_a_configured_salesforce_value_still_replaces_an_existing_key(monkeypatch) -> None:
    _required(monkeypatch)
    monkeypatch.setenv("SALESFORCE_PASSWORD", "sf-password-rotated")
    secrets_api = _Secrets(scope_exists=True, keys=SALESFORCE_KEYS)

    result = subject.provision_runtime_secrets(client=_client(secrets_api))

    assert (subject.DEFAULT_SCOPE, "salesforce-password", "sf-password-rotated") in secrets_api.puts
    assert result.kept == ("salesforce-client-secret", "salesforce-security-token")


# ------------------------------------------------- the read-only --check mode


def test_check_never_creates_the_scope_or_writes(monkeypatch) -> None:
    _required(monkeypatch)
    secrets_api = _Secrets()

    plan = subject.check_runtime_secrets(client=_client(secrets_api))

    assert plan.write_keys == DEFAULT_WRITTEN_KEYS and plan.scope_exists is False
    assert secrets_api.created_scopes == [] and secrets_api.puts == []
    assert MASK_SECRET not in repr(plan) and CURRENT_SECRET not in repr(plan)


def test_check_refuses_a_changed_mask_exactly_as_a_write_run(monkeypatch) -> None:
    _required(monkeypatch)
    secrets_api = _Secrets(stored={"cotality-id-mask-v1": OTHER_MASK_SECRET})

    with pytest.raises(subject.RuntimeSecretChangeRefused):
        subject.check_runtime_secrets(client=_client(secrets_api))

    assert secrets_api.puts == []


def test_cli_refusal_exits_2_with_names_only(monkeypatch, capsys) -> None:
    _required(monkeypatch)
    secrets_api = _Secrets(stored={"cotality-id-mask-v1": OTHER_MASK_SECRET})
    monkeypatch.setattr(subject, "WorkspaceClient", lambda: _client(secrets_api))

    digests = _record_fingerprints(monkeypatch)

    for argv in (["--check"], []):
        assert subject.main(argv) == subject.EXIT_REFUSED == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("[runtime-secrets] refused: mip-runtime/cotality-id-mask-v1") == 2
    _assert_names_only(captured.err, digests)
    assert secrets_api.puts == []


def test_cli_check_reports_key_names_and_writes_nothing(monkeypatch, capsys) -> None:
    _required(monkeypatch)
    secrets_api = _Secrets(
        stored={"cotality-id-mask-v1": MASK_SECRET},
        keys=SALESFORCE_KEYS,
    )
    monkeypatch.setattr(subject, "WorkspaceClient", lambda: _client(secrets_api))

    assert subject.main(["--check"]) == 0

    out = capsys.readouterr().out
    assert out == (
        "runtime secret plan for mip-runtime: write genie-action-current, genie-action-previous; "
        "keep cotality-id-mask-v1, salesforce-client-secret, salesforce-password, "
        "salesforce-security-token (read-only check, nothing written)\n"
    )
    assert secrets_api.puts == []

