"""The docs never present a NO-GO path as the live App's recovery (moved here in M0-PR3).

These guards lived in the retired CI-isolation tests, but none of them is about
the retired CI staging install. The live ``mip-app``'s command of record, an
operator ``./scripts/deploy.sh -t dev``, is NO-GO today (no reserved principals
in the live workspace, the deploy never creates one, and the only durable
credential sink is this repository's secrets), so docs/deployment.md's parked
CI section and docs/runbook.md section 1.2 escalate to the owner instead of
starting it. The section-1.2 incident recovery is where an overclaim would do
harm, so every sentence there and in the CI section goes through the ban below.

The same section states the reserved-identity contract, names no application,
account or workspace id, and finds other teams' catalogs at run time rather
than naming them; GRANTS.md gives the metastore-wide remediation no CI command
of record.
"""

from __future__ import annotations

import itertools
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DEPLOYMENT_DOC = ROOT / "docs" / "deployment.md"
RUNBOOK = ROOT / "docs" / "runbook.md"
GRANTS_DOC = ROOT / "docs" / "security" / "GRANTS.md"
WORKFLOWS_README = ROOT / ".github" / "workflows" / "README.md"
DEPLOY_DEV = ROOT / ".github" / "workflows" / "deploy-dev.yml"
CI_HEADING = "## CI deploy (deploy-dev.yml): parked, and the release-gate bootstrap"
IDENTITIES_HEADING = "### Per-run M2M identities"
LIVE_NO_GO = "**The live install is outside this bootstrap, and its governed deploy is NO-GO.**"


def _doc_section(heading: str) -> str:
    """The section's lines up to the next heading of its level or higher, outside code fences."""

    lines = DEPLOYMENT_DOC.read_text(encoding="utf-8").splitlines()
    level = len(heading) - len(heading.lstrip("#"))
    body: list[str] = []
    fenced = False
    for line in lines[lines.index(heading) + 1 :]:
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and re.match(rf"#{{1,{level}}} ", line):
            break
        body.append(line)
    return "\n".join(body)


def _flat(text: str) -> str:
    return " ".join(text.split())


def _numbered_item(block: str, number: int) -> str:
    return block.split(f"\n{number}. ", 1)[1].split(f"\n{number + 1}. ", 1)[0]


def _owner_steps() -> str:
    return _doc_section(CI_HEADING).split("**Owner steps", 1)[1].split("**Integrator steps", 1)[0]


def _runbook_recovery() -> str:
    """docs/runbook.md section 1.2's stopped-App recovery, up to section 1.3."""

    runbook = RUNBOOK.read_text(encoding="utf-8")
    start = runbook.index("If the Lakebase instance or App is stopped")
    return runbook[start : runbook.index("### 1.3", start)]


# ------------------------------------------------------- the live-recovery ban

# An operator deploy of the live install, however it is spelled in prose.
_OPERATOR_LIVE_DEPLOY = r"`?(?:\./)?scripts/deploy\.sh`? -t dev`?"
# A recovery verb that reaches the operator deploy, or an un-negated dispatch for the live App, in one sentence.
_LIVE_RECOVERY_OVERCLAIMS = (
    re.compile(r"(?i)\b(?:recover|restor|restart|redeploy|repair)\w*\b[^.]{0,80}?(?:" + _OPERATOR_LIVE_DEPLOY + ")"),
    re.compile(
        r"(?i)(?<!not )(?<!never )(?<!n't )\bdispatch\b (?:the )?(?:workflow|`?deploy-dev(?:\.yml)?`?)"
        r"[^.]{0,60}?\bfor the live\b"
    ),
    re.compile(r"(?i)\brecovers? through an operator\b"),
)
# Any sentence holding both an operator deploy and recovery vocabulary, in
# either order and whatever the verb, is an overclaim. No sentence in the CI
# section or the runbook recovery needs both; a negation does not excuse one
# (rephrase with "start"). The bare `deploy.sh`, `--target dev`/`--target=dev`
# and `make deploy-dev` (whose recipe runs `./scripts/deploy.sh`, target dev)
# count as the operator deploy too.
_OPERATOR_LIVE_DEPLOY_RE = re.compile(
    r"(?i)(?:(?:\./)?scripts/)?\bdeploy\.sh`? (?:-t|--target)[ =]dev\b|\bmake deploy-dev\b"
)
_RECOVERY_VOCABULARY = re.compile(
    r"(?i)\b(?:recover|restor|restart|re-?deploy|repair|reviv|resurrect)\w*"
    r"|\b(?:bring|brings|bringing|brought|get|gets|getting|got|come|comes|coming|came)\b[^.]{0,40}?\bback\b"
    r"|\broll(?:s|ed|ing)?\b[^.]{0,40}?\bforward\b"
)
# A dispatch or the deploy-dev workflow named beside the live App and recovery
# vocabulary, in any order ("Restore the live App: dispatch ...").
_LIVE_APP_RE = re.compile(r"(?i)\blive (?:dev )?App\b")
_DISPATCH_RE = re.compile(r"(?i)\bdispatch\w*\b|\bdeploy-dev(?:\.yml)?\b")
# Sentence ends in flattened Markdown: end punctuation, whitespace, then a capital after optional markup.
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[`*\"'(\[]*[A-Z])")


def assert_no_live_recovery_overclaim(text: str) -> None:
    """No sentence presents an operator deploy or a dispatch as the live App's recovery."""

    for pattern in _LIVE_RECOVERY_OVERCLAIMS:
        match = pattern.search(text)
        assert match is None, match.group(0) if match else ""
    for sentence in _SENTENCE_BREAK.split(" ".join(text.split())):
        both = _OPERATOR_LIVE_DEPLOY_RE.search(sentence) and _RECOVERY_VOCABULARY.search(sentence)
        assert not both, sentence
        dispatched = _LIVE_APP_RE.search(sentence) and _DISPATCH_RE.search(sentence)
        assert not (dispatched and _RECOVERY_VOCABULARY.search(sentence)), sentence


@pytest.mark.parametrize(
    "overclaim",
    [
        "so it can never deploy or recover the live dev App, which an operator recovers with `./scripts/deploy.sh -t dev`.",
        "Restart it with `./scripts/deploy.sh -t dev`.",
        "Redeploy the live App: run ./scripts/deploy.sh -t dev now.",
        "Do not start that deploy during an incident, and dispatch the workflow below for the live App.",
        "Dispatch deploy-dev.yml for the live App.",
        "The live App recovers through an operator.",
        "Run `./scripts/deploy.sh -t dev` to recover the live dev App.",
        "An operator brings the live App back with `./scripts/deploy.sh -t dev`.",
        "Get the live App back up: ./scripts/deploy.sh -t dev.",
        "To restore service, an operator runs scripts/deploy.sh -t dev from a clean checkout.",
        "An operator recovers the live dev App with `make deploy-dev`.",
        "An operator restores the live dev App with `deploy.sh -t dev`.",
        "Recover the live App with `scripts/deploy.sh --target dev`.",
        "The live App comes back once an operator runs `./scripts/deploy.sh -t dev`.",
        "An operator re-deploys the live App with `./scripts/deploy.sh -t dev`.",
        "An operator rolls the live App forward with `./scripts/deploy.sh -t dev`.",
        "Restore the live App: dispatch deploy-dev.yml.",
    ],
)
def test_the_live_recovery_overclaim_ban_catches_paraphrases(overclaim: str) -> None:
    with pytest.raises(AssertionError):
        assert_no_live_recovery_overclaim(overclaim)


@pytest.mark.parametrize(
    "negated",
    [
        "Never dispatch the workflow below for the live App.",
        "Do not start that deploy during an incident, and do not dispatch the workflow below for the live App.",
        "Don't dispatch deploy-dev.yml for the live App.",
        "Never start `./scripts/deploy.sh -t dev` against the live workspace during an incident; escalate to the owner.",
        "No governed path recovers it today. Its command of record, an operator `./scripts/deploy.sh -t dev`, is NO-GO.",
        # Production and a CI install are not the live dev install.
        "To restore production, an operator runs `scripts/deploy.sh -t prod`.",
        "deploy-dev.yml recovers only a CI install.",
    ],
)
def test_the_live_recovery_overclaim_ban_lets_the_refusals_through(negated: str) -> None:
    """The ban's controls: the refusals the docs actually use, and the 'Never dispatch' false positive."""

    assert_no_live_recovery_overclaim(negated)


def test_the_live_app_has_no_governed_recovery_path_and_escalates_to_the_owner() -> None:
    """The CI section and the runbook recovery escalate to the owner, and no sentence in either overclaims."""

    flat = _flat(_doc_section(CI_HEADING))
    raw_recovery = _runbook_recovery()
    recovery = _flat(raw_recovery)

    live = flat.split(LIVE_NO_GO, 1)[1].split("**The CI deployer", 1)[0]
    assert "Nothing in this section deploys, recovers or restarts the live `mip-app`." in live
    assert "`scripts/deploy.sh` never creates one" in live and "`--expected-application-id`" in live
    assert "The only path that mints a usable durable credential cannot serve the live install" in live
    assert "Deploying or recovering the live App is therefore an owner-only decision." in live
    assert "Never start `./scripts/deploy.sh -t dev` against the live workspace during an incident" in live

    whole = _flat(DEPLOYMENT_DOC.read_text(encoding="utf-8"))
    assert "`./scripts/deploy.sh -t dev` or dispatch `deploy-dev.yml`" not in whole, "not equivalent first installs"

    assert recovery.index("**The live dev App**") < recovery.index("**A CI install.**")
    assert recovery.index("**A CI install.**") < recovery.index("gh workflow run deploy-dev.yml")
    for anchor in (
        "No governed path recovers it today.",
        "is NO-GO on the live workspace",
        f'"{LIVE_NO_GO.strip("*").removesuffix(".")}"',
        "Do not start that deploy during an incident, and do not dispatch the workflow below for the live App.",
        "Escalate to the owner",
        "is an owner-only decision",
        "It is never a recovery path for the live dev App.",
    ):
        assert anchor in recovery, anchor
    assert "A read-only listing on 2026-10-27 found none of the seven reserved principals in the live workspace" in recovery
    assert "The workspace holds none of the seven" not in recovery, "a dated finding, never an undated present fact"
    assert_no_live_recovery_overclaim(flat + " " + recovery)

    dispatch = raw_recovery[raw_recovery.index("gh workflow run deploy-dev.yml") : raw_recovery.index("# Fail closed unless")]
    assert "-f acknowledge_single_maintainer_break_glass=true" in dispatch, "or the run ends at refuse-unacknowledged"
    inputs = yaml.load(DEPLOY_DEV.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)["on"]["workflow_dispatch"]["inputs"]
    assert set(re.findall(r"-f (\w+)=", dispatch)) <= set(inputs), "gh refuses a dispatch input the workflow lacks"


# ------------------------------------------------ the reserved-identity contract


def test_the_owner_steps_state_the_reserved_identity_contract() -> None:
    """Owner step 1: one workspace per install, its seven reserved principals, and no CI-only identities."""

    section = _doc_section(CI_HEADING)
    flat = _flat(section)
    step1 = _flat(_numbered_item(_owner_steps(), 1))

    for anchor in (
        'the seven names in "Per-run M2M identities"',
        "`tools/databricks/m2m_identity_contract.py` `IDENTITY_DEFAULTS`",
        "`provision_m2m_oauth._find_existing_sp`",
        "`agent_runtime_access.AGENT_RUNTIME_DISPLAY_NAME`",
        "CI-only identities cannot coexist with them there",
        "**Never create a second service principal with a reserved display name.**",
        "(`Multiple service principals use reserved display name`)",
        "Resolution: one workspace per install, each with its own seven reserved principals.",
        "**Owner invariant: each workspace keeps its own reserved principals.**",
        "no code checks it",
        "`scripts/deploy.sh` never creates a reserved principal",
        "The only path that mints a usable durable credential, for a new principal or a rotation, is `--set-gh-secrets`",
        "mint 300-second secrets for existing principals and delete them after the probe; none is durable.",
        "`provision_m2m_oauth --pre-app-bootstrap --set-gh-secrets`",
        "only to one GitHub Environment's secrets (`gh secret set --env`, never repository-level ones; `--gh-env` names the environment)",
        "`MIP_M2M_GITHUB_REPOSITORY`",
        "in this repository they belong to the CI install",
        "It stays out of scope until that design is reviewed.",
        "Compare the listings, never a stored `*_CLIENT_ID`",
    ):
        assert anchor in step1, anchor
    # Deploy-time identity probes do mint (and delete) 300-second secrets, so
    # the claim holds only for durable credentials.
    assert not re.search(r"mints a usable credential", flat), "every such claim says durable"
    for probe in ("campaign_treatment_identity_probe", "identity_boundary_probes"):
        source = (ROOT / "tools" / "databricks" / f"{probe}.py").read_text(encoding="utf-8")
        assert re.search(r'_TEMPORARY\w*LIFETIME = "300s"', source), f"{probe}: the doc's 300-second claim"
    for loose in ("never creates a principal", "only governed creation path"):
        assert loose not in flat, loose

    assert "Give the CI install its own identities" not in flat
    assert "CI-only service principal" not in flat
    assert not re.search(r"(?i)\b(?:give|create|assign|use)\b[^.]{0,80}\bCI-only\b", flat), "no instruction to make CI-only identities"
    for row in (
        "`reconcile_historical_agent_endpoints cleanup`",
        "`agent_runtime_access.audit_global_genie_access`",
        "`audit_agent_runtime_foreign_uc_access`",
    ):
        assert row in section, f"workspace-global inventory row {row}"


def test_every_reserved_identity_name_is_in_the_per_run_identities_table() -> None:
    """The contract the runbook cites cannot drift from the code that enforces it."""

    from tools.databricks.agent_runtime_access import AGENT_RUNTIME_DISPLAY_NAME
    from tools.databricks.m2m_identity_contract import IDENTITY_DEFAULTS

    lines = _doc_section(IDENTITIES_HEADING).splitlines()
    first = next(index for index, line in enumerate(lines) if line.startswith("| "))
    table = list(itertools.takewhile(lambda line: line.startswith("|"), lines[first:]))
    named = [match for line in table[2:] for match in re.findall(r"^\| [^|]+ \| `([^`]+)` \|", line)]

    assert sorted(named) == sorted(defaults.sp_name for defaults in IDENTITY_DEFAULTS.values())
    assert len(named) == len(IDENTITY_DEFAULTS) == 7, "the runbook says seven"
    assert IDENTITY_DEFAULTS["agent_runtime"].sp_name == AGENT_RUNTIME_DISPLAY_NAME


# ------------------------------------------------- placeholders, never identifiers

_APPLICATION_ID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
# A Databricks workspace or metastore-scoped numeric id (15-16 digits); dates and counts are far shorter.
_NUMERIC_ID = re.compile(r"(?<![\d.])\d{15,16}(?![\d.])")


@pytest.mark.parametrize(
    "text",
    [
        "set `MIP_CI_DEPLOYER_CLIENT_ID` to 1b2c3d4e-0000-4abc-8def-0123456789ab",
        "never to the live workspace (`1234567890123456`)",
        "workspace_id 123456789012345 on the CI credential",
    ],
    ids=["application-id", "workspace-id", "fifteen-digit-id"],
)
def test_the_identifier_scan_catches_each_shape(text: str) -> None:
    assert _APPLICATION_ID.search(text) or _NUMERIC_ID.search(text)


def test_the_ci_docs_name_placeholders_never_an_application_account_or_workspace_id() -> None:
    """Per-client identifiers live in GitHub Environments or `.env.local`, never in the docs."""

    texts = {
        "deployment.md CI section": _doc_section(CI_HEADING),
        "runbook.md section 1.2 recovery": _runbook_recovery(),
        "workflows README": WORKFLOWS_README.read_text(encoding="utf-8"),
    }
    for label, text in texts.items():
        assert not _APPLICATION_ID.search(text), f"{label}: placeholders only, never an application or account id"
        assert not _NUMERIC_ID.search(text), f"{label}: never a workspace or account id"

    flat = _flat(texts["deployment.md CI section"])
    assert "`<deployer-application-id>`" in flat, "the placeholder the owner substitutes"
    assert "subject `repo:<owner>/<repo>:environment:dev`" in flat
    assert "never widen it to a branch, a ref or `repo:<owner>/<repo>:*`" in flat
    assert "It has no OAuth secret and no personal access token" in flat
    proof = "Prove the GitHub OIDC deployer identity"
    steps = yaml.safe_load(DEPLOY_DEV.read_text(encoding="utf-8"))["jobs"]["deploy"]["steps"]
    assert proof in [step.get("name") for step in steps]
    assert f'"{proof}" runs next, read-only' in flat


def test_the_uc_blockers_are_found_at_run_time_never_named() -> None:
    """Owner step 7 hands the owner a read-only inventory; no other team's catalog or user handle is written down.

    Every backticked lower-case token in the step is a platform, product or
    API name from the allowlist, so a catalog name or a user handle cannot
    creep back in.
    """

    raw = _numbered_item(_owner_steps(), 7)
    step7 = _flat(raw)

    for probe in (
        "databricks catalogs list -o json",
        'select(.isolation_mode == "OPEN")',
        'databricks grants get catalog "$catalog" -o json',
        'select(.principal == "account users")',
    ):
        assert probe in raw, probe
    assert "Never revoke another team's grant" in step7
    assert not re.search(r"\bowner [a-z]+\.[a-z]+", step7), "no user handle"
    allowed = {"samples", "system", "users", "metastore_id", "workspace_id"}
    tokens = set(re.findall(r"`([a-z_]+)`", raw))
    assert tokens <= allowed, sorted(tokens - allowed)


# ------------------------------------------- GRANTS.md: no CI command of record


def test_grants_gives_foreign_catalog_remediation_no_ci_command_of_record() -> None:
    """The workflow passes the remediation flag as a literal 0; the operator path targets the NO-GO live install."""

    raw = GRANTS_DOC.read_text(encoding="utf-8")
    grants = _flat(raw)

    assert "The command of record is the manual `deploy-dev` workflow" not in grants
    heading = "**Foreign-catalog remediation has no CI command of record (post-W5, 2026-10-27).**"
    assert heading in grants
    # No sentence after the negated heading may name a command of record for it, however paraphrased.
    paragraph = grants.split(heading, 1)[1].split("When an owner-approved run does reach it", 1)[0]
    assert "command of record" not in paragraph.lower()
    assert "the workflow passes `MIP_REMEDIATE_FOREIGN_CATALOG_BINDINGS=0` as a literal, so no dispatch can request the remediation" in paragraph
    env = {
        key: value
        for step in yaml.load(DEPLOY_DEV.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)["jobs"]["deploy"]["steps"]
        for key, value in (step.get("env") or {}).items()
    }
    assert env["MIP_REMEDIATE_FOREIGN_CATALOG_BINDINGS"] == "0", "the doc's literal"
    assert (
        "The operator path, `MIP_REMEDIATE_FOREIGN_CATALOG_BINDINGS=1 ./scripts/deploy.sh -t dev`, targets the live "
        "workspace, whose governed deploy is NO-GO"
    ) in grants
    assert "Any metastore-wide remediation is therefore an owner-only, separately reviewed decision" in grants
    # The owner decision rests on the blast radius the code has:
    # foreign_catalog_binding_manifest refuses a policy that leaves any other
    # metastore workspace unbound, so the hazard is the window between
    # _apply_catalog's isolate and re-bind calls, which a killed run keeps open
    # until a resumed recovery.
    assert (
        "this remediation flips each `OPEN` catalog the policy names to `ISOLATED` and then binds every other "
        "metastore workspace `READ_WRITE`. The signed manifest refuses a policy that omits one of them, that "
        "changes an already-`ISOLATED` catalog's bindings, or that binds the deploying workspace. At completion "
        "only the deploying workspace loses the catalog. Between each catalog's flip and its re-binding, every "
        "other workspace (the live one and every other team's) loses it. A run killed there stays in that narrower "
        "state until a resumed recovery, which never reopens. A workspace assigned to the metastore later never "
        "sees the catalog."
    ) in grants
    assert "every workspace the policy does not bind loses that catalog" not in grants
    assert "must keep the live workspace bound" not in grants, "the manifest already refuses an unbound workspace"
    assert "Its signed manifest preserves every non-MIP metastore workspace as `READ_WRITE`" in grants, "consistency"
    assert "remediate_foreign_catalog_bindings=true" not in raw, "the dispatch input is gone"
