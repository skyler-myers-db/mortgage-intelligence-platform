"""``make bundle-validate`` validates the client target, and that passes (M1-PR2).

The ``client`` target commits no host, so the CLI validates it against the
workspace the operator's own CLI auth names, with the root, run_as and names
of the filled client profile in ``.env.local`` (tools/databricks/bundle_env.py).
The ``ci`` target cannot stand in: its host is an RFC 2606 ``.invalid`` name,
so a real CLI validate of it stops at auth, whatever host the environment or a
profile names.

The real-CLI cases run the command in a sandbox clone
(tests/fixtures/deploy_sandbox.py) against a loopback stub workspace: SCIM
``Me`` plus a stateful ``workspace/get-status`` and ``workspace/mkdirs``,
reached through a stub CLI profile, so nothing leaves 127.0.0.1. They skip
when no ``databricks`` CLI is on PATH (PR CI installs none); the Makefile
recipe check runs everywhere.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import urllib.parse
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.fixtures.client_profiles import IDENTITY_IDS
from tests.fixtures.deploy_sandbox import ANSI, sandbox

ROOT = Path(__file__).resolve().parents[2]
CLIENT = "client-one"
CLIENT_ROOT = f"/Workspace/Users/{IDENTITY_IDS['deployer']}/.bundle/mortgage-intelligence-platform/{CLIENT}"
# A human operator validating from a workstation: not the deployer.
OPERATOR = "operator@lender.example"
needs_cli = pytest.mark.skipif(shutil.which("databricks") is None, reason="no databricks CLI on PATH")


def _recipe(target: str) -> list[str]:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    body = makefile[makefile.index(f"\n{target}: render-sql\n") + 1 :].split("\n\n", 1)[0]
    return [line.strip() for line in body.splitlines()[1:]]


def test_both_make_validate_targets_validate_the_client_target() -> None:
    assert _recipe("bundle-validate") == ["$(PYTHON) tools/databricks/bundle_env.py validate -t client"]
    assert _recipe("bundle-validate-env") == ["@$(PYTHON) tools/databricks/bundle_env.py validate -t client"]


class _StubWorkspace(BaseHTTPRequestHandler):
    """SCIM ``Me`` for the operator; folders exist once ``mkdirs`` made them."""

    folders: set[str]
    requests: list[tuple[str, str, str]]

    def _send(self, code: int, body: dict[str, object]) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - http.server's hook name
        url = urllib.parse.urlparse(self.path)
        self.requests.append(("GET", url.path, url.query))
        if url.path == "/api/2.0/preview/scim/v2/Me":
            self._send(200, {"userName": OPERATOR, "id": "1", "active": True})
            return
        if url.path == "/api/2.0/workspace/get-status":
            path = urllib.parse.parse_qs(url.query)["path"][0]
            if path in self.folders:
                self._send(200, {"object_type": "DIRECTORY", "object_id": 7, "path": path})
                return
        self._send(404, {"error_code": "RESOURCE_DOES_NOT_EXIST", "message": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - http.server's hook name
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        self.requests.append(("POST", self.path, body))
        if self.path == "/api/2.0/workspace/mkdirs":
            self.folders.add(json.loads(body)["path"])
            self._send(200, {})
            return
        self._send(404, {"error_code": "NOT_FOUND", "message": "not stubbed"})

    do_PUT = do_PATCH = do_DELETE = do_POST

    def log_message(self, *_args: object) -> None:
        pass


@pytest.fixture
def stub_workspace() -> Iterator[tuple[str, list[tuple[str, str, str]]]]:
    """(origin, the request log) of a loopback stub workspace."""

    requests: list[tuple[str, str, str]] = []
    handler = type("Handler", (_StubWorkspace,), {"folders": set(), "requests": requests})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", requests
    finally:
        server.shutdown()
        server.server_close()


def _operator_env(tmp_path: Path, origin: str) -> tuple[Path, dict[str, str]]:
    """The sandbox clone and an operator environment whose CLI profile is the stub workspace."""

    checkout, env, _log = sandbox(tmp_path)
    config = tmp_path / "databrickscfg"
    config.write_text(f"[stub]\nhost = {origin}\ntoken = offline-stub\n", encoding="utf-8")
    env.update(
        {
            # The real CLI, never the sandbox's stub; caches under a fresh home.
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(tmp_path / "home"),
            "DATABRICKS_CONFIG_FILE": str(config),
            "DATABRICKS_CONFIG_PROFILE": "stub",
            # The operator's own auth binding, so .env.local's profile host
            # (a synthetic cloud host) is never the CLI's.
            "MIP_DEPLOYER_DATABRICKS_PROFILE": "stub",
        }
    )
    return checkout, env


@needs_cli
def test_make_bundle_validate_passes_against_the_authenticated_workspace(
    tmp_path: Path, stub_workspace: tuple[str, list[tuple[str, str, str]]]
) -> None:
    origin, requests = stub_workspace
    checkout, env = _operator_env(tmp_path, origin)

    result = subprocess.run(
        ["make", "-s", "bundle-validate"], cwd=checkout, env=env, text=True, capture_output=True, check=False, timeout=300
    )

    out = ANSI.sub("", result.stdout + result.stderr)
    assert result.returncode == 0, out[-4000:]
    assert "Target: client" in out and f"Path: {CLIENT_ROOT}\n" in out and f"User: {OPERATOR}\n" in out
    assert "error" not in out.split("Target: client", 1)[1].lower(), out[-2000:]
    # It authenticated against the profile's workspace and read the client root;
    # the only write is the documented one: the root's empty files folder.
    assert ("GET", "/api/2.0/preview/scim/v2/Me", "") in requests
    files = f"{CLIENT_ROOT}/files"
    assert ("GET", "/api/2.0/workspace/get-status", urllib.parse.urlencode({"path": files})) in requests
    writes = [request for request in requests if request[0] != "GET"]
    assert writes == [("POST", "/api/2.0/workspace/mkdirs", json.dumps({"path": files}, separators=(",", ":")))]


@needs_cli
def test_the_ci_target_never_reaches_a_workspace(
    tmp_path: Path, stub_workspace: tuple[str, list[tuple[str, str, str]]]
) -> None:
    """The control: the same operator environment, the ``ci`` target, refused before any request."""

    origin, requests = stub_workspace
    checkout, env = _operator_env(tmp_path, origin)
    python = str(checkout / ".venv" / "bin" / "python")

    result = subprocess.run(
        [python, "tools/databricks/bundle_env.py", "validate", "-t", "ci"],
        cwd=checkout, env=env, text=True, capture_output=True, check=False, timeout=300,
    )

    assert result.returncode != 0
    assert "ci-placeholder.invalid" in ANSI.sub("", result.stdout + result.stderr)
    assert requests == [], "the ci target's host is never the stub's"
