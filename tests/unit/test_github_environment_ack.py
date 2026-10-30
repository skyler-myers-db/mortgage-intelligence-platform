"""What the GitHub-environment plan's digest pins, against a fake gh on PATH.

* The ``binding`` covers every identifying value of the resolved profile,
  not only those the plan writes: a plan that writes one low-entropy secret
  (a host, an email) never prints a hash of that value alone, which anyone
  could confirm a guess against.
* It covers the signed-in maintainer, the one required reviewer the plan
  installs: an apply under another gh login refuses the reviewed digest.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests.fixtures.client_profiles import demo_profile
from tests.fixtures.fake_gh import MAINTAINER, FakeGitHub, install_fake_gh
from tests.fixtures.github_environment_harness import plan_and_apply, run_env_tool, write_profile
from tools.clients.bootstrap_plan import canonical_json

CLIENT = "fictional-demo"


def _drop_secret(fake: FakeGitHub, name: str) -> None:
    state = fake.state()
    del state["environments"][CLIENT]["secrets"][name]
    fake.write(state)


def _applied_without(root: Path, values: dict[str, str], dropped: str) -> FakeGitHub:
    """A converged environment that then lost one identifying secret."""

    fake = install_fake_gh(root / "github")
    env = write_profile(root / "input" / "client.env", values)
    _planned, applied = plan_and_apply(fake, env)
    assert applied.code == 0, applied.err
    _drop_secret(fake, dropped)
    return fake


@pytest.mark.parametrize("dropped", ["DATABRICKS_HOST", "MIP_ADMIN_EMAILS"])
def test_the_binding_is_never_a_hash_of_the_one_value_a_plan_writes(tmp_path: Path, dropped: str) -> None:
    values = demo_profile()
    # The same install twice, but for a metastore id the plan does not write.
    other = demo_profile(MIP_EXPECT_METASTORE_ID="00000000-0000-4000-8000-00000000a002")
    plans = []
    for name, profile in (("one", values), ("two", other)):
        fake = _applied_without(tmp_path / name, profile, dropped)
        planned = run_env_tool(fake, "--plan", env_file=tmp_path / name / "input" / "client.env")
        assert planned.code == 0, planned.err
        plans.append(planned.document())
    one, two = plans
    assert [step["op"] + ":" + step["name"] for step in one["steps"]] == [f"set_secret:{dropped}"]
    assert one["steps"] == two["steps"]
    # A guess at the one written value, with the public maintainer id, does not confirm it.
    for guess in ({dropped: values[dropped]}, {}):
        for key in ("secrets", "identifying"):
            oracle = canonical_json({"maintainer_id": MAINTAINER["id"], key: guess})
            assert hashlib.sha256(oracle).hexdigest() != one["binding"]
    assert one["binding"] != two["binding"]


@pytest.mark.parametrize("profile", ["complete", "skeleton"])
def test_an_apply_under_another_maintainer_refuses_the_reviewed_plan(tmp_path: Path, profile: str) -> None:
    fake = install_fake_gh(tmp_path / "github")
    values = demo_profile() if profile == "complete" else demo_profile(MIP_IDENTITY_IDS_JSON=None)
    env = write_profile(tmp_path / "input" / "client.env", values)
    planned = run_env_tool(fake, "--plan", env_file=env)
    assert planned.code == 0, planned.err
    # On a new repository no step names the reviewer's id: only the binding does.
    assert str(MAINTAINER["id"]) not in planned.out
    state = fake.state()
    state["maintainer"] = {"login": "someone-else", "id": 5151}
    fake.write(state)
    fake.clear_log()
    applied = run_env_tool(fake, "--apply", "--ack-plan", planned.digest, env_file=env)
    assert applied.code == 2 and "does not match the plan computed now" in applied.err
    assert fake.mutations() == []
