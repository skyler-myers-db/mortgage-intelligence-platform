"""The App's URL is a runner mask where the release resolves it, before anything prints it.

The URL (``https://<app>-<workspace id>.<cloud>.databricksapps.com``) is no
client-profile field, so the profile's mask step never registered it, and the
release reads it from the App in two places:

* ``deploy_app_snapshot`` (a first install's first sight: ``apps get`` after
  the activation), which then echoes ``app url:``;
* step 0a (a routine release's first sight: the existing App's inventory row).

After either, every ``--base-url`` / ``--app-url`` command line the ``run``
helpers echo, and the closing summary's ``App URL:`` line, print it.
``mask_app_url`` (scripts/deploy.sh) registers the URL and its host through the
real ``tools.clients.redact.emit_mask`` right after each resolve, and a mask
that cannot be registered stops the release there. Each harness runs the
reviewed shell with the real ``redact`` module, stdout and stderr merged
(tests/fixtures/runner_mask_harness.py), so the order of a mask and the first
print is the job log's own. tests/unit/test_bootstrap_warehouse_release_sandbox.py
proves the first-install place on a whole release.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fixtures.deploy_script import deploy_contract_text, deploy_entrypoint_text
from tests.fixtures.runner_mask_harness import (
    carrying,
    env_writer,
    merged_log,
    shell_function,
    shell_slice,
)

HOST = "mip-app-url-sandbox.example.invalid"
URL = f"https://{HOST}"
MASKS = [f"::add-mask::{URL}", f"::add-mask::{HOST}"]


def _summary_line() -> str:
    """The closing summary's App URL line, exactly as the entrypoint prints it."""

    (line,) = [line for line in deploy_entrypoint_text().splitlines() if "App URL:     " in line]
    return line + "\n"


# What the release does with the URL once it holds it: a command echo and the summary.
USES = 'echo "\\$ tool --base-url ${MIP_APP_URL}"\n' + _summary_line()
SUMMARY = f"  App URL:     {URL}"


# --------------------------------------------------------------------------
# The helper
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", [URL, URL + "/", URL + "/api/v1/health"])
def test_the_url_and_its_host_are_registered_whatever_follows_the_host(tmp_path: Path, value: str) -> None:
    code, lines = merged_log(tmp_path, f'mask_app_url "{value}"')

    assert code == 0 and lines == [f"::add-mask::{value}", f"::add-mask::{HOST}", "reached-after-block"], lines


def test_an_empty_url_starts_no_interpreter(tmp_path: Path) -> None:
    code, lines = merged_log(tmp_path, 'PYTHON=/nonexistent/python\nmask_app_url ""\necho used', redact=False)

    assert code == 0 and lines == ["used", "reached-after-block"], lines


# --------------------------------------------------------------------------
# deploy_app_snapshot: a first install's first sight of its URL
# --------------------------------------------------------------------------

# The real function. The activation's own commands are stand-ins that print nothing
# but their echo; ``apps get`` answers the App, URL included.
SNAPSHOT = (
    "prove_blue_restorable_before_activation() { :; }\nemit_app_deploy_payload() { :; }\n"
    "assert_expected_app_identity() { :; }\nkeep_cli_result_private() { \"$@\" > /dev/null; }\n"
    'databricks() { [[ "$1 $2" == "apps get" ]] || return 0; printf \'%s\' "$APP_JSON"; }\n'
    "APP_NAME=mip-app\nAPP_SOURCE_PATH=/Workspace/app-src/r1\nAPP_GIT_SHA=0123abcd\nAPP_RELEASE_ID=r1\n"
    "APP_RELEASE_MANIFEST=manifest.json\nAPP_RELEASE_PRINCIPAL=deployer@example.invalid\nTARGET=client\n"
    "APP_RELEASE_ROOT_LEAF=app-src\nAPP_LAST_DEPLOY_PAYLOAD=\nAPP_DEPLOY_PAYLOAD=\n"
    + shell_function("deploy_app_snapshot")
    + 'deploy_app_snapshot "activate"\n'
)


def _snapshot(tmp_path: Path, *, actions: bool = True, redact: bool = True) -> tuple[int, list[str]]:
    env_writer(tmp_path, "converge_static_app_source", {})
    app = json.dumps({"name": "mip-app", "url": URL})
    # The payload's temporary file stays in the harness directory.
    shell = 'export TMPDIR="$PWD"\n' + SNAPSHOT + USES
    return merged_log(tmp_path, shell, actions=actions, redact=redact, env={"APP_JSON": app})


def test_the_activation_masks_the_url_before_it_echoes_it(tmp_path: Path) -> None:
    code, lines = _snapshot(tmp_path)

    assert code == 0, lines
    printed = carrying(lines, HOST)
    assert printed == [*MASKS, f"  app url:    {URL}", f"$ tool --base-url {URL}", SUMMARY], lines
    deploy = next(index for index, line in enumerate(lines) if "databricks apps deploy" in line)
    assert lines.index(MASKS[0]) == deploy + 1, "right after the resolve, which follows the activation"


def test_the_activation_stops_when_the_url_cannot_be_masked(tmp_path: Path) -> None:
    code, lines = _snapshot(tmp_path, redact=False)

    assert code == 1, lines
    assert not carrying(lines, HOST), "neither the failure nor a later line printed the URL"
    assert any("App's URL could not be registered as a runner mask" in line for line in lines), lines
    assert "reached-after-block" not in lines


def test_the_activation_is_silent_outside_actions(tmp_path: Path) -> None:
    code, lines = _snapshot(tmp_path, actions=False)

    assert code == 0, lines
    assert carrying(lines, HOST) == [f"  app url:    {URL}", f"$ tool --base-url {URL}", SUMMARY], lines


# --------------------------------------------------------------------------
# Step 0a: a routine release's first sight of the existing App's URL
# --------------------------------------------------------------------------

STEP_0A = shell_slice(
    "    # >>> app url: the existing App's, masked at resolve >>>",
    "    # <<< app url: the existing App's, masked at resolve <<<",
)


def _existing(
    tmp_path: Path, apps: list[dict[str, str]], *, redact: bool = True, uses: str = USES
) -> tuple[int, list[str]]:
    return merged_log(tmp_path, STEP_0A + "\n" + uses, redact=redact, env={"_EXISTING_APPS_JSON": json.dumps(apps)})


def test_step_0a_masks_the_existing_apps_url_before_any_command_or_the_summary_prints_it(tmp_path: Path) -> None:
    apps = [{"name": "another-app", "url": "https://x.example.invalid"}, {"name": "mip-app", "url": URL}]
    code, lines = _existing(tmp_path, apps)

    assert code == 0, lines
    assert lines == [*MASKS, f"$ tool --base-url {URL}", SUMMARY, "reached-after-block"], lines


def test_step_0a_stops_when_the_url_cannot_be_masked(tmp_path: Path) -> None:
    code, lines = _existing(tmp_path, [{"name": "mip-app", "url": URL}], redact=False)

    assert code == 4, lines
    assert not carrying(lines, HOST), lines
    assert any("App's URL could not be registered as a runner mask" in line for line in lines), lines
    assert "reached-after-block" not in lines


def test_step_0a_registers_nothing_when_the_inventory_names_no_url(tmp_path: Path) -> None:
    code, lines = _existing(tmp_path, [{"name": "mip-app", "url": ""}], uses=_summary_line())

    assert code == 0, lines
    assert not [line for line in lines if line.startswith("::")], lines
    assert lines[-2:] == ["  App URL:     (check the Databricks workspace → Apps)", "reached-after-block"], lines


def test_every_place_that_sets_the_url_masks_it_first() -> None:
    # The two resolves are the only assignments; a third would need its own mask and test.
    text = deploy_contract_text()
    assert text.count("export MIP_APP_URL=") == 2, "a new place sets the App URL"
    for resolved in ("DEPLOYED_APP_URL", "_EXISTING_APP_URL"):
        head = text.index(f'    {resolved}="$(')
        assert text.index(f'mask_app_url "${resolved}"', head) < text.index(f'export MIP_APP_URL="${resolved}"', head)
