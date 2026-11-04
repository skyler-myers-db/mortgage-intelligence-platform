"""The owner's estate inventory: closed schema, exact purpose-bound ack, expiry and gaps (M4-PR1).

The inventory is taken by the metastore's direct owner (``tools/clients/
estate_inventory.py``) and loaded by every agentic-tier consumer right before
it relies on it (``tools/databricks/estate/inventory.py``). Its ``estate``
digest stands in for the deferred owner attestation (M1-PR5), so these tests
pin that the ack is exact, purpose-bound and read from the process
environment only, and that the loader refuses an expired, future, foreign or
gapped inventory with lines that carry no name.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from databricks.sdk.errors import PermissionDenied, ResourceDoesNotExist

from tests.unit import test_audit_agent_runtime_foreign_uc_access as harness
from tools.clients import estate_inventory
from tools.clients.handover_journal import canonical_json, plan_sha256
from tools.databricks import prestop_accepted_exposure, quiesce_window_plan
from tools.databricks.estate import inventory as estate
from tools.databricks.estate.accepted_exposure import canonical_plan

NOW = harness.NOW
HIDDEN = "hidden-team-catalog"


def _document(**changes: Any) -> dict[str, Any]:
    document = json.loads(canonical_json(_collect()))
    document.update(changes)
    return document


def _collect(workspace: Any = None, **kwargs: Any) -> dict[str, Any]:
    return estate_inventory.collect(
        workspace if workspace is not None else harness._workspace(),
        account=kwargs.pop("account", harness._account()),
        install_catalog=harness.CATALOG,
        now=kwargs.pop("now", NOW),
        bound_readers=kwargs.pop("bound_readers", {}),
    )


def _env(document: dict[str, Any], *, ack: str | None = None, **extra: str) -> dict[str, str]:
    text = canonical_json(document)
    digest = estate.estate_digest(document) if ack is None else ack
    return {estate.INVENTORY_JSON_ENV: text, estate.ACK_ENV: digest, **extra}


def _load(environ: dict[str, str], **kwargs: Any) -> estate.EstateInventory:
    kwargs.setdefault("now", NOW)
    return estate.load_estate_inventory(
        environ,
        metastore_id=kwargs.pop("metastore_id", "metastore-id"),
        workspace_id=kwargs.pop("workspace_id", harness.WORKSPACE_ID),
        install_catalog=kwargs.pop("install_catalog", harness.CATALOG),
        **kwargs,
    )


def _refusal(environ: dict[str, str], **kwargs: Any) -> str:
    with pytest.raises(estate.EstateInventoryRefusal) as caught:
        _load(environ, **kwargs)
    return str(caught.value)


def _hidden_workspace() -> Any:
    """``other`` plus a catalog bound only to another workspace."""

    workspace = harness._workspace(
        extra_catalogs=[
            harness.SimpleNamespace(name=HIDDEN, isolation_mode="ISOLATED", owner=harness.FOREIGN_OWNER)
        ]
    )
    original = workspace.workspace_bindings.get_bindings
    workspace.workspace_bindings.get_bindings = lambda kind, name: (
        iter([harness.SimpleNamespace(workspace_id=harness.OTHER_WORKSPACE_ID, binding_type="BINDING_TYPE_READ_WRITE")])
        if name == HIDDEN
        else original(kind, name)
    )
    return workspace


def test_a_clean_inventory_loads_and_its_digest_is_the_canonical_estate_digest() -> None:
    document = _document()
    inventory = _load(_env(document))

    assert inventory.digest == plan_sha256(document, domain=estate.ACK_DOMAIN)
    assert document["purpose"] == "estate" and inventory.gap_count() == 0
    assert set(inventory.catalogs) == {"mip", "other", "system", "samples", "__databricks_internal"}
    other = inventory.catalogs["other"]
    assert other.owners == {
        harness.FOREIGN_OWNER: {"function": 1, "registered_model": 1, "schema": 1, "table": 1, "volume": 1}
    }
    assert other.information_schema_owners == {"System user": 2}


def test_the_estate_ack_is_exact() -> None:
    document = _document()
    digest = estate.estate_digest(document)

    assert "not acknowledged" in _refusal(_env(document, ack=""))
    assert "64-hex" in _refusal(_env(document, ack=digest.upper()))
    assert "64-hex" in _refusal(_env(document, ack=digest[:-1]))
    flipped = digest[:-1] + ("0" if digest[-1] != "0" else "1")
    assert "not this inventory's estate digest" in _refusal(_env(document, ack=flipped))
    # A one-character change to the inventory is a different digest.
    changed = _document()
    changed["catalogs"]["other"]["owner"] = "renamed-owner@example.com"
    assert "not this inventory's estate digest" in _refusal(_env(changed, ack=digest))


def test_the_estate_ack_is_purpose_bound() -> None:
    document = _document()
    digest = estate.estate_digest(document)
    others = {
        "accepted exposure plan": hashlib.sha256(canonical_plan([]).encode()).hexdigest(),
        "quiesce window body": quiesce_window_plan.body_sha256(document),
        "handover plan": plan_sha256(document, domain="mip.uc-ownership-handover.plan.v1"),
        "bare canonical sha256": hashlib.sha256(canonical_json(document).encode()).hexdigest(),
    }
    for name, other in others.items():
        assert other != digest, name
        assert "not this inventory's estate digest" in _refusal(_env(document, ack=other)), name
    # The body names its purpose: the same document under another purpose has no estate digest.
    with pytest.raises(ValueError, match="estate inventory only"):
        estate.estate_digest({**document, "purpose": "adopt_live"})
    assert "for purpose estate" in _refusal(_env({**document, "purpose": "adopt_live"}, ack=digest))
    # Another purpose's ack variable never satisfies the estate ack.
    environ = _env(document, ack="")
    environ["MIP_ACK_ACCEPTED_EXPOSURE_PLAN"] = digest
    assert "not acknowledged" in _refusal(environ)


def test_the_ack_and_the_inventory_are_never_read_from_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = _document()
    (tmp_path / ".env.local").write_text(
        f"{estate.ACK_ENV}={estate.estate_digest(document)}\n"
        f"{estate.INVENTORY_JSON_ENV}='{canonical_json(document)}'\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    assert "not acknowledged" in _refusal({estate.INVENTORY_JSON_ENV: canonical_json(document)})
    assert "needs the owner's estate inventory" in _refusal({})


def test_expiry_is_at_most_thirty_days_and_never_in_the_future() -> None:
    def at(issued: Any) -> dict[str, str]:
        return _env(_document(issued_at=issued.strftime("%Y-%m-%dT%H:%M:%SZ")))

    assert _load(at(NOW - estate.MAX_AGE)).issued_at == NOW - estate.MAX_AGE
    assert "older than 30 days" in _refusal(at(NOW - estate.MAX_AGE - timedelta(seconds=1)))
    assert "in the future" in _refusal(at(NOW + timedelta(seconds=1)))
    # The pre-lease check judges the age at the end of the run's margin.
    near = at(NOW - estate.MAX_AGE + timedelta(hours=12))
    assert _load(near)
    assert "at the end of this run's margin" in _refusal(near, margin=estate.RUN_MARGIN)
    assert estate.RUN_MARGIN == prestop_accepted_exposure.RUN_MARGIN


def test_the_inventory_must_describe_the_deployed_metastore_workspace_and_catalog() -> None:
    environ = _env(_document())

    assert "another metastore" in _refusal(environ, metastore_id="metastore-other")
    assert "another install workspace" in _refusal(environ, workspace_id=harness.OTHER_WORKSPACE_ID)
    assert "another install catalog" in _refusal(environ, install_catalog="mip_other")


def test_any_gap_refuses_with_a_count_and_no_name() -> None:
    document = _collect(_hidden_workspace())

    assert document["catalogs"][HIDDEN]["gap"] == "binding_denied_unreadable"
    message = _refusal(_env(document))
    assert "1 gaps" in message and HIDDEN not in message


def test_the_retired_binding_policy_refuses_when_set_and_names_its_replacement() -> None:
    environ = _env(_document(), **{estate.RETIRED_POLICY_ENV: '{"version":1,"catalogs":{}}'})

    message = _refusal(environ)
    assert "retired" in message and estate.INVENTORY_JSON_ENV in message and estate.ACK_ENV in message
    assert _load(_env(_document(), **{estate.RETIRED_POLICY_ENV: "  "}))


def test_the_source_is_one_env_secret_or_one_private_file(tmp_path: Path) -> None:
    document = _document()
    path = tmp_path / "estate.json"
    path.write_text(canonical_json(document), encoding="utf-8")
    path.chmod(0o600)
    by_file = {estate.INVENTORY_FILE_ENV: str(path), estate.ACK_ENV: estate.estate_digest(document)}

    assert _load(by_file).digest == estate.estate_digest(document)
    assert "exactly one" in _refusal({**_env(document), estate.INVENTORY_FILE_ENV: str(path)})
    path.chmod(0o644)
    assert "mode 0600" in _refusal(by_file)
    path.chmod(0o600)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    assert "never a symlink" in _refusal({**by_file, estate.INVENTORY_FILE_ENV: str(link)})
    assert "cannot be read" in _refusal({**by_file, estate.INVENTORY_FILE_ENV: str(tmp_path / "absent")})


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda d: d.update(extra=1), "invalid top-level field set"),
        (lambda d: d["catalogs"]["other"].update(gap="unknown_gap"), "unreviewed gap code"),
        (lambda d: d["catalogs"]["other"]["owners"][harness.FOREIGN_OWNER].update(table=0), "positive integers"),
        (lambda d: d["catalogs"]["other"]["owners"][harness.FOREIGN_OWNER].update(view=1), "unreviewed key"),
        (lambda d: d["catalogs"]["other"].update(scope="install"), "scope does not match"),
        (lambda d: d["catalogs"]["mip"]["owners"].update(x={"table": 1}), "only a foreign catalog"),
        (
            lambda d: d["catalogs"]["other"].update(
                bindings=[{"workspace_id": harness.WORKSPACE_ID, "binding_type": "BINDING_TYPE_READ_WRITE"}]
            ),
            "OPEN catalog lists no bindings",
        ),
        (lambda d: d.update(issued_at="2026-11-04"), "issued_at"),
        (lambda d: d["workspaces"]["assigned"].remove(harness.WORKSPACE_ID), "install workspace"),
        (
            lambda d: d["workspaces"]["defunct"].update({harness.WORKSPACE_ID: {}}),
            "not an assigned foreign one",
        ),
    ],
)
def test_the_schema_is_closed_and_its_lines_carry_no_value(mutate: Any, expected: str) -> None:
    document = _document()
    mutate(document)

    with pytest.raises(estate.EstateInventoryRefusal) as caught:
        estate.parse_inventory(canonical_json(document))
    assert expected in str(caught.value)
    for value in (harness.FOREIGN_OWNER, "metastore-id", harness.WORKSPACE_ID, "other"):
        assert all(value not in line.split(" ") for line in caught.value.lines), value


def test_a_repeated_key_refuses() -> None:
    text = canonical_json(_document())
    with pytest.raises(estate.EstateInventoryRefusal, match="repeats a key"):
        estate.parse_inventory(text.replace('{"catalogs":', '{"purpose":"estate","catalogs":', 1))


def test_a_binding_denied_catalog_is_read_only_through_a_bound_workspace_reader() -> None:
    workspace = _hidden_workspace()
    listed: list[str] = []
    original = workspace.schemas.list
    workspace.schemas.list = lambda catalog, **kwargs: listed.append(catalog) or original(catalog, **kwargs)
    schema = harness.SimpleNamespace(name="private", full_name=f"{HIDDEN}.private", owner=harness.APPLICATION_ID)
    reader = harness._bound_reader(schemas={HIDDEN: [schema]})

    document = _collect(workspace, bound_readers={harness.OTHER_WORKSPACE_ID: reader})

    assert HIDDEN not in listed  # the install workspace's answer for it is never trusted
    assert document["catalogs"][HIDDEN]["gap"] == ""
    assert document["catalogs"][HIDDEN]["owners"] == {harness.APPLICATION_ID: {"schema": 1}}


def test_a_refused_read_is_a_gap_never_an_empty_census() -> None:
    workspace = harness._workspace()

    def refuse(catalog: str, **_kwargs: Any) -> Any:
        raise PermissionDenied("listing refused")

    workspace.schemas.list = refuse
    document = _collect(workspace)

    assert document["catalogs"]["other"]["gap"] == "read_refused"
    assert "1 gaps" in _refusal(_env(document))


def test_the_collector_reads_no_grant_and_never_writes() -> None:
    workspace = harness._workspace()
    _collect(workspace)

    assert workspace.grants.calls == []


def _defunct_account(*, listing: Any) -> Any:
    account = harness._account()
    workspaces_get = account.workspaces.get

    def get(workspace_id: int) -> Any:
        if str(workspace_id) == harness.OTHER_WORKSPACE_ID:
            raise ResourceDoesNotExist("workspace deleted")
        return workspaces_get(workspace_id)

    account.workspaces.get = get
    original = account.workspace_assignment.list
    account.workspace_assignment.list = lambda workspace_id: (
        listing() if str(workspace_id) == harness.OTHER_WORKSPACE_ID else original(workspace_id)
    )
    return account


def test_a_defunct_workspace_is_recorded_with_the_exact_digest_of_its_assignments() -> None:
    def gone() -> Any:
        raise ResourceDoesNotExist("no assignments")

    listed = _collect(account=_defunct_account(listing=lambda: iter([])))["workspaces"]["defunct"]
    absent = _collect(account=_defunct_account(listing=gone))["workspaces"]["defunct"]

    assert listed == {
        harness.OTHER_WORKSPACE_ID: {"assignments": "listed", "assignments_sha256": estate.assignments_digest([])}
    }
    assert absent == {
        harness.OTHER_WORKSPACE_ID: {"assignments": "not_found", "assignments_sha256": estate.assignments_digest(None)}
    }
    assert estate.assignments_digest([]) != estate.assignments_digest(None)


def test_an_account_refusal_is_never_read_as_a_defunct_workspace() -> None:
    account = harness._account()

    def refuse(_workspace_id: int) -> Any:
        raise PermissionDenied("account admin required")

    account.workspaces.get = refuse
    with pytest.raises(PermissionDenied):
        _collect(account=account)


def test_the_cli_writes_a_private_file_and_prints_counts_and_the_digest_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    out = tmp_path / "estate.json"

    code = estate_inventory.main(
        ["--out", str(out)], workspace_factory=harness._workspace, account_factory=harness._account, now=NOW
    )

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert os.stat(out).st_mode & 0o777 == 0o600
    digest = estate.estate_digest(json.loads(out.read_text(encoding="utf-8")))
    assert f"sha256 {digest}" in captured.out
    printed = captured.out + captured.err
    for value in (harness.FOREIGN_OWNER, "other", "metastore-id", harness.WORKSPACE_ID, harness.OTHER_WORKSPACE_ID):
        assert value not in printed, value
    assert estate_inventory.main(["--summarize", str(out)], now=NOW) == 0
    assert f"sha256 {digest}" in capsys.readouterr().out


def test_the_cli_exits_three_on_a_gap_and_two_without_metastore_ownership(tmp_path: Path) -> None:
    gapped = estate_inventory.main(
        ["--out", str(tmp_path / "gapped.json")],
        workspace_factory=_hidden_workspace,
        account_factory=harness._account,
        now=NOW,
    )
    refused = estate_inventory.main(
        ["--out", str(tmp_path / "refused.json")],
        workspace_factory=lambda: harness._workspace(owner="someone-else@example.com"),
        account_factory=harness._account,
        now=NOW,
    )

    assert (gapped, refused) == (3, 2)
    assert not (tmp_path / "refused.json").exists()
