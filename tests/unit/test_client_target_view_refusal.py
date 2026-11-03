"""The deploy refuses a client-target view it cannot trust (M1-PR2, M1-PR3, M2-PR5).

``resolve_client_profile`` (scripts/lib/deploy_identity_lifecycle.sh) reads
the 0600 view ``python -m tools.clients resolve --emit client-target`` writes
and refuses, exit 2 with a line that names no value:

* a view missing the client key, the install class, the host, the deployer
  or the jobs identity, or carrying one of them empty;
* an install class or a policy value outside its closed set (App env, demo
  feeds, presenter mode, weekly refresh), each pattern anchored at both ends;
* a missing principal that an ambient ``BUNDLE_VAR_*`` export would fill;
* a name the view does not carry.

The real resolver always writes a complete view, so ``$PYTHON`` is a shell
stub that copies a hand-written one to ``--out``. The positive controls feed
the resolver's own view, rendered by its own env writer, and must pass.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.fixtures.client_profiles import customer_profile, demo_profile
from tools.clients import profile as views
from tools.clients.__main__ import _render
from tools.clients.environment import RawProfile, resolve

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = ROOT / "scripts" / "lib" / "deploy_identity_lifecycle.sh"
INCOMPLETE = "[deploy] the client-target view is incomplete; refusing."
UNEXPECTED = "[deploy] the client-target view carries an unexpected name; refusing."
# The values resolve_client_profile keeps, printed after it returns.
_KEPT = (
    "CLIENT_KEY", "CLIENT_INSTALL_CLASS", "CLIENT_HOST", "CLIENT_DEPLOYER", "CLIENT_JOBS_PRINCIPAL",
    "CLIENT_APP_ENV", "CLIENT_DEMO_FEEDS", "CLIENT_PRESENTER_MODE", "CLIENT_SCHEDULED_REFRESH",
)
_HARNESS = (
    "set -euo pipefail\n"
    "RED=''; RST=''\n"
    f'. "{LIBRARY}"\n'
    # The resolver's argv: resolve --emit client-target --format env --out FILE.
    'mip_stub_resolver() { while (($#)); do [[ "$1" == --out ]] && cp "$MIP_TEST_VIEW" "$2"; shift; done; }\n'
    "PYTHON=mip_stub_resolver\n"
    "resolve_client_profile\n"
    + "".join(f'printf "{name}=%s\\n" "${name}"\n' for name in _KEPT)
)


Profile = Callable[..., dict[str, str]]


def _view(build: Profile = demo_profile) -> dict[str, str]:
    return views.client_target(resolve(RawProfile(values=build())))


def _run(tmp_path: Path, view: dict[str, str], **ambient: str) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "client-target.env"
    path.write_text(_render(view, "env"), encoding="utf-8")
    # The refusal line is asserted exactly: no runner mask lines, no ambient client values.
    base = {
        key: value
        for key, value in os.environ.items()
        if key != "GITHUB_ACTIONS" and not key.startswith(("MIP_", "BUNDLE_VAR_", "CLIENT_"))
    }
    return subprocess.run(
        ["bash", "-c", _HARNESS],
        cwd=tmp_path,
        env={**base, "MIP_TEST_VIEW": str(path), **ambient},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _assert_refused(completed: subprocess.CompletedProcess[str], line: str) -> None:
    assert completed.returncode == 2, (completed.returncode, completed.stderr)
    assert completed.stderr.strip() == line
    assert completed.stdout == "", "nothing past the refusal ran"


@pytest.mark.parametrize("build", [demo_profile, customer_profile], ids=["demo", "customer"])
def test_the_resolvers_own_view_is_accepted_and_kept(tmp_path: Path, build: Profile) -> None:
    view = _view(build)
    completed = _run(tmp_path, view)

    assert completed.returncode == 0, completed.stderr
    lines = completed.stdout.splitlines()
    install_class = view["MIP_INSTALL_CLASS"]
    assert lines[0] == f"  client:     {view['MIP_CLIENT']} ({install_class} install; profile resolved)"
    assert dict(line.split("=", 1) for line in lines[1:]) == {
        "CLIENT_KEY": view["MIP_CLIENT"],
        "CLIENT_INSTALL_CLASS": install_class,
        "CLIENT_HOST": view["DATABRICKS_HOST"],
        "CLIENT_DEPLOYER": view["BUNDLE_VAR_deployer_principal"],
        "CLIENT_JOBS_PRINCIPAL": view["BUNDLE_VAR_jobs_principal"],
        "CLIENT_APP_ENV": view["MIP_APP_ENV"],
        "CLIENT_DEMO_FEEDS": view["MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS"],
        "CLIENT_PRESENTER_MODE": view["MIP_PRESENTER_MODE"],
        "CLIENT_SCHEDULED_REFRESH": view["MIP_SCHEDULED_REFRESH"],
    }


REQUIRED = (
    "MIP_CLIENT", "MIP_INSTALL_CLASS", "DATABRICKS_HOST", "BUNDLE_VAR_deployer_principal",
    "BUNDLE_VAR_jobs_principal", "MIP_APP_ENV", "MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS", "MIP_PRESENTER_MODE",
    "MIP_SCHEDULED_REFRESH",
)


@pytest.mark.parametrize("name", REQUIRED)
def test_a_view_missing_a_required_name_is_refused(tmp_path: Path, name: str) -> None:
    view = _view()
    assert name in view, "the resolver's view carries every name the deploy requires"
    del view[name]

    _assert_refused(_run(tmp_path, view), INCOMPLETE)


@pytest.mark.parametrize("name", REQUIRED)
def test_a_view_carrying_a_required_name_empty_is_refused(tmp_path: Path, name: str) -> None:
    view = {**_view(), name: ""}

    _assert_refused(_run(tmp_path, view), INCOMPLETE)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MIP_INSTALL_CLASS", "trial"),
        ("MIP_INSTALL_CLASS", "demos"),
        ("MIP_APP_ENV", "staging"),
        ("MIP_APP_ENV", "production"),
        ("MIP_APP_ENV", "xsandbox"),
        ("MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS", "2"),
        ("MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS", "10"),
        ("MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS", "01"),
        ("MIP_PRESENTER_MODE", "2"),
        ("MIP_PRESENTER_MODE", "10"),
        ("MIP_PRESENTER_MODE", "01"),
        ("MIP_SCHEDULED_REFRESH", "weekly"),
        ("MIP_SCHEDULED_REFRESH", "only"),
        ("MIP_SCHEDULED_REFRESH", "xoff"),
    ],
)
def test_a_value_outside_its_closed_set_is_refused(tmp_path: Path, name: str, value: str) -> None:
    view = {**_view(), name: value}

    _assert_refused(_run(tmp_path, view), INCOMPLETE)


@pytest.mark.parametrize("name", ["BUNDLE_VAR_deployer_principal", "BUNDLE_VAR_jobs_principal"])
def test_an_ambient_export_never_fills_a_missing_principal(tmp_path: Path, name: str) -> None:
    view = _view()
    ambient = view.pop(name)

    _assert_refused(_run(tmp_path, view, **{name: ambient}), INCOMPLETE)


@pytest.mark.parametrize("name", ["MIP_UNREVIEWED", "BUNDLE_VAR_uc_catalog", "CLIENT_JOBS_PRINCIPAL"])
def test_a_name_the_view_does_not_carry_is_refused(tmp_path: Path, name: str) -> None:
    view = {**_view(), name: "unreviewed-value"}

    _assert_refused(_run(tmp_path, view), UNEXPECTED)
