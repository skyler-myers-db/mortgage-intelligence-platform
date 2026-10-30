"""Client live checks (M1-PR2): a host, workspace id, metastore id or deployer mismatch exits 2.

tools/clients/live_checks.py runs right after scripts/deploy.sh binds the
deployer's workspace auth, before any other Databricks call. Every mismatch
exits 2 and names the check, never a value; a host mismatch sends no request
at all. (The bundle_env half of the workspace-auth contract is
tests/unit/test_workspace_auth.py; the deploy's own profile-host check is in
tests/unit/test_client_install_dry_run.py.)
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.fixtures.client_profiles import DEMO_PROFILE, IDENTITY_IDS
from tools.clients import live_checks
from tools.clients.schema import FIELD_CLASS, FieldClass

PROFILE = DEMO_PROFILE
# Every identifying value of the profile, and each application id inside its JSON.
_IDENTIFYING = sorted(
    {
        *(value for name, value in PROFILE.items() if FIELD_CLASS[name] is FieldClass.IDENTIFYING),
        *IDENTITY_IDS.values(),
    }
)


class _LiveWorkspace:
    """A fake deployer WorkspaceClient that records each GET it answers."""

    def __init__(self, **overrides: str) -> None:
        live = {
            "host": PROFILE["DATABRICKS_HOST"],
            "workspace_id": PROFILE["MIP_EXPECT_WORKSPACE_ID"],
            "metastore_id": PROFILE["MIP_EXPECT_METASTORE_ID"],
            "metastore_workspace_id": PROFILE["MIP_EXPECT_WORKSPACE_ID"],
            "principal": IDENTITY_IDS["deployer"],
            **overrides,
        }
        self.calls: list[str] = []
        self.config = SimpleNamespace(host=live["host"])
        self._live = live
        self.metastores = SimpleNamespace(current=self._current)
        self.current_user = SimpleNamespace(me=self._me)

    def get_workspace_id(self) -> int:
        self.calls.append("GET scim/Me (org id)")
        return int(self._live["workspace_id"])

    def _current(self) -> object:
        self.calls.append("GET current-metastore-assignment")
        return SimpleNamespace(
            metastore_id=self._live["metastore_id"],
            workspace_id=int(self._live["metastore_workspace_id"]),
        )

    def _me(self) -> object:
        self.calls.append("GET scim/Me")
        return SimpleNamespace(user_name=self._live["principal"])


def _live_check(
    tmp_path: Path, workspace: _LiveWorkspace, capsys: pytest.CaptureFixture[str]
) -> tuple[int, str]:
    env_file = tmp_path / "client.env"
    env_file.write_text("".join(f"{name}='{value}'\n" for name, value in PROFILE.items()), encoding="utf-8")
    code = live_checks.main([], environ={}, env_file=env_file, client=lambda: workspace)
    out = capsys.readouterr()
    text = out.out + out.err
    assert not any(value in text for value in _IDENTIFYING), "a refusal names checks, never values"
    return code, text


def test_live_checks_pass_on_the_profiles_workspace_and_deployer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Hosts and application ids compare without regard to case or a trailing slash.
    workspace = _LiveWorkspace(
        host=PROFILE["DATABRICKS_HOST"].upper() + "/", principal=IDENTITY_IDS["deployer"].upper()
    )

    code, text = _live_check(tmp_path, workspace, capsys)

    assert code == 0, text
    assert "client live checks ok" in text
    assert workspace.calls == ["GET scim/Me (org id)", "GET current-metastore-assignment", "GET scim/Me"]


def test_a_host_mismatch_exits_2_before_any_request(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    workspace = _LiveWorkspace(host="https://dbc-other.cloud.databricks.com")

    code, text = _live_check(tmp_path, workspace, capsys)

    assert code == 2
    assert "DATABRICKS_HOST: the deployer's resolved workspace host is not the profile's" in text
    assert workspace.calls == [], "no credential leaves for a host that is not the profile's"


@pytest.mark.parametrize(
    ("overrides", "refusal"),
    (
        ({"workspace_id": "4200000099"}, "MIP_EXPECT_WORKSPACE_ID: the workspace this host serves"),
        (
            {"metastore_id": "00000000-0000-4000-8000-00000000a099"},
            "MIP_EXPECT_METASTORE_ID: the workspace's current metastore is not the profile's",
        ),
        ({"metastore_workspace_id": "4200000099"}, "the metastore assignment names another workspace"),
        ({"principal": IDENTITY_IDS["normal"]}, "MIP_IDENTITY_IDS_JSON.deployer: the authenticated principal"),
        ({"principal": "operator@summit.example"}, "MIP_IDENTITY_IDS_JSON.deployer: the authenticated principal"),
    ),
)
def test_a_workspace_metastore_or_deployer_mismatch_exits_2(
    overrides: dict[str, str], refusal: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, text = _live_check(tmp_path, _LiveWorkspace(**overrides), capsys)

    assert code == 2
    assert refusal in text
    assert text.count("\n  - ") == 1, "exactly the one mismatched check is refused"


def test_every_identity_mismatch_is_reported_together(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    workspace = _LiveWorkspace(
        workspace_id="4200000099",
        metastore_id="00000000-0000-4000-8000-00000000a099",
        principal=IDENTITY_IDS["admin"],
    )

    code, text = _live_check(tmp_path, workspace, capsys)

    assert code == 2
    assert text.count("\n  - ") == 3


def test_an_unreadable_workspace_exits_2_with_the_error_class_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = _LiveWorkspace()

    def unreachable() -> int:
        raise ConnectionError(f"cannot reach {PROFILE['DATABRICKS_HOST']} as {IDENTITY_IDS['deployer']}")

    workspace.get_workspace_id = unreachable  # type: ignore[method-assign]

    code, text = _live_check(tmp_path, workspace, capsys)

    assert code == 2
    assert "live checks could not read the workspace: ConnectionError" in text


def test_a_refused_profile_exits_2_before_building_a_client(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_file = tmp_path / "client.env"
    env_file.write_text("MIP_CLIENT='fictional-demo'\n", encoding="utf-8")
    built: list[str] = []

    code = live_checks.main([], environ={}, env_file=env_file, client=lambda: built.append("client"))

    assert code == 2
    assert built == []
    assert "client live checks refused:" in capsys.readouterr().err


def test_live_checks_take_no_arguments(capsys: pytest.CaptureFixture[str]) -> None:
    assert live_checks.main(["--skip"]) == 2
    assert "no arguments" in capsys.readouterr().err
