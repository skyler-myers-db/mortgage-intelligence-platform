"""``--phase data`` grants EXECUTE only to the App identity its signed record pins (M1-PR6).

``tools/databricks/data_phase_app_identity.py`` replaces an unpinned ``apps
get``: the live App's service principal (client id and SCIM id) must be the
one the signed last-good rollback record names, or the data phase refuses
before its ledger, any job and any grant. ``_load_record`` (the record's scope
ownership and signature proof) has its own tests; here it is stubbed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tools.databricks import data_phase_app_identity as identity

APP = "mip-app"
CLIENT = "00000000-0000-4000-8000-0000000000bb"
SCIM = "4242"
ARGV = ["--app-name", APP, "--rollback-scope", "mip-app-rollback", "--lakebase-instance", "mip-app-state"]


def _workspace(*, client: str = CLIENT, scim: str = SCIM) -> Any:
    app = SimpleNamespace(service_principal_client_id=client, service_principal_id=scim)
    return SimpleNamespace(apps=SimpleNamespace(get=lambda name: app))


def _record(monkeypatch: pytest.MonkeyPatch, outcome: Any) -> None:
    def load(workspace: Any, **_: Any) -> dict[str, str]:
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(identity, "_load_record", load)


PINNED = {"app_service_principal_client_id": CLIENT, "app_service_principal_scim_id": SCIM}


def test_the_pinned_identity_is_printed_alone(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _record(monkeypatch, PINNED)
    assert identity.main(ARGV, workspace=_workspace()) == 0
    assert capsys.readouterr().out == f"{CLIENT}\n"


@pytest.mark.parametrize(
    "live",
    ({"client": "00000000-0000-4000-8000-0000000000cc"}, {"scim": "9999"}, {"client": ""}, {"scim": ""}),
    ids=("other-client", "other-scim", "no-client", "no-scim"),
)
def test_an_app_replaced_under_the_same_name_refuses(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], live: dict[str, str]
) -> None:
    _record(monkeypatch, PINNED)
    assert identity.main(ARGV, workspace=_workspace(**live)) == identity.EXIT_REFUSED
    captured = capsys.readouterr()
    assert captured.out == "" and "is not the one its signed rollback record pins" in captured.err


def test_no_signed_record_refuses_and_names_only_the_reviewed_absence(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _record(monkeypatch, RuntimeError(f"{identity.NO_RECORD}; run the explicit bootstrap command"))
    assert identity.main(ARGV, workspace=_workspace()) == identity.EXIT_REFUSED
    assert "none exists yet" in capsys.readouterr().err
    # Any other failure is named by its class: its message can carry a scope or an id.
    _record(monkeypatch, RuntimeError("App rollback secret scope mip-app-rollback-PLACEHOLDER is not owned"))
    assert identity.main(ARGV, workspace=_workspace()) == identity.EXIT_REFUSED
    err = capsys.readouterr().err
    assert "RuntimeError" in err and "PLACEHOLDER" not in err


def test_an_unreadable_app_refuses_by_class(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _record(monkeypatch, PINNED)

    def boom(name: str) -> Any:
        raise PermissionError(f"workspace-PLACEHOLDER denied {name}")

    workspace = SimpleNamespace(apps=SimpleNamespace(get=boom))
    assert identity.main(ARGV, workspace=workspace) == identity.EXIT_REFUSED
    err = capsys.readouterr().err
    assert "PermissionError" in err and "PLACEHOLDER" not in err
