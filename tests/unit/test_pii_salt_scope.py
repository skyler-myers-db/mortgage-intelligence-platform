"""tools/databricks/pii_salt_scope.py puts the PII salt once, on an authoritative absence only.

``secret('mip', 'pii-salt-v1')`` keys every masked join key in an install, and
the Secrets API overwrites on put. The inline deploy step this tool replaced
read a failed ``list-secrets`` as "key absent" and put a fresh salt over the
live one. These tests drive ``main`` against a fake Secrets API that records
every call, so each case states exactly which calls reached the workspace.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterator
from types import SimpleNamespace

import pytest

from tools.databricks import pii_salt_scope as subject
from tools.databricks import provision_runtime_secrets as runtime_secrets

INSTALL_HOST = "https://dbc-synthetic-install.cloud.databricks.com"
OTHER_HOST = "https://dbc-synthetic-other.cloud.databricks.com"
SCOPE = subject.DEFAULT_SCOPE
KEY = subject.SALT_KEY
# A value no listing or message could produce by accident, so a leak is visible.
GENERATED = "5a17" * 16
LEAKY_DETAIL = "dbc-leaky-detail.cloud.databricks.com"


class ListFailure(Exception):
    """Stands in for an SDK PermissionDenied, a timeout or a dropped connection."""


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(runtime_secrets, "ENV_LOCAL", tmp_path / ".missing-env-local")
    for name in ("DATABRICKS_HOST", "DATABRICKS_CONFIG_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    # deploy.sh exports the host it bound the deployer to.
    monkeypatch.setenv(runtime_secrets.DEPLOY_HOST_ENV, INSTALL_HOST)
    monkeypatch.setattr(subject.secrets, "token_hex", lambda nbytes: GENERATED[: nbytes * 2])


class _Secrets:
    """A Secrets API fake. ``fail`` maps an operation to the call numbers that raise."""

    def __init__(
        self,
        store: dict[str, set[str]] | None = None,
        *,
        fail: dict[str, set[int]] | None = None,
        partial: Collection[str] = (),
        drop_puts: bool = False,
    ) -> None:
        self.store = {scope: set(keys) for scope, keys in (store or {}).items()}
        self.fail = fail or {}
        self.partial = partial
        self.drop_puts = drop_puts
        self.calls: list[tuple[str, ...]] = []
        self.values: list[str] = []
        self._counts: dict[str, int] = {}

    def _enter(self, operation: str, *args: str) -> None:
        self.calls.append((operation, *args))
        self._counts[operation] = self._counts.get(operation, 0) + 1
        if self._counts[operation] in self.fail.get(operation, set()):
            raise ListFailure(f"{operation} failed against {LEAKY_DETAIL}")

    def _page(self, operation: str, items: list[SimpleNamespace]) -> Iterator[SimpleNamespace]:
        # A paginated listing that loses its second page: the first page alone
        # would look like an authoritative listing without the key.
        yield from items[:1]
        if operation in self.partial:
            raise ListFailure(f"{operation} page 2 failed against {LEAKY_DETAIL}")
        yield from items[1:]

    def list_scopes(self) -> Iterator[SimpleNamespace]:
        self._enter("list_scopes")
        return self._page("list_scopes", [SimpleNamespace(name=name) for name in sorted(self.store)])

    def list_secrets(self, *, scope: str) -> Iterator[SimpleNamespace]:
        self._enter("list_secrets", scope)
        if scope not in self.store:
            raise ListFailure("RESOURCE_DOES_NOT_EXIST")
        keys = [SimpleNamespace(key=key) for key in sorted(self.store[scope])]
        return self._page("list_secrets", keys)

    def create_scope(self, *, scope: str) -> None:
        self._enter("create_scope", scope)
        self.store.setdefault(scope, set())

    def put_secret(self, *, scope: str, key: str, string_value: str) -> None:
        self._enter("put_secret", scope, key)
        self.values.append(string_value)
        if not self.drop_puts:
            self.store.setdefault(scope, set()).add(key)

    def get_secret(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("the salt tool must never read a secret value")

    def delete_secret(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("the salt tool must never delete a secret")


def _client(api: _Secrets, host: str = INSTALL_HOST) -> SimpleNamespace:
    return SimpleNamespace(secrets=api, config=SimpleNamespace(host=host))


def _main(api: _Secrets, mode: str, *, require_existing: str = "0", host: str = INSTALL_HOST) -> int:
    argv = [mode, "--scope", SCOPE, "--require-existing", require_existing]
    return subject.main(argv, client=_client(api, host))


def _writes(api: _Secrets) -> list[tuple[str, ...]]:
    return [call for call in api.calls if call[0] in {"create_scope", "put_secret"}]


LIST_SCOPES = ("list_scopes",)
LIST_KEYS = ("list_secrets", SCOPE)
PUT = ("put_secret", SCOPE, KEY)
LIVE_SHAPE = {SCOPE: {KEY}, "other-team-scope": {"their-key"}}


# --- A list error never reaches a create or a put ----------------------------


@pytest.mark.parametrize("mode", ("ensure", "check"))
@pytest.mark.parametrize("require_existing", ("0", "1"))
@pytest.mark.parametrize(
    ("store", "failure"),
    (
        pytest.param(LIVE_SHAPE, {"fail": {"list_scopes": {1}}}, id="live-shape-scopes-error"),
        pytest.param(LIVE_SHAPE, {"fail": {"list_secrets": {1}}}, id="live-shape-keys-error"),
        pytest.param(
            {SCOPE: {"aa-first-key", KEY}}, {"partial": {"list_secrets"}}, id="keys-page-lost"
        ),
        pytest.param({SCOPE: {"zz-other"}}, {"fail": {"list_secrets": {1}}}, id="key-absent-keys-error"),
        pytest.param({}, {"fail": {"list_scopes": {1}}}, id="fresh-scopes-error"),
        pytest.param(
            {"aa-first": set(), SCOPE: set()}, {"partial": {"list_scopes"}}, id="scopes-page-lost"
        ),
    ),
)
def test_list_failure_never_writes(capsys, mode, require_existing, store, failure) -> None:
    api = _Secrets(store, **failure)

    assert _main(api, mode, require_existing=require_existing) == subject.EXIT_REFUSED

    assert _writes(api) == []
    assert {call[0] for call in api.calls} <= {"list_scopes", "list_secrets"}
    err = capsys.readouterr().err
    assert "[pii-salt] refused: could not list" in err
    assert "(ListFailure), so absence is not proven" in err
    assert "No put was attempted." in err
    assert LEAKY_DETAIL not in err


def test_a_failed_relist_after_the_scope_create_never_puts(capsys) -> None:
    api = _Secrets({}, fail={"list_secrets": {1}})

    assert _main(api, "ensure") == subject.EXIT_REFUSED

    assert api.calls == [LIST_SCOPES, ("create_scope", SCOPE), LIST_SCOPES, LIST_KEYS]
    assert "No put was attempted." in capsys.readouterr().err


@pytest.mark.parametrize(
    "items",
    (
        pytest.param({"list_scopes": [SimpleNamespace(name="")]}, id="unnamed-scope"),
        pytest.param({"list_scopes": [SimpleNamespace(name=SCOPE)] * 2}, id="duplicate-scope"),
        pytest.param({"list_secrets": [SimpleNamespace(key=KEY)] * 2}, id="duplicate-key"),
        pytest.param({"list_secrets": [SimpleNamespace(key=None)]}, id="unnamed-key"),
    ),
)
def test_a_malformed_inventory_refuses_before_any_write(monkeypatch, capsys, items) -> None:
    api = _Secrets({SCOPE: set()})
    for operation, listing in items.items():
        monkeypatch.setattr(
            api, operation, lambda *_a, _listing=listing, **_k: iter(_listing), raising=True
        )

    assert _main(api, "ensure") == subject.EXIT_REFUSED

    assert _writes(api) == []
    assert "inventory is malformed, so absence is not proven" in capsys.readouterr().err


# --- Authoritative inventories ------------------------------------------------


@pytest.mark.parametrize("require_existing", ("0", "1"))
def test_a_present_key_is_never_written(capsys, require_existing) -> None:
    for mode in ("check", "ensure"):
        api = _Secrets(LIVE_SHAPE)

        assert _main(api, mode, require_existing=require_existing) == 0

        assert api.calls == [LIST_SCOPES, LIST_KEYS]
    out = capsys.readouterr().out
    assert "[pii-salt] mip/pii-salt-v1 is present; nothing to write (read-only check" in out
    assert "[pii-salt] mip/pii-salt-v1 already present; left untouched" in out


def test_an_absent_key_is_put_once_then_listed(capsys) -> None:
    api = _Secrets({SCOPE: {"zz-other"}})

    assert _main(api, "ensure") == 0

    assert api.calls == [LIST_SCOPES, LIST_KEYS, PUT, LIST_SCOPES, LIST_KEYS]
    assert api.values == [GENERATED]
    assert KEY in api.store[SCOPE]
    captured = capsys.readouterr()
    assert "[pii-salt] generated mip/pii-salt-v1 (random 64-hex, write-once) and verified" in (
        captured.out
    )
    assert GENERATED not in captured.out + captured.err


def test_a_fresh_workspace_creates_the_scope_and_relists_before_the_put() -> None:
    api = _Secrets({"other-team-scope": {"their-key"}})

    assert _main(api, "ensure") == 0

    assert api.calls == [
        LIST_SCOPES,
        ("create_scope", SCOPE),
        LIST_SCOPES,
        LIST_KEYS,
        PUT,
        LIST_SCOPES,
        LIST_KEYS,
    ]
    assert api.store["other-team-scope"] == {"their-key"}


def test_the_value_is_32_random_bytes_from_the_secrets_module(monkeypatch) -> None:
    requested: list[int] = []
    monkeypatch.setattr(
        subject.secrets, "token_hex", lambda nbytes: requested.append(nbytes) or "ab" * nbytes
    )
    api = _Secrets({SCOPE: set()})

    assert _main(api, "ensure") == 0

    assert requested == [32]
    assert re.fullmatch(r"[0-9a-f]{64}", api.values[0])


@pytest.mark.parametrize(
    ("store", "expected"),
    (
        pytest.param({SCOPE: set()}, "absent; the provisioning step will write it once", id="key"),
        pytest.param({}, "absent with its scope; the provisioning step will create both", id="scope"),
    ),
)
def test_check_plans_a_first_write_and_never_makes_it(capsys, store, expected) -> None:
    api = _Secrets(store)

    assert _main(api, "check") == 0

    assert _writes(api) == []
    assert f"[pii-salt] mip/pii-salt-v1 is {expected} (read-only check" in capsys.readouterr().out


# --- require-existing ----------------------------------------------------------


@pytest.mark.parametrize("mode", ("ensure", "check"))
@pytest.mark.parametrize(
    ("store", "missing"),
    (
        pytest.param({SCOPE: {"zz-other"}}, "mip/pii-salt-v1", id="key-absent"),
        pytest.param({"other-team-scope": set()}, "scope mip", id="scope-absent"),
    ),
)
def test_require_existing_refuses_an_absent_salt(capsys, mode, store, missing) -> None:
    api = _Secrets(store)

    assert _main(api, mode, require_existing="1") == subject.EXIT_REFUSED

    assert _writes(api) == []
    err = capsys.readouterr().err
    assert f"[pii-salt] refused: {missing} is absent, but this install requires an existing salt" in err


@pytest.mark.parametrize("value", (None, "", "true", "yes", "2"))
def test_require_existing_must_be_stated_as_0_or_1(value) -> None:
    argv = ["ensure", "--scope", SCOPE]
    if value is not None:
        argv += ["--require-existing", value]
    api = _Secrets({})

    with pytest.raises(SystemExit) as raised:
        subject.main(argv, client=_client(api))

    assert raised.value.code == subject.EXIT_REFUSED
    assert api.calls == []


# --- Failures after the put are reported, never retried blind ----------------


def test_a_failed_put_is_reported_as_unproven(capsys) -> None:
    api = _Secrets({SCOPE: set()}, fail={"put_secret": {1}})

    assert _main(api, "ensure") == subject.EXIT_WRITE_UNPROVEN

    assert api.calls == [LIST_SCOPES, LIST_KEYS, PUT]
    captured = capsys.readouterr()
    assert "[pii-salt] failed: the put of mip/pii-salt-v1 failed (ListFailure)" in captured.err
    assert GENERATED not in captured.out + captured.err
    assert LEAKY_DETAIL not in captured.err


def test_a_put_that_is_not_listed_afterwards_is_unproven(capsys) -> None:
    api = _Secrets({SCOPE: set()}, drop_puts=True)

    assert _main(api, "ensure") == subject.EXIT_WRITE_UNPROVEN

    assert api.calls == [LIST_SCOPES, LIST_KEYS, PUT, LIST_SCOPES, LIST_KEYS]
    assert "was put but is not listed afterwards" in capsys.readouterr().err


def test_a_relist_error_after_the_put_is_unproven_not_a_second_put(capsys) -> None:
    api = _Secrets({SCOPE: set()}, fail={"list_secrets": {2}})

    assert _main(api, "ensure") == subject.EXIT_WRITE_UNPROVEN

    assert api.calls.count(PUT) == 1
    assert "was put but not re-listed" in capsys.readouterr().err


def test_a_failed_scope_create_never_puts(capsys) -> None:
    api = _Secrets({}, fail={"create_scope": {1}})

    assert _main(api, "ensure") == subject.EXIT_REFUSED

    assert api.calls == [LIST_SCOPES, ("create_scope", SCOPE)]
    assert "could not create scope mip (ListFailure)" in capsys.readouterr().err


# --- Refusals before any Secrets API call --------------------------------------


@pytest.mark.parametrize("mode", ("ensure", "check"))
def test_another_workspace_is_refused_before_any_call(capsys, mode) -> None:
    api = _Secrets(LIVE_SHAPE)

    assert _main(api, mode, host=OTHER_HOST) == subject.EXIT_REFUSED

    assert api.calls == []
    assert "[pii-salt] refused: the Databricks SDK resolved workspace" in capsys.readouterr().err


@pytest.mark.parametrize("scope", ("", "mip scope", "mip/../x", "m" * 129))
def test_an_invalid_scope_name_is_refused_before_any_call(capsys, scope) -> None:
    api = _Secrets(LIVE_SHAPE)

    assert subject.main(
        ["ensure", "--scope", scope, "--require-existing", "0"], client=_client(api)
    ) == subject.EXIT_REFUSED

    assert api.calls == []
    assert "the scope name is invalid. Nothing was read or written." in capsys.readouterr().err
