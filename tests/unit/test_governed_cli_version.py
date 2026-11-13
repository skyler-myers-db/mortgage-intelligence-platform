"""The governed deploy job installs exactly the Databricks CLI the gates were reviewed on.

The plan parser and gate, the instance ack, the App start reader, the
scheduled-refresh posture, the legacy-root archive and every cutover spike
were source-reviewed or measured on one CLI version, ``REVIEWED_CLI_VERSION``
in tools/databricks/bundle_plan_shape.py. ``databricks.yml``'s
``databricks_cli_version`` is only a floor, so it cannot say which CLI the
governed job runs; deploy-dev.yml's ``databricks/setup-cli`` pin does.

With no ``with:``, setup-cli installs the version its own ``VERSION`` file
names (setup_release.sh reads it when ``inputs.version`` is empty). That file
is upstream, so this test needs no network: it pins the (commit, version)
pair verified once, out of band, and holds the workflow line, its trailing tag
comment and the reviewed-version constant equal to it. Changing any one of
them alone is red. To move to another CLI, re-review the gates on it,
re-verify the pair (the tag must resolve to a commit whose VERSION file reads
the version), then change all of them together:

    gh api repos/databricks/setup-cli/git/ref/tags/v<version>
    gh api 'repos/databricks/setup-cli/contents/VERSION?ref=<sha>' --jq .content | base64 -d
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from tests.fixtures import bundle_plan_fixtures
from tools.databricks import bundle_plan_gate, bundle_plan_shape
from tools.databricks.bundle_plan_shape import PLAN_VERSION, REVIEWED_CLI_VERSION

REPO = Path(__file__).resolve().parents[2]
DEPLOY_DEV = REPO / ".github" / "workflows" / "deploy-dev.yml"
DEPLOYMENT_DOC = REPO / "docs" / "deployment.md"

# databricks/setup-cli tag v1.10.0 is this commit (a lightweight tag, object
# type commit), and its VERSION file reads 1.10.0 (verified 2026-11-13).
REVIEWED_SETUP_CLI = ("8821c27b1fe57472264eeb6e50c1f31d69552f28", "1.10.0")

_PIN_LINE = re.compile(
    r"      - uses: databricks/setup-cli@(?P<sha>[0-9a-f]{40}) # v(?P<version>\d+\.\d+\.\d+)"
)
# Every way a runner gets a Databricks CLI, in any case (as the workflow
# contract test casefolds action names): the setup-cli action or its raw
# install.sh, or a CLI release asset fetched directly.
_INSTALL_SITE = re.compile(
    r"databricks/setup-cli|databricks/cli/releases|databricks_cli_(?!version\b)", re.IGNORECASE
)

# Review notes that name the CLI version they were checked on. Moving to
# another CLI has to touch, and so re-review, each one.
_REVIEW_PROVENANCE = {
    "databricks.yml": ("Verified on CLI {tag} (", "targets: CLI {tag}, read-only"),
    "docs/deployment.md": ("derived from the Databricks CLI {tag} source",),
    "tools/clients/archive_legacy_root.py": ("CLI {tag} offer list",),
    "tools/databricks/app_start_deployment.py": ("CLI {tag}, the SDK pinned in",),
    "tools/databricks/bundle_plan_instance_ack.py": ("database_instance.go`` at {tag})",),
    "tools/databricks/scheduled_refresh_posture.py": ("(CLI {tag}:",),
}


def _setup_cli_lines(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return [line for line in text.splitlines() if _INSTALL_SITE.search(line)]


def test_the_governed_job_installs_the_reviewed_cli_version() -> None:
    lines = _setup_cli_lines(DEPLOY_DEV)
    assert len(lines) == 1, "deploy-dev.yml installs the CLI exactly once"
    pin = _PIN_LINE.fullmatch(lines[0])
    assert (
        pin is not None
    ), "the setup-cli step is a full commit sha with its `# vX.Y.Z` tag comment"

    assert (pin["sha"], pin["version"]) == REVIEWED_SETUP_CLI
    assert pin["version"] == REVIEWED_CLI_VERSION

    workflow = yaml.safe_load(DEPLOY_DEV.read_text(encoding="utf-8"))
    steps = [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if str(step.get("uses", "")).casefold().startswith("databricks/setup-cli@")
    ]
    assert [step["uses"] for step in steps] == [f"databricks/setup-cli@{REVIEWED_SETUP_CLI[0]}"]
    # `uses` alone: a `with: version` would install that version instead of the
    # VERSION file's, and an `env`, `if` or `continue-on-error` could swap or
    # skip the install.
    assert set(steps[0]) == {"uses"}


def test_no_other_workflow_or_action_installs_a_databricks_cli() -> None:
    github = REPO / ".github"
    installers = sorted(
        path.relative_to(REPO).as_posix()
        for path in github.rglob("*")
        if path.is_file() and _setup_cli_lines(path)
    )

    assert installers == [".github/workflows/deploy-dev.yml"]


def test_the_gates_name_the_reviewed_cli_version() -> None:
    tag = f"v{REVIEWED_CLI_VERSION}"

    assert (
        f"Databricks CLI {tag}, plan_version {PLAN_VERSION})"
        in bundle_plan_gate.REMEDIES["malformed_plan"]
    )
    assert f"the installed\ntag, {tag}, not from a live capture" in (
        bundle_plan_shape.__doc__ or ""
    )
    assert f"the reviewed {tag} shape" in (bundle_plan_shape.PlanShapeError.__doc__ or "")
    assert bundle_plan_fixtures.CLI_VERSION == REVIEWED_CLI_VERSION


def test_the_review_provenance_names_the_reviewed_cli_version() -> None:
    tag = f"v{REVIEWED_CLI_VERSION}"
    missing = [
        (site, phrase.format(tag=tag))
        for site, phrases in _REVIEW_PROVENANCE.items()
        for phrase in phrases
        if phrase.format(tag=tag) not in " ".join((REPO / site).read_text(encoding="utf-8").split())
    ]

    assert missing == []


def test_the_deployment_doc_names_the_cli_the_governed_job_installs() -> None:
    text = " ".join(DEPLOYMENT_DOC.read_text(encoding="utf-8").split())

    assert f"It installs Databricks CLI {REVIEWED_CLI_VERSION}, the version the plan gate" in text
    assert f"pinned to its `v{REVIEWED_CLI_VERSION}` tag's commit with no `with:`" in text
