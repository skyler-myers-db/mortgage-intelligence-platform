"""The install policy is the client profile's own fields, never a target name (M1-PR3).

* The matrix: install class x weekly refresh x presenter mode x demo feeds,
  every combination through the real profile resolver. A demo flag on a
  customer install is refused (by the profile's rules and again by
  ``install_policy``); every other combination yields exactly its own fields,
  in the policy, the shell's client-target view, the bundle variables and the
  App payload view.
* ``git grep -P`` finds no branch on a bundle target name in the deploy
  tooling, with a positive control in a scratch repository that proves the
  same command does find each kind of branch.
"""

from __future__ import annotations

import itertools
import subprocess
from pathlib import Path

import pytest

from tests.fixtures.client_profiles import customer_profile, demo_profile
from tools.clients import profile as views
from tools.clients.environment import RawProfile, resolve
from tools.clients.errors import ProfileRefusal
from tools.clients.policy import InstallPolicy, install_policy, policy_controls

REPO = Path(__file__).resolve().parents[2]
RECIPIENTS = ("refresh-alerts@summit.example",)
_FLAGS = (None, "0", "1")
MATRIX = list(itertools.product(("demo", "customer"), ("on", "off"), _FLAGS, _FLAGS))


def _values(install_class: str, refresh: str, presenter: str | None, feeds: str | None) -> dict[str, str]:
    build = demo_profile if install_class == "demo" else customer_profile
    return build(
        MIP_SCHEDULED_REFRESH=refresh,
        MIP_PRESENTER_MODE=presenter,
        MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS=feeds,
    )


@pytest.mark.parametrize(("install_class", "refresh", "presenter", "feeds"), MATRIX)
def test_the_policy_matrix(install_class: str, refresh: str, presenter: str | None, feeds: str | None) -> None:
    values = _values(install_class, refresh, presenter, feeds)
    refused = install_class == "customer" and "1" in (presenter, feeds)
    if refused:
        with pytest.raises(ProfileRefusal) as caught:
            resolve(RawProfile(values=values))
        expected = {
            name
            for name, value in (("MIP_PRESENTER_MODE", presenter), ("MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS", feeds))
            if value == "1"
        }
        named = {line.split(":", 1)[0] for line in caught.value.lines if "demo-only" in line}
        assert named == expected
        return

    profile = resolve(RawProfile(values=values))
    policy = install_policy(profile)

    assert policy == InstallPolicy(
        install_class=install_class,
        app_env="sandbox" if install_class == "demo" else "prod",
        demo_first_party_feeds=feeds == "1",
        presenter_mode=presenter == "1",
        scheduled_refresh=refresh == "on",
        refresh_recipients=RECIPIENTS if refresh == "on" else (),
    )
    assert policy.pause_status == ("UNPAUSED" if refresh == "on" else "PAUSED")
    controls = {
        "MIP_APP_ENV": policy.app_env,
        "MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS": "1" if feeds == "1" else "0",
        "MIP_PRESENTER_MODE": "1" if presenter == "1" else "0",
        "MIP_SCHEDULED_REFRESH": refresh,
    }
    assert policy_controls(policy) == controls
    target = views.client_target(profile)
    assert {name: target[name] for name in controls} == controls
    assert not set(target.values()) & set(RECIPIENTS), "the shell view carries no recipient"
    assert views.bundle_vars(profile)["scheduled_refresh_pause_status"] == policy.pause_status
    payload = views.app_payload_env(profile)
    assert payload["APP_ENV"] == policy.app_env
    assert payload["MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS"] == controls["MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS"]
    assert payload["MIP_PRESENTER_MODE"] == controls["MIP_PRESENTER_MODE"]


def test_the_policy_never_reads_the_install_class_for_a_demo_flag_default() -> None:
    """A demo install that leaves both flags unset gets both off (no class default)."""

    policy = install_policy(
        resolve(RawProfile(values=demo_profile(MIP_PRESENTER_MODE=None, MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS=None)))
    )
    assert (policy.demo_first_party_feeds, policy.presenter_mode) == (False, False)


def test_a_refresh_without_recipients_is_refused() -> None:
    with pytest.raises(ProfileRefusal) as caught:
        resolve(RawProfile(values=demo_profile(MIP_SCHEDULED_REFRESH_RECIPIENTS=None)))
    assert any(line.startswith("MIP_SCHEDULED_REFRESH_RECIPIENTS: is required") for line in caught.value.lines)


def test_install_policy_refuses_a_demo_flag_on_a_customer_even_past_the_profile_rules() -> None:
    """Defence in depth: a profile built without its validators still cannot yield such a policy."""

    customer = resolve(RawProfile(values=customer_profile()))
    for field in ("presenter_mode", "demo_first_party_feeds"):
        forged = customer.model_copy(update={field: "1"})
        with pytest.raises(ProfileRefusal, match="demo-only and refused for a customer install"):
            install_policy(forged)
    silent = customer.model_copy(update={"scheduled_refresh": "on", "scheduled_refresh_recipients": ()})
    with pytest.raises(ProfileRefusal, match="MIP_SCHEDULED_REFRESH_RECIPIENTS"):
        install_policy(silent)


# ------------------------------------------- no bundle target-name branch


# Each pattern is one kind of target-name branch. The retired names cover the
# install-policy plumbing that keyed on a target before M1-PR3 (the retired CI
# install's own name has a ratchet test of its own).
_NAMES = r"(dev|prod|client|ci|staging)"
BRANCH_PATTERNS = (
    rf"\$\{{?TARGET\}}?\"?\s*(==|!=)\s*\"?{_NAMES}\b",
    rf"\b(args\.)?target\s*(==|!=)\s*[\"']{_NAMES}[\"']",
    rf"[\"']{_NAMES}[\"']\s*(==|!=)\s*(args\.)?target\b",
    r"_TARGETS\s*=\s*frozenset\(\{[^}\n]*[\"'](dev|prod|staging)[\"']",
    r"\b(TARGET_CLASS|_DEV_CLASS_TARGETS|is_live_install|MIP_ALLOW_DEMO_FIRST_PARTY_IN_PROD)\b|--live-install",
)
SCANNED = ("scripts", "tools", "jobs", "pipelines", "backend", "Makefile", ".github/workflows")
# The one allowed comparison: deploy.sh's refusal of any target but the one
# deployable target. It is the target allowlist, not a policy branch.
ALLOWED = {"scripts/deploy.sh": ('if [[ "$TARGET" != "client" ]]; then',)}
_CONTROL_LINES = {
    "control.sh": (
        'if [[ "$TARGET" == "dev" ]]; then',
        'if [[ "${TARGET}" != prod ]]; then',
        "  _SCHEDULED_REFRESH_ARGS+=(--live-install)",
        'case "$TARGET_CLASS" in',
    ),
    "control.py": (
        'if target == "prod":',
        "if args.target != 'ci':",
        'if "client" == target:',
        '_DEV_CLASS_TARGETS = frozenset({"dev", "staging"})',
        "_VERIFIER_OPTIONAL_BUNDLE_TARGETS = frozenset({'', 'dev'})",
        "def is_live_install(*, install_class):",
        'if env.get("MIP_ALLOW_DEMO_FIRST_PARTY_IN_PROD"):',
    ),
}


def _git_grep(cwd: Path, *paths: str) -> list[tuple[str, str]]:
    """(path, line) for every match of any pattern, through ``git grep -P``."""

    command = ["git", "grep", "-n", "-I", "-P"]
    for pattern in BRANCH_PATTERNS:
        command += ["-e", pattern]
    completed = subprocess.run(
        [*command, "--", *paths], cwd=cwd, capture_output=True, text=True, check=False, timeout=120
    )
    assert completed.returncode in (0, 1), completed.stderr
    found = []
    for raw in completed.stdout.splitlines():
        path, _number, line = raw.split(":", 2)
        found.append((path, line.strip()))
    return found


def test_the_grep_finds_each_kind_of_target_name_branch(tmp_path: Path) -> None:
    """Positive control: the same git grep -P, over a scratch repository that holds one of each."""

    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True, timeout=60)
    for name, lines in _CONTROL_LINES.items():
        (tmp_path / name).write_text("\n".join(lines) + "\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, timeout=60)

    found = _git_grep(tmp_path, ".")

    expected = {(name, line.strip()) for name, lines in _CONTROL_LINES.items() for line in lines}
    assert set(found) == expected


def test_no_target_name_branch_remains_in_the_deploy_tooling() -> None:
    found = _git_grep(REPO, *SCANNED)

    unexpected = [(path, line) for path, line in found if line not in ALLOWED.get(path, ())]
    assert unexpected == []
    assert {(path, line) for path, line in found} == {
        (path, line) for path, lines in ALLOWED.items() for line in lines
    }, "the allowlist names only what the tree holds"
