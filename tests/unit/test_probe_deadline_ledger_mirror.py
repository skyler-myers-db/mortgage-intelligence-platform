"""Ledger-mirror behavior for bounded workspace reads.

The 2026-08-10 hour-long recovery was ~5k sequential CLI reads over the
grown lease ledger, re-walked per stability round. The mirror serves
immutable records from disk; mutable head/marker/root records stay live.
"""

from __future__ import annotations

import pytest

from tools.databricks import probe_deadlines


@pytest.fixture(autouse=True)
def _reset_mirror(monkeypatch):
    monkeypatch.setattr(probe_deadlines, "_mirror_snapshot", None)
    monkeypatch.delenv("MIP_PROBE_CLI_TRANSPORT", raising=False)
    monkeypatch.delenv(probe_deadlines._MIRROR_DIR_ENV, raising=False)


def test_mutable_ledger_paths_never_mirrored():
    root = probe_deadlines._LEASE_ROOT
    assert not probe_deadlines._mirror_immutable(f"{root}/mip-app.json.head")
    assert not probe_deadlines._mirror_immutable(f"{root}/mip-app.json.protocol-v5")
    assert not probe_deadlines._mirror_immutable(f"{root}/mip-app.json")


def test_immutable_ledger_paths_are_mirrorable():
    root = probe_deadlines._LEASE_ROOT
    generation = f"{root}/mip-app.json.2942d267-d1ab-4136-b103-8ba9f7396a57"
    assert probe_deadlines._mirror_immutable(generation)
    assert probe_deadlines._mirror_immutable(f"{generation}.next")
    assert probe_deadlines._mirror_immutable(
        f"{root}/mip-oauth-credential-mutations.b548be03.b548be03"
        ".oauth-credential-resolution.json"
    )


def test_snapshot_hit_serves_disk_bytes_without_any_transport(tmp_path, monkeypatch):
    path = f"{probe_deadlines._LEASE_ROOT}/mip-app.json.aa11.next"
    (tmp_path / "mip-app.json.aa11.next").write_bytes(b'{"generation_id": "aa11"}')
    monkeypatch.setenv(probe_deadlines._MIRROR_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(probe_deadlines, "_mirror_snapshot", {path})

    def _no_transport(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("mirror hit must not touch subprocess or SDK")

    monkeypatch.setattr(probe_deadlines.subprocess, "run", _no_transport)
    data = probe_deadlines.bounded_workspace_read(object(), path)
    assert data == b'{"generation_id": "aa11"}'


def test_live_read_success_backfills_the_mirror(tmp_path, monkeypatch):
    path = f"{probe_deadlines._LEASE_ROOT}/mip-app.json.bb22"
    monkeypatch.setenv(probe_deadlines._MIRROR_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(probe_deadlines, "_mirror_snapshot", set())

    class _Download:
        def read(self):
            return b'{"generation_id": "bb22"}'

    class _WorkspaceApi:
        def download(self, requested):
            assert requested == path
            return _Download()

    class _Workspace:
        workspace = _WorkspaceApi()

    data = probe_deadlines.bounded_workspace_read(_Workspace(), path, attempts=1)
    assert data == b'{"generation_id": "bb22"}'
    assert (tmp_path / "mip-app.json.bb22").read_bytes() == data
    assert path in probe_deadlines._mirror_snapshot
