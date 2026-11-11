"""The owner's review of a 10B-0 Genie pin plan prints counts only (tools/clients/genie_pin_review.py).

In CI the pin plan's candidate, managers and deployer are runner masks, so the
log shows ``***`` for each; the owner compares the counts this tool prints, as
the owner, with the masked line, and the holders with the profile's reviewed
Genie managers. It never prints a space id or a principal name, makes no
write, and refuses (exit 2) any title count but one, as the pin check does.
"""

from __future__ import annotations

import pytest

from tests.fixtures.genie_pin_workspace import TITLE, PinWorkspace, Space
from tools.clients import genie_pin_review as review

CREATOR = "creator@lender.example"
REVIEWED = "reviewed-principal@lender.example"
STRANGER = "unreviewed-principal@lender.example"


def _main(
    workspace: PinWorkspace, capsys: pytest.CaptureFixture[str], managers: str | None = None
) -> tuple[int, str]:
    environ = {} if managers is None else {review.MANAGERS_ENV: managers}
    rc = review.main(["--genie-space-name", TITLE], workspace_factory=lambda: workspace, environ=environ)
    out = capsys.readouterr()
    return rc, out.out + out.err


def test_one_title_match_prints_its_direct_manager_count_and_no_identifier(capsys: pytest.CaptureFixture[str]) -> None:
    workspace = PinWorkspace(
        spaces=[Space("governed-space-id", managers=(CREATOR, REVIEWED)), Space("other-space-id", title="Another title")],
        deployer=CREATOR,
    )

    rc, text = _main(workspace, capsys, managers=REVIEWED)

    assert rc == 0
    assert "Genie spaces carrying the governed title: 1" in text
    assert "direct CAN_MANAGE holders of that space: 2 (the caller is one: yes)" in text
    assert "reviewed Genie managers (MIP_GENIE_MANAGER_PRINCIPALS) among them: 1 of 1; holders outside that list: 1" in text
    assert "must list exactly 2 masked entries" in text
    for value in ("governed-space-id", "other-space-id", CREATOR, REVIEWED, "users"):
        assert value not in text, value
    assert workspace.mutations == [], "read-only"


def test_a_reviewed_manager_missing_and_an_unreviewed_holder_show_in_the_counts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = PinWorkspace(spaces=[Space("governed-space-id", managers=(CREATOR, STRANGER))], deployer=CREATOR)

    rc, text = _main(workspace, capsys, managers=f" {REVIEWED.upper()} , {CREATOR} ")

    assert rc == 0, "counts for the owner to judge, never a verdict on names"
    assert "among them: 1 of 2; holders outside that list: 1" in text
    for value in (CREATOR, REVIEWED, REVIEWED.upper(), STRANGER):
        assert value not in text, value


def test_the_inherited_holders_do_not_count_and_the_caller_may_be_none_of_them(capsys: pytest.CaptureFixture[str]) -> None:
    workspace = PinWorkspace(spaces=[Space("governed-space-id", managers=(REVIEWED,))], deployer=CREATOR)

    rc, text = _main(workspace, capsys)

    assert rc == 0 and "holders of that space: 1 (the caller is one: no)" in text
    assert "MIP_GENIE_MANAGER_PRINCIPALS is unset in this shell" in text and "among them" not in text


def test_the_reviewed_managers_come_from_the_process_environment(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(review.MANAGERS_ENV, REVIEWED)
    workspace = PinWorkspace(spaces=[Space("governed-space-id", managers=(REVIEWED,))], deployer=CREATOR)

    rc = review.main(["--genie-space-name", TITLE], workspace_factory=lambda: workspace)

    assert rc == 0 and "among them: 1 of 1; holders outside that list: 0" in capsys.readouterr().out


@pytest.mark.parametrize("count", (0, 2))
def test_any_count_but_one_refuses_as_the_pin_check_does(count: int, capsys: pytest.CaptureFixture[str]) -> None:
    workspace = PinWorkspace(spaces=[Space(f"space-{index}", managers=(CREATOR,)) for index in range(count)])

    rc, text = _main(workspace, capsys, managers=CREATOR)

    assert rc == 2 and f"Genie spaces carrying the governed title: {count}" in text
    assert "REFUSED" in text and "holders" not in text
    assert not [f"space-{index}" for index in range(count) if f"space-{index}" in text]


def test_a_failed_read_prints_its_class_only(capsys: pytest.CaptureFixture[str]) -> None:
    workspace = PinWorkspace(spaces=[Space("governed-space-id")])

    def denied(method: str, path: str) -> None:
        raise PermissionError(f"denied on {path}")

    workspace.api_client.do = denied

    rc, text = _main(workspace, capsys)

    assert rc == 2 and "(PermissionError)" in text and "governed-space-id" not in text
