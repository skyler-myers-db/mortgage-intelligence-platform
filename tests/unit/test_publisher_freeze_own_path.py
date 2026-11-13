"""A freeze marker a thaw can name must name its own path: kind, marker id and App (wave 7c).

``frozen_documents`` lists only the markers a CI thaw may sign over. A marker
that verifies under the deploy's key but names another kind, another marker
id or another App than its file name is planted, not frozen by this App's
freeze: the publisher still reads it frozen (fail closed), but no thaw may be
listed for it or written over it. Each plant here is validly signed, and the
same document at the path it names is listed, so only the own-path check can
refuse it. Every test runs against the in-memory workspace: no Databricks call.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from tests.unit.test_publisher_freeze import (  # noqa: F401 - the autouse fixture is the step's env
    APP,
    NOW,
    PLANTED,
    PLANTED_VERIFY,
    _env_file,
    _run,
    _the_freeze_steps_env,
    _workspace,
)
from tools.clients import publisher_freeze as tool
from tools.databricks import app_deployment_freeze as freezer
from tools.databricks.operation_ack import ack_digest

SIGNING = {tool.SIGNING_KEY: PLANTED, tool.VERIFY_KEY: PLANTED_VERIFY}
OTHER_APP = "other-app"
CASES = ("other-kind", "another-apps-bytes", "another-marker-id")


def _plant(case: str) -> tuple[str, str, dict[str, Any], str]:
    """(the App a thaw names, the path's marker id, the signed document, the path it names)."""

    marker_id, signed_for = str(uuid.uuid4()), str(uuid.uuid4())
    unsigned = {
        "version": 1, "kind": "freeze", "app_name": APP, "marker_id": marker_id,
        "frozen_by": "planted", "created_at": "2027-01-15T08:00:00+00:00", "until": NOW + 600,
    }
    if case == "other-kind":  # the other kind's name, with the freeze kind's exact fields
        unsigned["kind"] = "thaw"
        return APP, marker_id, freezer._sign(unsigned, environ=SIGNING), freezer.freeze_path(APP, marker_id)
    if case == "another-apps-bytes":  # APP's signed marker, copied under another App's name
        return OTHER_APP, marker_id, freezer._sign(unsigned, environ=SIGNING), freezer.freeze_path(APP, marker_id)
    unsigned["marker_id"] = signed_for  # signed for another marker id than its file name
    return APP, marker_id, freezer._sign(unsigned, environ=SIGNING), freezer.freeze_path(APP, signed_for)


def _planted_workspace(path: str, document: dict[str, Any]) -> Any:
    workspace = _workspace()
    workspace.workspace.add_file(path, json.dumps(document, sort_keys=True).encode())
    return workspace


@pytest.mark.parametrize("case", CASES)
def test_the_planted_document_is_listed_at_the_path_it_names(monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    """Positive control: the signature verifies, so a refusal below is the own-path check's alone."""

    _app, _marker_id, document, own_path = _plant(case)
    if case == "other-kind":
        document = freezer._sign(
            {**{k: v for k, v in document.items() if not k.startswith("attestation_")}, "kind": "freeze"},
            environ=SIGNING,
        )
    monkeypatch.setenv(tool.VERIFY_KEY, PLANTED_VERIFY)
    workspace = _planted_workspace(own_path, document)

    assert freezer.frozen_documents(workspace, app_name=APP, now=NOW) == [(document["marker_id"], document)]


@pytest.mark.parametrize("case", CASES)
def test_a_validly_signed_marker_that_misnames_its_path_is_never_listed_or_thawed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    app, marker_id, document, _own = _plant(case)
    path = freezer.freeze_path(app, marker_id)
    workspace = _planted_workspace(path, document)
    tree = workspace.workspace
    nodes = dict(tree.nodes)

    monkeypatch.setenv(tool.VERIFY_KEY, PLANTED_VERIFY)
    assert freezer.frozen_documents(workspace, app_name=app, now=NOW) == []
    (marker,) = freezer.judge_markers(workspace, now=NOW)
    assert (marker.app_name, marker.marker_id, marker.frozen) == (app, marker_id, True), "fail closed"
    assert marker.reason == "unverifiable (freeze marker does not name its own path)"
    monkeypatch.delenv(tool.VERIFY_KEY)
    # The pair a thaw would list for it if it were listed: the path's App and marker id, its bytes.
    pair = ack_digest("freeze_thaw", tool.thaw_plan(app, marker_id, document))

    env_file = _env_file(tmp_path, MIP_APP_NAME=app)
    for given in ("", pair):
        monkeypatch.setenv(tool.ACK_ENV, given)
        assert _run("thaw", env_file, workspace) == 2
        out, err = capsys.readouterr()
        assert out == "" and err.startswith(
            "[mip-publisher-freeze] REFUSED thaw_unverified: 1 freeze marker(s) of this App read frozen"
        ), err
        assert "nothing was written" in err and pair not in err
    assert tree.ops("upload") == [] and tree.nodes == nodes, "no thaw file, nothing written"
    assert not tree.exists(freezer.thaw_path(app, marker_id))
    monkeypatch.setenv(tool.VERIFY_KEY, PLANTED_VERIFY)
    assert [m.frozen for m in freezer.judge_markers(workspace, now=NOW)] == [True]
