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

import pytest

from tests.fixtures.client_profiles import DEMO_PROFILE, IDENTITY_IDS
from tests.fixtures.live_workspace import LiveAccount, LiveWorkspace
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


@pytest.fixture(autouse=True)
def _outside_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    # Under GitHub Actions emit_mask writes ::add-mask:: lines (the runner
    # consumes them and prints nothing); the masked run has its own test below.
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


def _LiveWorkspace(**overrides: str) -> LiveWorkspace:  # noqa: N802 - the M1-PR2 name, now the shared fake
    """The shared fake (tests/fixtures/live_workspace.py): a healthy first install of PROFILE."""

    return LiveWorkspace(PROFILE, identity_ids=IDENTITY_IDS, **overrides)


IDENTITY_CALLS = ["GET scim/Me (org id)", "GET current-metastore-assignment", "GET scim/Me"]


def _account() -> LiveAccount:
    return LiveAccount(IDENTITY_IDS["account_principal"])


def _live_check(
    tmp_path: Path, workspace: LiveWorkspace, capsys: pytest.CaptureFixture[str]
) -> tuple[int, str]:
    env_file = tmp_path / "client.env"
    env_file.write_text("".join(f"{name}='{value}'\n" for name, value in PROFILE.items()), encoding="utf-8")
    code = live_checks.main([], environ={}, env_file=env_file, client=lambda: workspace, account=_account)
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
    assert workspace.calls[:3] == IDENTITY_CALLS, "the identity reads come first"
    assert all(call.startswith("GET") for call in workspace.calls), "read-only"


def test_under_actions_each_live_id_is_masked_and_every_other_line_is_value_free(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    live_ids = ("4200000099", "00000000-0000-4000-8000-00000000a099", IDENTITY_IDS["admin"])
    workspace = _LiveWorkspace(workspace_id=live_ids[0], metastore_id=live_ids[1], principal=live_ids[2])
    env_file = tmp_path / "client.env"
    env_file.write_text("".join(f"{name}='{value}'\n" for name, value in PROFILE.items()), encoding="utf-8")

    code = live_checks.main([], environ={}, env_file=env_file, client=lambda: workspace, account=_account)
    out = capsys.readouterr()

    assert code == 2
    masks = [line for line in out.err.splitlines() if line.startswith("::add-mask::")]
    assert set(live_ids) <= {line.removeprefix("::add-mask::") for line in masks}
    rest = out.out + "".join(f"{line}\n" for line in out.err.splitlines() if not line.startswith("::add-mask::"))
    assert not any(value in rest for value in (*_IDENTIFYING, *live_ids)), "only mask lines carry a value"
    assert out.err.index("::add-mask::") < out.err.index("client live checks refused:")


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
