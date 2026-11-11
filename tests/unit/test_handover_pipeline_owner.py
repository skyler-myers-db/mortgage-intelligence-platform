"""Every pipeline dataset is owned by an approved principal its pipeline runs as live (spike S4).

Spike S4 measured that a pipeline-published streaming table or materialized
view cannot be re-owned by any ALTER or REST owner PATCH, by any actor, and
that its owner flips to the pipeline's run_as the moment that setting changes.
So after the release's bundle apply the governed pipeline's datasets belong to
the jobs identity for good, never to the data-owner group. Before this check
the handover counted such a dataset as ``other_owner`` and asserted nothing.

Because the platform keeps a dataset's owner equal to its pipeline's run_as,
"owned by its run_as" alone passes any identity a pipeline runs as. So the
owner must also be approved: the jobs principal, or the legacy owner before
the release; ``--verify --released`` admits only the jobs principal. The
refusals below that matter are the ones the platform can reach: another
identity running a pipeline into the governed catalog, and a run_as moved off
the jobs principal after the release.

``tests/fixtures/uc_handover_fake.FakeUc`` models those measurements (a refused
owner update, ``set_run_as`` re-owning every dataset at once, a pipeline GET
with the effective run_as and no run_as in its spec), pinned first below.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.errors import NotFound

from tests.fixtures.client_profiles import IDENTITY_IDS, demo_profile
from tests.fixtures.interim_pairs import pairs_from_json
from tests.fixtures.uc_handover_fake import (
    JOBS,
    LEGACY,
    OTHER,
    PIPELINE,
    TARGET,
    FakeUc,
    PipelineOwnerRefused,
)
from tools.clients import handover_pipeline_owner as checker
from tools.clients import handover_uc_ownership as handover
from tools.databricks.uc_interim_owner_pairs import InterimOwnerPairs

DATASETS = ("mip.silver.parcel_rollup", "mip.silver.parcel_stream")
STRAY = "stray-pipeline-PLACEHOLDER"


def _install(pairs: InterimOwnerPairs | None = None) -> handover.Install:
    return handover.Install(
        catalog="mip", sync_catalog="mip_app_state", target=TARGET, pairs=pairs or InterimOwnerPairs(), jobs_principal=JOBS
    )


def _released(tmp_path: Path) -> FakeUc:
    """The release's bundle apply done: the governed pipeline runs as the jobs identity."""

    fake, _pairs = _handed_over(tmp_path)
    fake.set_run_as(PIPELINE, JOBS)
    return fake


def _add_stray(fake: FakeUc, identity: str) -> None:
    """Another pipeline, run as ``identity``, publishing one dataset into the governed catalog."""

    fake.doc["pipelines"][STRAY] = identity
    fake.doc["tables"]["mip.silver.stray_rollup"] = {"owner": identity, "type": "MATERIALIZED_VIEW", "pipeline_id": STRAY}


def _handed_over(tmp_path: Path) -> tuple[FakeUc, InterimOwnerPairs]:
    """Cutover step 7 done: everything but the pipeline datasets moved, their pairs configured."""

    fake = FakeUc()
    handover.apply(fake, _install(), ack=handover.plan(fake, _install()).sha256, journal_path=tmp_path / "j.json")
    rows = handover.plan(fake, _install(), interim_expires=date.today() + timedelta(days=7)).suggested_pairs
    return fake, pairs_from_json(json.dumps(rows))


# --- the measured behaviour the fake models ---------------------------------------


def test_the_fake_refuses_an_owner_change_on_a_pipeline_dataset_and_follows_run_as() -> None:
    fake = FakeUc()
    for name in DATASETS:
        with pytest.raises(PipelineOwnerRefused, match="managed by a DLT pipeline is not supported"):
            fake.tables.update(name, owner=TARGET)
    assert fake.updates == [] and {fake.tables.get(name).owner for name in DATASETS} == {LEGACY}
    pipeline = fake.pipelines.get(pipeline_id=PIPELINE)
    assert pipeline.run_as_user_name == LEGACY and not hasattr(pipeline.spec, "run_as")

    fake.set_run_as(PIPELINE, JOBS)
    assert {fake.tables.get(name).owner for name in DATASETS} == {JOBS}, "the owner flips with run_as"
    assert fake.pipelines.get(pipeline_id=PIPELINE).run_as_user_name == JOBS
    assert fake.tables.get("mip.gold.borrower_360").owner == LEGACY, "positive control: other tables stay"


# --- the end-state assertion -----------------------------------------------------


def test_before_the_release_the_datasets_are_the_legacy_run_as_and_paired(tmp_path: Path) -> None:
    fake, pairs = _handed_over(tmp_path)
    assert handover.verify(fake, _install(pairs)) == []
    datasets = handover.plan(fake, _install(pairs)).pipeline_datasets
    assert [row["full_name"] for row in datasets] == list(DATASETS)
    assert checker.owner_codes(fake, datasets, jobs_principal=JOBS, interim_owner=LEGACY) == []
    assert checker.owner_codes(fake, datasets, jobs_principal=JOBS) == [checker.UNAPPROVED] * 2, "no interim owner"


def test_after_the_release_the_jobs_identity_owns_them_and_no_pair_is_needed(tmp_path: Path) -> None:
    fake = _released(tmp_path)
    current = handover.plan(fake, _install())
    assert current.document["pipeline_managed"] == [], "nothing pipeline-managed is the legacy owner's"
    assert {row["owner"] for row in current.pipeline_datasets} == {JOBS}
    assert handover.verify(fake, _install()) == []
    assert handover.verify(fake, _install(), released=True) == [], "the end state"


def test_the_released_verify_refuses_the_legacy_owner_even_with_its_pairs(tmp_path: Path) -> None:
    fake, pairs = _handed_over(tmp_path)  # the release has not run
    assert handover.verify(fake, _install(pairs)) == [], "positive control: the pre-release state passes"
    assert handover.verify(fake, _install(pairs), released=True) == [checker.UNAPPROVED] * 2


@pytest.mark.parametrize("released", (False, True), ids=("verify", "verify-released"))
def test_another_identity_running_a_pipeline_into_the_governed_catalog_refuses(tmp_path: Path, released: bool) -> None:
    fake = _released(tmp_path)
    _add_stray(fake, OTHER)
    names = [row["full_name"] for row in handover.plan(fake, _install()).pipeline_datasets]
    assert "mip.silver.stray_rollup" in names, "positive control: the stray dataset is enumerated"
    assert handover.verify(fake, _install(), released=released) == [checker.UNAPPROVED]


def test_a_stray_pipeline_run_as_the_legacy_owner_refuses_once_released(tmp_path: Path) -> None:
    fake = _released(tmp_path)
    _add_stray(fake, LEGACY)
    pairs = pairs_from_json(
        json.dumps(handover.plan(fake, _install(), interim_expires=date.today() + timedelta(days=7)).suggested_pairs)
    )
    assert handover.verify(fake, _install(pairs)) == [], "before the release a paired legacy dataset is admitted"
    assert handover.verify(fake, _install(pairs), released=True) == [checker.UNAPPROVED]


@pytest.mark.parametrize("identity", (OTHER, LEGACY), ids=("another-identity", "back-to-legacy"))
def test_a_run_as_moved_off_the_jobs_identity_after_the_release_refuses(tmp_path: Path, identity: str) -> None:
    fake, pairs = _handed_over(tmp_path)  # the step-7 pairs are still configured
    fake.set_run_as(PIPELINE, JOBS)
    fake.set_run_as(PIPELINE, identity)  # drift after the release: the datasets follow it at once
    assert {fake.tables.get(name).owner for name in DATASETS} == {identity}
    assert handover.verify(fake, _install(pairs), released=True) == [checker.UNAPPROVED] * 2
    if identity == OTHER:
        assert handover.verify(fake, _install(pairs)) == [checker.UNAPPROVED] * 2


def test_the_profile_install_takes_the_jobs_principal_from_the_identity_pins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handover, "REPO", tmp_path)  # no .env.local: the profile is the shell's alone
    monkeypatch.setattr(handover.os, "environ", demo_profile())
    install = handover._profile_install()
    assert install.jobs_principal == IDENTITY_IDS["jobs"] and install.jobs_principal != IDENTITY_IDS["deployer"]


def test_a_blank_jobs_principal_refuses_the_check_rather_than_approving_nothing() -> None:
    with pytest.raises(ValueError, match="jobs principal is required"):
        checker.owner_codes(FakeUc(), [], jobs_principal=" ")


def test_a_dataset_owned_by_another_identity_than_its_pipelines_run_as_refuses(tmp_path: Path) -> None:
    fake, _pairs = _handed_over(tmp_path)
    fake.set_run_as(PIPELINE, JOBS)
    # A state the measured API never reached (a lagging flip, a hand edit): the check must still see it.
    fake.doc["tables"]["mip.silver.parcel_stream"]["owner"] = OTHER
    assert handover.verify(fake, _install()) == [checker.NOT_RUN_AS]


def test_matching_interim_pairs_never_mask_a_dataset_its_pipeline_no_longer_runs_as(tmp_path: Path) -> None:
    fake, pairs = _handed_over(tmp_path)
    fake.doc["pipelines"][PIPELINE] = JOBS  # run_as moved, the datasets' owner did not
    codes = handover.verify(fake, _install(pairs))
    assert codes == [checker.NOT_RUN_AS, checker.NOT_RUN_AS], "both still read the legacy owner"


@pytest.mark.parametrize(
    "damage",
    (lambda fake: fake.doc["pipelines"].pop(PIPELINE), lambda fake: fake.doc["pipelines"].__setitem__(PIPELINE, "")),
    ids=("pipeline-gone", "blank-run-as"),
)
def test_an_unreadable_pipeline_run_as_refuses(tmp_path: Path, damage: Any) -> None:
    fake, pairs = _handed_over(tmp_path)
    damage(fake)
    assert handover.verify(fake, _install(pairs)) == [checker.UNREADABLE, checker.UNREADABLE]


def test_a_dataset_whose_listing_lacks_its_pipeline_falls_back_to_its_get_then_refuses() -> None:
    gets: list[str] = []

    def table(name: str) -> Any:
        gets.append(name)
        if name == "c.s.gone":
            raise NotFound("table not found")
        return SimpleNamespace(pipeline_id=PIPELINE if name == "c.s.listed_late" else None)

    workspace = SimpleNamespace(
        tables=SimpleNamespace(get=table),
        pipelines=SimpleNamespace(get=lambda *, pipeline_id: SimpleNamespace(run_as_user_name=JOBS.upper())),
    )
    datasets = [
        {"full_name": "c.s.listed", "owner": JOBS, "pipeline_id": PIPELINE},
        {"full_name": "c.s.listed_late", "owner": JOBS, "pipeline_id": ""},
        {"full_name": "c.s.unlinked", "owner": JOBS, "pipeline_id": ""},
        {"full_name": "c.s.gone", "owner": JOBS, "pipeline_id": ""},
    ]
    assert checker.owner_codes(workspace, datasets, jobs_principal=JOBS) == [checker.UNRESOLVED, checker.UNRESOLVED]
    assert gets == ["c.s.listed_late", "c.s.unlinked", "c.s.gone"], "a listed pipeline id is never re-read"


def test_verify_prints_codes_never_names(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fake, _pairs = _handed_over(tmp_path)
    fake.set_run_as(PIPELINE, JOBS)
    fake.doc["tables"]["mip.silver.parcel_rollup"]["owner"] = OTHER
    capsys.readouterr()
    assert handover.main(["--verify"], install=_install, workspace_factory=lambda: fake) == 2
    out = capsys.readouterr()
    assert f"REFUSED {checker.NOT_RUN_AS} x1" in out.err and "verify: FAIL" in out.out
    for value in (JOBS, OTHER, LEGACY, PIPELINE, "parcel_rollup"):
        assert value not in out.out + out.err, value


def test_released_is_a_verify_mode_and_prints_codes_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        handover.main(["--plan", "--released"], install=_install, workspace_factory=FakeUc)
    assert "--released is a --verify mode" in capsys.readouterr().err
    fake, _pairs = _handed_over(tmp_path)
    assert handover.main(["--verify", "--released"], install=_install, workspace_factory=lambda: fake) == 2
    out = capsys.readouterr()
    assert f"REFUSED {checker.UNAPPROVED} x2" in out.err and "verify: FAIL" in out.out
    for value in (JOBS, LEGACY, PIPELINE, "parcel_rollup"):
        assert value not in out.out + out.err, value
    fake.set_run_as(PIPELINE, JOBS)
    assert handover.main(["--verify", "--released"], install=_install, workspace_factory=lambda: fake) == 0
