"""The owner break-glass procedure, as the docs state it, against the code (wave 7c).

The procedure is the owner's, so what the docs tell them is part of the
control. These pins are the review's rules: never cancel a live run inside a
window (a cancel kills the runner long before its EXIT trap could re-pin
blue); wait out ``STARTING`` and never start twice; the release needs the
dead run's lease to expire; plan right before the release, because the
dead-man's marker state is in the digest. And the reason the ack exists: the
shape check cannot see an env value or a ``value_from``.
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

from tests.unit.test_docs_commands_parse import BREAK_GLASS_HEADING, _bash_blocks
from tools.clients import dispatch_acks
from tools.databricks import app_deployment_lease as lease
from tools.databricks import app_start_deployment as starts
from tools.databricks import quiesce_window_recovery as recovery
from tools.databricks.app_deployment_owner_start_repin import REFUSAL_CODES
from tools.databricks.operation_ack import ACK_ENV, PURPOSES, REMEDY

ROOT = Path(__file__).resolve().parents[2]
REPIN_HEADING = "### The release that re-pins an owner start"
NEXT_HEADING = "## App Python dependencies: the deps-digest line"
# What the first draft of this procedure said, and the review refused (threat T5).
CANCEL_ADVICE = (
    "cancel it first",
    "cancel the run first",
    "cancel it:",
    "its EXIT trap starts and re-pins blue",
    "its EXIT trap re-pins blue",
)


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return " ".join(text.split())


def _break_glass() -> str:
    text = _read("docs/deployment.md")
    assert text.count(BREAK_GLASS_HEADING) == 1, "the heading the doc tests anchor on, verbatim"
    assert BREAK_GLASS_HEADING == "### Owner break-glass: a raw `apps start`"
    start = text.index(BREAK_GLASS_HEADING)
    assert start < text.index(REPIN_HEADING) < text.index(NEXT_HEADING)
    return text[start : text.index(NEXT_HEADING)]


def _reads_then_start(section: str) -> tuple[list[str], list[str], str]:
    """The first bash block (the reads), the next one (the start) and the prose between them."""

    fences = list(re.finditer(r"```bash\n(.*?)\n```", section, flags=re.DOTALL))
    assert len(fences) >= 2
    between = section[fences[0].end() : fences[1].start()]
    return fences[0].group(1).splitlines(), fences[1].group(1).splitlines(), _flat(between)


def test_the_break_glass_block_reads_the_state_before_the_one_start() -> None:
    # The reads gate the start, so the start is never in the paste that reads them.
    reads, start, between = _reads_then_start(_break_glass())
    assert [line.split()[0:3] for line in reads[1:]] == [["databricks", "apps", "get"]]
    assert "quiesce_window status --unverified" in reads[0] and ".compute_status.state" in reads[1]
    assert len(reads) == 2 and not any("apps start" in line for line in reads), "the two reads, no start"
    assert [line.split()[0:3] for line in start] == [["databricks", "apps", "start"]], "one start, after the reads"
    assert "start only a `STOPPED` App" in between and "never pasted with the reads" in between
    runbook = _read("docs/clients/first-client-cutover.md")
    glass = runbook[runbook.index("**If the outage cannot wait for the dead-man") : runbook.index("## Step 13.")]
    reads, start, between = _reads_then_start(glass)
    assert sorted(line.split()[0:3] for line in reads) == [
        ["DATABRICKS_CONFIG_PROFILE=<owner-profile>", "python", "-m"],
        ["databricks", "apps", "get"],
    ]
    assert not any("apps start" in line for line in reads), "the runbook's reads carry no start either"
    assert [line.split()[0:3] for line in start] == [["databricks", "apps", "start"]], "one start, in its own block"
    assert "Start only a `STOPPED` App, once" in between and "never pasted with the reads" in between


def test_the_procedure_never_tells_the_owner_to_cancel_a_live_run() -> None:
    section = _flat(_break_glass())
    runbook = _flat(_read("docs/clients/first-client-cutover.md"))
    for advice in CANCEL_ADVICE:
        assert advice not in section and advice not in runbook, advice
    assert "**Never cancel a live run inside a window.**" in section
    assert "SIGINT, SIGTERM 7.5 s later and kills the process tree 2.5 s after that" in section
    assert "a cancelled run does not restore blue" in section
    assert "Never cancel a live run inside its window" in runbook


def test_the_procedure_waits_out_starting_and_never_starts_twice() -> None:
    section = _flat(_break_glass())
    assert "wait until the compute is `ACTIVE` or `STOPPED`, never `STARTING`," in section
    assert "Never start a `STARTING` App: a second start leaves two deployments nobody journaled (`owner_start_ambiguous`), which has no governed exit." in section
    assert "If the journal reads `window_starting` and the compute is `ACTIVE`, the dead run's own start is serving: start nothing" in section
    assert "start only a `STOPPED` App" in section
    runbook = _flat(_read("docs/clients/first-client-cutover.md"))
    assert "wait until the compute is `ACTIVE` or `STOPPED` (never `STARTING`)" in runbook
    assert "Start only a `STOPPED` App, once" in runbook


def test_the_release_waits_for_the_dead_runs_lease_and_the_plan_comes_right_before_it() -> None:
    section = _flat(_break_glass())
    assert timedelta(hours=4) == lease.LEASE_TTL, "the docs name four hours"
    assert "only after the dead run's lease has expired, four hours after its last heartbeat (`LEASE_TTL`)" in section
    assert "**Dispatch a plan right before the release**" in section
    assert "changes at the dead-man's deadline (`absent` becomes `deadman_skipped`), so a digest printed before the deadline is refused after it: plan again." in section
    runbook = _flat(_read("docs/clients/first-client-cutover.md"))
    assert "four hours after its last heartbeat" in runbook and "(`absent` becomes `deadman_skipped`)" in runbook


def test_the_docs_say_why_the_ack_is_always_required() -> None:
    section = _flat(_break_glass())
    assert "**Why the ack is always required.** That shape check cannot see an env value or a `value_from`: spike S7 measured that `get_deployment` returns env NAMES only." in section
    assert "reads exactly like a start" in section and "it is never inferred, and a tool never supplies it" in section
    module = _flat(_read("tools/databricks/quiesce_window_recovery.py"))
    assert "it shows env NAMES only, never a value or a ``value_from``" in module


def test_every_refusal_and_the_guard_are_documented_and_the_ack_is_release_only() -> None:
    section = _break_glass()
    for code in sorted(REFUSAL_CODES - {"owner_start_usage"}):
        assert f"`{code}`" in section, code
    assert f"`{starts.BASELINE_MOVED}`" in section and "before any journal write, start or stop" in _flat(section)
    assert dispatch_acks.OPERATION_ACKS[recovery.PURPOSE].phases == frozenset({"release"})
    assert recovery.PURPOSE in PURPOSES and ACK_ENV[recovery.PURPOSE] == recovery.ACK_ENV
    assert "operation_acks owner_start_repin=<its sha256>" in REMEDY[recovery.PURPOSE]
    assert "admitted in the release phase only" in _flat(section)


def test_no_documented_shell_block_exports_the_ack() -> None:
    """A local run's export is prose: a bash block's only environment is the owner's CLI profile."""

    docs = ("docs/deployment.md", "docs/clients/first-client-cutover.md", "docs/clients/onboarding.md", "docs/clients/review-modes.md")
    for doc in docs:
        for block in re.findall(r"```(?:bash|sh|shell)?\n(.*?)\n\s*```", _read(doc), flags=re.DOTALL):
            assert recovery.ACK_ENV not in block, doc
    assert f"`{recovery.ACK_ENV}`" in _break_glass(), "named in prose"


def test_the_dispatch_lines_carry_the_ack_on_a_release_and_never_on_a_plan() -> None:
    lines = [
        line.strip()
        for doc in ("docs/deployment.md", "docs/clients/first-client-cutover.md")
        for line in _read(doc).splitlines()
        if "gh workflow run deploy-dev.yml" in line and "owner_start_repin=" in line
    ]
    assert len(lines) == 2, "one in the deploy guide, one in the runbook's step 12"
    for line in lines:
        assert "-f phase=release " in line and "-f owner_attestation=<profile-ack>" in line
        acks_text = re.search(r'operation_acks="([^"]+)"', line)
        assert acks_text is not None and acks_text.group(1).startswith("owner_start_repin=<sha256> ")
        assert acks_text.group(1).endswith(" metastore_app_grants=<sha256>"), "the attestation stays last"
    runbook = _read("docs/clients/first-client-cutover.md")
    step_12 = runbook[runbook.index("## Step 12. Release (run 1)") : runbook.index("## Step 13.")]
    assert lines[1] in step_12 and "repin --from-owner-start" in step_12
