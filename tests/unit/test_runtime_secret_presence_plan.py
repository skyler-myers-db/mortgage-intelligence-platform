"""The runtime secret plan is presence-only: an existing key is never read or written.

Every case drives ``tools/databricks/provision_runtime_secrets.py`` against a
fake Secrets API for one scope. Its ``get_secret`` is a spy: it records each
call and answers, so a value read that swallows its own failure is still seen.
A provisioned install (all six keys, which is what a live install's
``mip-runtime`` scope lists) must plan zero writes and make zero value reads.
A first install generates its keys on the runner, writes each once, and reads
each back for presence only. ``--rotate-genie-action`` is the one mode that
reads a value, and it refuses under GitHub Actions.
"""

from __future__ import annotations

import base64
import re
from types import SimpleNamespace

import pytest

from tools.databricks import provision_runtime_secrets as subject

INSTALL_HOST = "https://dbc-synthetic-install.cloud.databricks.com"
ALL_KEYS = tuple(subject.ENV_TO_KEY.values())
SALESFORCE_KEYS = tuple(
    key for env_name, key in subject.ENV_TO_KEY.items() if env_name in subject.OPTIONAL_RESOURCE_ENVS
)
HMAC_INPUTS = (subject.MASK_ENV, subject.CURRENT_ENV, subject.PREVIOUS_ENV)
STRONG_INPUT = "synthetic-presence-plan-input-" + "q" * 32
HEX_KEY = re.compile(r"[0-9a-f]{64}")


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch) -> None:
    monkeypatch.setattr(subject, "ENV_LOCAL", subject.REPO / ".missing-test-env")
    for name in (
        *subject.ENV_TO_KEY,
        subject.PREVIOUS_KID_ENV,
        subject.HOST_ENV,
        subject.GITHUB_ACTIONS_ENV,
        "DATABRICKS_CONFIG_PROFILE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(subject.DEPLOY_HOST_ENV, INSTALL_HOST)


class _Scope:
    """A fake Secrets API holding one scope. ``gets`` is the value-read spy."""

    def __init__(self, *, keys: tuple[str, ...] = (), exists: bool | None = None, drop: tuple[str, ...] = ()) -> None:
        self.exists = bool(keys) if exists is None else exists
        self.values = {key: f"stored-value-of-{key}-{'s' * 32}" for key in keys}
        self.drop = frozenset(drop)
        self.created: list[str] = []
        self.gets: list[str] = []
        self.puts: list[tuple[str, str]] = []
        self.lists = 0

    def list_scopes(self):
        return [SimpleNamespace(name=subject.DEFAULT_SCOPE)] if self.exists else []

    def create_scope(self, *, scope: str) -> None:
        assert scope == subject.DEFAULT_SCOPE
        self.created.append(scope)
        self.exists = True

    def list_secrets(self, *, scope: str):
        assert scope == subject.DEFAULT_SCOPE and self.exists
        self.lists += 1
        return [SimpleNamespace(key=key) for key in sorted(self.values)]

    def get_secret(self, *, scope: str, key: str):
        self.gets.append(key)
        return SimpleNamespace(key=key, value=base64.b64encode(self.values[key].encode()).decode())

    def put_secret(self, *, scope: str, key: str, string_value: str) -> None:
        assert scope == subject.DEFAULT_SCOPE
        self.puts.append((key, string_value))
        if key not in self.drop:
            self.values[key] = string_value


class _Untouchable:
    def __getattr__(self, name: str):
        raise AssertionError(f"workspace call {name} before the refusal")


def _client(api) -> SimpleNamespace:
    return SimpleNamespace(secrets=api, config=SimpleNamespace(host=INSTALL_HOST))


def _cli(monkeypatch, api) -> None:
    monkeypatch.setattr(subject, "WorkspaceClient", lambda: _client(api))


def _put_keys(api: _Scope) -> list[str]:
    return [key for key, _value in api.puts]


# ------------------------------------------------------ a provisioned install


@pytest.mark.parametrize("inputs", ("unset", "set"), ids=("no-inputs", "inputs-ignored"))
@pytest.mark.parametrize("mode", ("check", "write"))
def test_a_provisioned_install_plans_zero_writes_and_reads_no_value(monkeypatch, mode: str, inputs: str) -> None:
    if inputs == "set":
        for name in HMAC_INPUTS:
            monkeypatch.setenv(name, f"{STRONG_INPUT}-{name}")
        monkeypatch.setenv(subject.PREVIOUS_KID_ENV, "v1")
    api = _Scope(keys=ALL_KEYS)
    before = dict(api.values)

    if mode == "check":
        plan = subject.check_runtime_secrets(client=_client(api), require_existing=True)
        written, kept, notes = plan.write_keys, plan.kept, plan.notes
    else:
        result = subject.provision_runtime_secrets(client=_client(api), require_existing=True)
        written, kept, notes = result.written, result.kept, result.notes

    assert written == () and kept == ALL_KEYS
    assert api.gets == [], "no stored value is read"
    assert api.puts == [] and api.created == [] and api.values == before
    if inputs == "set":
        assert notes == tuple(
            f"{subject.ENV_TO_KEY[name]}: kept unread; {name} is set but never replaces an existing key"
            for name in HMAC_INPUTS
        )
    else:
        assert notes == ()


def test_a_weak_input_for_an_existing_key_is_unused_and_not_validated(monkeypatch) -> None:
    monkeypatch.setenv(subject.MASK_ENV, "short")
    api = _Scope(keys=ALL_KEYS)

    plan = subject.check_runtime_secrets(client=_client(api))

    assert plan.write_keys == () and api.gets == []


@pytest.mark.parametrize("stored", ("previous-stored", "previous-absent"))
def test_a_previous_input_without_its_kid_refuses_before_the_stop(monkeypatch, capsys, stored: str) -> None:
    """The App payload refuses this pairing whether or not the input seeds a key, and it runs after the stop."""
    monkeypatch.setenv(subject.PREVIOUS_ENV, STRONG_INPUT)
    keys = ALL_KEYS if stored == "previous-stored" else tuple(key for key in ALL_KEYS if key != subject.PREVIOUS_KEY)
    api = _Scope(keys=keys)
    _cli(monkeypatch, api)

    assert subject.main(["--check"]) == subject.EXIT_REFUSED

    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"refused on {INSTALL_HOST}: {subject.PREVIOUS_KID_ENV} is required when {subject.PREVIOUS_ENV}" in captured.err
    assert api.gets == [] and api.puts == []


def test_the_cli_check_on_a_provisioned_scope_reports_zero_writes_and_zero_reads(monkeypatch, capsys) -> None:
    api = _Scope(keys=ALL_KEYS)
    _cli(monkeypatch, api)

    assert subject.main(["--check", "--require-existing"]) == 0

    assert capsys.readouterr().out == (
        f"runtime secret plan for mip-runtime on {INSTALL_HOST}: write none; "
        f"keep {', '.join(ALL_KEYS)} (read-only check, nothing written)\n"
    )
    assert api.gets == [] and api.puts == []


def test_two_consecutive_runs_the_second_plans_zero_writes(monkeypatch, capsys) -> None:
    api = _Scope()
    _cli(monkeypatch, api)

    assert subject.main([]) == 0
    first = dict(api.values)
    assert subject.main(["--check", "--require-existing"]) == 0
    assert subject.main(["--require-existing"]) == 0

    out = capsys.readouterr().out
    assert f"(written: {', '.join(ALL_KEYS)}; kept: none)" in out
    assert "write none;" in out and f"(written: none; kept: {', '.join(ALL_KEYS)})" in out
    assert api.values == first and len(api.puts) == len(ALL_KEYS)
    assert api.gets == []


# ------------------------------------------------------------ a first install


def test_an_absent_mask_is_generated_once_and_read_back_for_presence(monkeypatch) -> None:
    api = _Scope(keys=tuple(key for key in ALL_KEYS if key != subject.MASK_KEY))

    result = subject.provision_runtime_secrets(client=_client(api))

    assert result.written == (subject.MASK_KEY,)
    assert result.notes == (
        f"{subject.MASK_KEY}: absent, so written once from a value generated on this runner",
    )
    ((key, value),) = api.puts
    assert key == subject.MASK_KEY and HEX_KEY.fullmatch(value)
    assert api.lists == 2, "one listing to plan, one to read the write back"
    assert api.gets == []


def test_an_absent_key_takes_its_optional_input(monkeypatch) -> None:
    monkeypatch.setenv(subject.MASK_ENV, STRONG_INPUT)
    api = _Scope(keys=tuple(key for key in ALL_KEYS if key != subject.MASK_KEY))

    result = subject.provision_runtime_secrets(client=_client(api))

    assert api.puts == [(subject.MASK_KEY, STRONG_INPUT)]
    assert result.notes == (f"{subject.MASK_KEY}: absent, so written once from {subject.MASK_ENV}",)


def test_a_first_install_generates_distinct_keys_and_prints_none(monkeypatch, capsys) -> None:
    api = _Scope()
    _cli(monkeypatch, api)

    assert subject.main([]) == 0

    written = dict(api.puts)
    assert api.created == [subject.DEFAULT_SCOPE] and tuple(written) == ALL_KEYS
    assert HEX_KEY.fullmatch(written[subject.MASK_KEY]) and HEX_KEY.fullmatch(written[subject.CURRENT_KEY])
    assert written[subject.MASK_KEY] != written[subject.CURRENT_KEY]
    for key in (subject.PREVIOUS_KEY, *SALESFORCE_KEYS):
        assert written[key].startswith(subject.DISABLED_PREVIOUS_PREFIX)
    out = capsys.readouterr().out
    for value in written.values():
        assert value[-16:] not in out
    assert not re.search(r"[0-9a-fA-F]{16,}", out)


def test_a_write_that_does_not_land_refuses(monkeypatch) -> None:
    api = _Scope(keys=tuple(key for key in ALL_KEYS if key != subject.MASK_KEY), drop=(subject.MASK_KEY,))

    with pytest.raises(subject.RuntimeSecretWriteRefused, match="does not list cotality-id-mask-v1"):
        subject.provision_runtime_secrets(client=_client(api))


# ------------------------------------------------ keys a deploy never replaces


@pytest.mark.parametrize("previous_input", (False, True), ids=("no-input", "input-set"))
def test_an_existing_previous_is_never_overwritten(monkeypatch, previous_input: bool) -> None:
    if previous_input:
        monkeypatch.setenv(subject.PREVIOUS_ENV, STRONG_INPUT)
        monkeypatch.setenv(subject.PREVIOUS_KID_ENV, "v1")
    api = _Scope(keys=(subject.PREVIOUS_KEY,))
    stored = api.values[subject.PREVIOUS_KEY]

    result = subject.provision_runtime_secrets(client=_client(api))

    assert subject.PREVIOUS_KEY in result.kept and subject.PREVIOUS_KEY not in _put_keys(api)
    assert api.values[subject.PREVIOUS_KEY] == stored and api.gets == []


@pytest.mark.parametrize("value", (None, "", "changeme", "<password>"), ids=("unset", "empty", "word", "bracketed"))
def test_unset_salesforce_inputs_keep_the_stored_keys(monkeypatch, value: str | None) -> None:
    for name in subject.OPTIONAL_RESOURCE_ENVS:
        if value is not None:
            monkeypatch.setenv(name, value)
    api = _Scope(keys=SALESFORCE_KEYS)

    result = subject.provision_runtime_secrets(client=_client(api))

    assert set(SALESFORCE_KEYS) <= set(result.kept)
    assert not set(SALESFORCE_KEYS) & set(_put_keys(api)) and api.gets == []


# ------------------------------------------------------- --require-existing


@pytest.mark.parametrize("argv", (["--check", "--require-existing"], ["--require-existing"]), ids=("check", "write"))
def test_require_existing_refuses_a_planned_write_with_exit_2(monkeypatch, capsys, argv: list[str]) -> None:
    api = _Scope(keys=tuple(key for key in ALL_KEYS if key != subject.CURRENT_KEY))
    _cli(monkeypatch, api)

    assert subject.main(argv) == subject.EXIT_REFUSED == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        f"[runtime-secrets] refused on {INSTALL_HOST}: --require-existing, but the plan writes "
        "genie-action-current. An install that is already provisioned plans zero writes: "
        "check the configured runtime secret scope before a first-install write mints new "
        "keys. Nothing was written.\n"
    )
    assert api.puts == [] and api.created == [] and api.gets == []


def test_require_existing_refuses_an_absent_scope(monkeypatch) -> None:
    api = _Scope()

    with pytest.raises(subject.RuntimeSecretWriteRefused, match="; scope mip-runtime is absent"):
        subject.provision_runtime_secrets(client=_client(api), require_existing=True)

    assert api.created == [] and api.puts == []


@pytest.mark.parametrize("mode", ("--retire-previous", "--rotate-genie-action"))
def test_require_existing_is_refused_beside_the_explicit_modes(mode: str) -> None:
    with pytest.raises(SystemExit) as exited:
        subject.main([mode, "--require-existing"])

    assert exited.value.code == 2


# ------------------------------------------------------ --rotate-genie-action


@pytest.mark.parametrize("value", ("true", "True", "1"))
def test_rotation_refuses_under_github_actions_before_any_call(monkeypatch, capsys, value: str) -> None:
    monkeypatch.setenv(subject.GITHUB_ACTIONS_ENV, value)
    monkeypatch.setattr(subject, "WorkspaceClient", lambda: _client(_Untouchable()))

    assert subject.main(["--rotate-genie-action"]) == 2
    with pytest.raises(subject.GenieActionRotationRefused, match="human-run only"):
        subject.rotate_genie_action_secret(client=_client(_Untouchable()))

    assert "--rotate-genie-action is human-run only, and GITHUB_ACTIONS is set" in capsys.readouterr().err


def test_rotation_moves_current_to_previous_and_generates_a_new_current(monkeypatch, capsys) -> None:
    api = _Scope(keys=ALL_KEYS)
    old_current = api.values[subject.CURRENT_KEY]
    _cli(monkeypatch, api)

    assert subject.main(["--rotate-genie-action"]) == 0

    assert api.gets == [subject.CURRENT_KEY], "the only value read"
    assert _put_keys(api) == [subject.PREVIOUS_KEY, subject.CURRENT_KEY], "previous first"
    assert api.values[subject.PREVIOUS_KEY] == old_current
    assert HEX_KEY.fullmatch(api.values[subject.CURRENT_KEY])
    out = capsys.readouterr().out
    assert out.startswith(f"Genie action keys in mip-runtime on {INSTALL_HOST}: rotated")
    assert old_current not in out and api.values[subject.CURRENT_KEY][-16:] not in out


@pytest.mark.parametrize("keys", ((), (subject.MASK_KEY,)), ids=("no-scope", "no-current-key"))
def test_rotation_without_a_stored_current_key_refuses(keys: tuple[str, ...]) -> None:
    api = _Scope(keys=keys)

    with pytest.raises(subject.GenieActionRotationRefused, match="nothing to rotate"):
        subject.rotate_genie_action_secret(client=_client(api))

    assert api.gets == [] and api.puts == []


def test_an_unreadable_current_key_is_never_rotated(monkeypatch) -> None:
    api = _Scope(keys=ALL_KEYS)
    denied = RuntimeError("no read")
    denied.error_code = "PERMISSION_DENIED"  # type: ignore[attr-defined]

    def refuse(*, scope: str, key: str):
        api.gets.append(key)
        raise denied

    monkeypatch.setattr(api, "get_secret", refuse)

    with pytest.raises(subject.GenieActionRotationRefused, match=r"could not be read \(PERMISSION_DENIED\)"):
        subject.rotate_genie_action_secret(client=_client(api))

    assert api.puts == []
