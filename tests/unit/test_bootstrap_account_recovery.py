"""Re-running the bootstrap after a step failed mid-apply.

A failed step stops the run and names the step and exception class only.
The next ``--plan`` holds only what is left:

* the data-owners group is created holding the deployer and jobs in one
  call, so a failure right after it leaves a group the next plan owns;
* a service principal created before its pin reached the sink is refused
  by name; the failure says how to recover (``--adopt``), and adopting it
  converges.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from tests.fixtures.bootstrap_harness import FakeSink, plan_and_apply, run_tool, write_env
from tests.fixtures.fake_databricks_account import FakeEstate, assert_safe

RECOVERY = "Re-run --plan. If it refuses a service principal this run created, confirm it and name it with --adopt"


@pytest.fixture
def estate() -> Iterator[FakeEstate]:
    found = FakeEstate()
    yield found
    assert_safe(found)


def test_a_failure_right_after_the_group_is_created_leaves_a_group_the_next_plan_owns(
    estate: FakeEstate, tmp_path: Path
) -> None:
    estate.fail_after = ("groups", "create")
    sink = FakeSink()
    _planned, applied = plan_and_apply(estate, write_env(tmp_path / "a.env"), sink)
    assert applied.code == 2 and "failed: RuntimeError" in applied.err and RECOVERY in applied.err
    (group_id,) = estate.groups
    deployer, jobs = (estate.by_name(f"acme-{role}") for role in ("deployer", "jobs"))
    assert deployer is not None and jobs is not None
    assert estate.groups[group_id][1] == {deployer.scim_id, jobs.scim_id}
    pinned = write_env(tmp_path / "pinned.env", pins=sink.pins)
    again = run_tool(estate, "--plan", env_file=pinned, sink=sink)
    assert again.code == 0, again.err
    assert not [line for line in again.steps() if "group" in line.split()[0]]
    resumed = run_tool(estate, "--apply", "--ack-plan", again.digest, env_file=pinned, sink=sink)
    assert resumed.code == 0 and "converged" in resumed.out, resumed.err
    assert list(estate.groups) == [group_id]


class _PinWriteFailsOnce(FakeSink):
    def put_identity_ids(self, pins: Mapping[str, str]) -> None:
        if self.pin_writes == 0:
            self.pin_writes += 1
            raise RuntimeError("gh secret set failed")
        super().put_identity_ids(pins)


def test_a_service_principal_created_before_its_pin_is_recovered_with_adopt(estate: FakeEstate, tmp_path: Path) -> None:
    env = write_env(tmp_path / "a.env")
    sink = _PinWriteFailsOnce()
    _planned, applied = plan_and_apply(estate, env, sink)
    assert applied.code == 2
    assert "step 1 (create_service_principal) failed: RuntimeError; 1 mutation(s) applied before it" in applied.err
    assert RECOVERY in applied.err
    deployer = estate.by_name("acme-deployer")
    assert deployer is not None and deployer.application_id not in applied.err
    refused = run_tool(estate, "--plan", env_file=env, sink=sink)
    assert refused.code == 2
    assert "deployer: a service principal named acme-deployer exists that this bootstrap did not pin" in refused.err
    adopt = ("--adopt", f"deployer={deployer.application_id}")
    _planned, recovered = plan_and_apply(estate, env, sink, *adopt)
    assert recovered.code == 0 and "converged" in recovered.out, recovered.err
    assert sink.pins is not None and sink.pins["deployer"] == deployer.application_id
    assert [sp.display_name for sp in estate.sps.values()].count("acme-deployer") == 1
