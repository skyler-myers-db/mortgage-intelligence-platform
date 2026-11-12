"""Adopt an existing install by exact bind, and unadopt it by the exact inverse (M2-PR6).

Driven against a synthetic 16-key legacy install and a fake bundle CLI
(tests/fixtures/adopt_estate.py): the CLI plans like the direct engine and
checks every state edit against the acknowledged plan, as bundle_env does.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures import adopt_estate as es
from tools.databricks import adopt_legacy_map as legacy_map
from tools.databricks import bundle_env_adopt
from tools.databricks import install_state_adopt as adopt

READS = frozenset(
    {
        ("apps", "get"),
        ("database", "get_database_instance"),
        ("database", "get_database_catalog"),
        ("warehouses", "get"),
        ("jobs", "get"),
        ("pipelines", "get"),
        ("lakeview", "get"),
        ("experiments", "get_experiment"),
        # the post-bind gate's name lookups
        ("current_user", "me"),
        ("catalogs", "get"),
        ("warehouses", "list"),
        ("jobs", "list"),
        ("pipelines", "list_pipelines"),
        ("experiments", "get_by_name"),
    }
)


class Lease:
    lease_id = "lease-1"
    source_git_sha = "0" * 40

    def __init__(self, *, held: bool = True) -> None:
        self.held = held
        self.checks = 0

    def __call__(self) -> None:
        self.checks += 1
        if not self.held:
            raise RuntimeError("the signed lease is not held")


def _plan(estate: es.Estate, cli: es.FakeBundleCli, phase: str = "adopt") -> legacy_map.AdoptPlan:
    return adopt.compute_plan(phase, es.inputs(), cli, estate)


def _apply(estate: es.Estate, cli: es.FakeBundleCli, tmp_path: Path, phase: str = "adopt", ack: str | None = None, lease: Lease | None = None) -> adopt.ApplyResult:
    ack = _plan(estate, es.FakeBundleCli(estate), phase).sha256 if ack is None else ack
    plan_dir = tmp_path / f"private-{len(list(tmp_path.iterdir()))}"
    plan_dir.mkdir()
    return adopt.apply(phase, ack, es.inputs(), cli, estate, lease=lease or Lease(), plan_dir=plan_dir)


# --- acceptance ---------------------------------------------------------------------


def test_a_synthetic_16_key_adopt_binds_every_key_and_plans_exactly_2_creates(tmp_path: Path) -> None:
    estate = es.Estate()
    cli = es.FakeBundleCli(estate)

    plan = _plan(estate, cli)
    assert not plan.refused, plan.refusals
    assert len(plan.edits) == 16 and plan.already == ()
    assert plan.expected_creates == es.NEW_KEYS

    result = _apply(estate, cli, tmp_path)

    assert sorted(call[1:] for call in cli.edits()) == sorted(
        (edit.key, edit.live_id) for edit in plan.edits
    ), "exactly the legacy map, each key once"
    assert estate.bound() == estate.legacy_ids
    gate = result.gate.public_summary()
    assert gate["verdict"] == "passed" and gate["position"] == "post-bind"
    assert gate["counts"]["create"] == 2 and gate["counts"]["delete"] == 0
    assert gate["counts"]["recreate"] == 0 and gate["counts"]["update_id"] == 0
    assert gate["adopt"] == {
        "bound": 16,
        "expected_creates": list(es.NEW_KEYS),
        "dashboard_parent_path": "pinned_permanent",
    }
    renames = {record.resource for record in result.gate.lookups if record.resource not in es.NEW_KEYS}
    assert renames, "every development-mode name is looked up before it is renamed"
    assert {service_method for service_method in estate.reads()} <= READS, "read-only but for the CLI"


def test_re_adopt_is_idempotent(tmp_path: Path) -> None:
    estate = es.Estate()
    _apply(estate, es.FakeBundleCli(estate), tmp_path)
    before = estate.state()
    cli = es.FakeBundleCli(estate)

    again = _plan(estate, cli)
    result = _apply(estate, cli, tmp_path)

    assert not again.refused and again.edits == () and len(again.already) == 16
    assert again.expected_creates == es.NEW_KEYS
    assert cli.edits() == [], "a second adopt edits nothing"
    assert estate.state() == before
    assert result.gate.public_summary()["verdict"] == "passed"


def test_unadopt_is_the_exact_inverse_of_adopt(tmp_path: Path) -> None:
    estate = es.Estate()
    # A key this deployer created itself stays bound: unadopt only undoes adopt.
    own = es.FakeBundleCli(estate)
    own._edit(es.NEW_KEYS[0], "777")
    before = estate.bound()
    adopter = es.FakeBundleCli(estate)
    _apply(estate, adopter, tmp_path)
    unadopter = es.FakeBundleCli(estate)

    plan = _plan(estate, unadopter, "unadopt")
    _apply(estate, unadopter, tmp_path, "unadopt")

    assert not plan.refused and len(plan.edits) == 16
    assert estate.bound() == before == {es.NEW_KEYS[0]: "777"}
    bound = [call[1] for call in adopter.edits() if call[0] == "bind"]
    unbound = [call[1] for call in unadopter.edits() if call[0] == "unbind"]
    assert unbound == bound[::-1] and len(set(bound)) == 16, "adopt's binds, in reverse"
    assert bound[0] == unbound[-1] == "mip_app", "the App's binding is the last one removed"
    assert [call[0] for call in unadopter.edits()] == ["unbind"] * 16
    assert _plan(estate, es.FakeBundleCli(estate), "unadopt").edits == (), "a second unadopt edits nothing"


# --- refusals before any edit -------------------------------------------------------


def _missing(estate: es.Estate) -> None:
    estate.object_of("jobs.mip_ref_seed").gone = True


def _renamed(estate: es.Estate) -> None:
    estate.object_of("jobs.mip_ref_seed").names["name"] = "someone-elses-job"


def _renamed_app(estate: es.Estate) -> None:
    estate.object_of("apps.mip_app").names["name"] = "another-app"


def _foreign_bound(estate: es.Estate) -> None:
    estate.object_of("pipelines.mip_feature_pipeline").deployment = f"{es.OTHER_ROOT}/state/metadata.json"


def _bound_elsewhere(estate: es.Estate) -> None:
    es.FakeBundleCli(estate)._edit("jobs.mip_ref_seed", "424242")


def _same_id_other_key(estate: es.Estate) -> None:
    es.FakeBundleCli(estate)._edit("jobs.mip_kpi_snapshot", estate.legacy_ids["jobs.mip_ref_seed"])


def _parent_moved(estate: es.Estate) -> None:
    estate.object_of("dashboards.mip_segment_dashboard").names["parent_path"] = "/Shared/elsewhere"


@pytest.mark.parametrize(
    ("damage", "code", "resource"),
    (
        (_missing, "missing", "jobs.mip_ref_seed"),
        (_renamed, "renamed", "jobs.mip_ref_seed"),
        (_renamed_app, "renamed", "apps.mip_app"),
        (_foreign_bound, "foreign_bound", "pipelines.mip_feature_pipeline"),
        (_bound_elsewhere, "bound_elsewhere", "jobs.mip_ref_seed"),
        (_same_id_other_key, "bound_elsewhere", "jobs.mip_ref_seed"),
        (_parent_moved, "dashboard_parent_moved", "dashboards.mip_segment_dashboard"),
    ),
    ids=("missing", "renamed", "renamed-app", "foreign-bound", "bound-elsewhere", "same-id-other-key", "dashboard-moved"),
)
def test_a_missing_renamed_or_foreign_bound_object_refuses_before_any_edit(
    tmp_path: Path, damage: object, code: str, resource: str
) -> None:
    estate = es.Estate()
    damage(estate)  # type: ignore[operator]
    cli = es.FakeBundleCli(estate)

    plan = _plan(estate, cli)
    assert (code, resource) in {(r.code, r.resource) for r in plan.refusals}, plan.refusals
    with pytest.raises(adopt.AdoptRefused) as refused:
        adopt.apply("adopt", plan.sha256, es.inputs(), cli, estate, lease=Lease(), plan_dir=tmp_path)
    assert refused.value.code == "plan_refused" and refused.value.inverse_proven
    assert cli.edits() == [], "nothing was bound"


def test_a_development_mode_name_is_the_bundle_name_and_nothing_else() -> None:
    assert legacy_map.same_name("jobs", "[dev someone] synthetic-x", "synthetic-x")
    assert legacy_map.same_name("experiments", "/Shared/[dev someone] x", "/Shared/x")
    assert not legacy_map.same_name("experiments", "[dev someone] /Shared/x", "/Shared/x")
    assert not legacy_map.same_name("jobs", "[dev someone] synthetic-x-old", "synthetic-x")
    assert not legacy_map.same_name("jobs", "synthetic-x [dev someone] ", "synthetic-x")
    assert not legacy_map.same_name("apps", "[dev someone] mip-app", "mip-app"), "named by id"


@pytest.mark.parametrize(
    ("legacy_keys", "code"),
    ((("jobs.mip_ref_seed", "jobs.retired_job"), "legacy_key_unknown"),),
)
def test_a_legacy_key_the_bundle_does_not_declare_refuses(legacy_keys: tuple[str, ...], code: str) -> None:
    estate = es.Estate(legacy_keys=legacy_keys)

    plan = _plan(estate, es.FakeBundleCli(estate))

    assert [(r.code, r.resource) for r in plan.refusals] == [(code, "jobs.retired_job")]


@pytest.mark.parametrize("phase", ("adopt", "unadopt"))
def test_a_deployer_state_key_the_bundle_dropped_refuses_before_the_lease(tmp_path: Path, phase: str) -> None:
    estate = es.Estate()
    if phase == "unadopt":
        _apply(estate, es.FakeBundleCli(estate), tmp_path)
    # This deployer's state binds a key the bundle no longer declares: the
    # pre-bind plan carries it as a delete, which names no bundle key.
    es.FakeBundleCli(estate)._edit("jobs.retired_job", "5555")
    cli = es.FakeBundleCli(estate)
    assert [entry.symbolic for entry in cli.plan().entries if entry.action == "delete"] == ["jobs.retired_job"]

    plan = _plan(estate, cli, phase)

    assert [(r.code, r.resource) for r in plan.refusals] == [("deployer_state_orphan", "jobs.retired_job")]
    with pytest.raises(adopt.AdoptRefused) as refused:
        adopt.apply(phase, plan.sha256, es.inputs(), cli, estate, lease=Lease(), plan_dir=tmp_path)
    assert refused.value.code == "plan_refused" and refused.value.inverse_proven
    assert cli.edits() == [], "refused by the read-only plan, before any edit"


def test_no_legacy_root_or_no_legacy_state_refuses() -> None:
    estate = es.Estate()
    cli = es.FakeBundleCli(estate)

    unset = adopt.compute_plan("adopt", es.inputs(legacy_root=None), cli, estate)
    missing = adopt.compute_plan("adopt", es.inputs(legacy_root=es.OTHER_ROOT), cli, estate)

    assert "not_an_adopted_install" in {r.code for r in unset.refusals}
    assert "legacy_state_missing" in {r.code for r in missing.refusals}


# --- the digest acknowledgement -------------------------------------------------------


def test_apply_refuses_an_ack_that_is_not_the_recomputed_plan(tmp_path: Path) -> None:
    estate = es.Estate()
    cli = es.FakeBundleCli(estate)
    acknowledged = _plan(estate, es.FakeBundleCli(estate)).sha256
    # The legacy install was deployed again between the review and the apply.
    legacy = estate.state(es.LEGACY_ROOT)
    assert legacy is not None
    estate.write_state(es.LEGACY_ROOT, {**legacy, "serial": legacy["serial"] + 1})

    with pytest.raises(adopt.AdoptRefused) as refused:
        _apply(estate, cli, tmp_path, ack=acknowledged)

    assert refused.value.code == "ack_mismatch" and cli.edits() == []
    assert acknowledged != "0" * 64
    with pytest.raises(adopt.AdoptRefused):
        _apply(estate, cli, tmp_path, ack="0" * 64)
    assert cli.edits() == [] and estate.state() is None


def test_the_lease_is_asserted_before_every_state_edit(tmp_path: Path) -> None:
    estate = es.Estate()
    lease = Lease()
    cli = es.FakeBundleCli(estate)
    _apply(estate, cli, tmp_path, lease=lease)
    assert lease.checks == 1 + 16
    assert {(a.lease_id, a.source_git_sha) for a in cli.authorities} == {("lease-1", "0" * 40)}, (
        "bundle_env is handed the held lease's evidence with every edit, to assert it again"
    )

    cli = es.FakeBundleCli(es.Estate())
    with pytest.raises(RuntimeError, match="lease"):
        _apply(cli.estate, cli, tmp_path, lease=Lease(held=False))
    assert cli.edits() == []


# --- the inverse on any failure after the first bind ----------------------------------


def test_a_dashboard_recreate_after_the_binds_refuses_and_undoes_them(tmp_path: Path) -> None:
    estate = es.Estate()
    cli = es.FakeBundleCli(estate, recreate_dashboards=True)

    with pytest.raises(adopt.AdoptRefused) as refused:
        _apply(estate, cli, tmp_path)

    assert refused.value.code == "gate_refused" and refused.value.inverse_proven
    gate = refused.value.result.gate  # type: ignore[union-attr]
    assert set(gate.codes) == {"dashboard_recreate"} and len(gate.codes) == 2
    assert estate.bound() == {}, "every bind this run made was undone"
    assert [call[0] for call in cli.edits()] == ["bind"] * 16 + ["unbind"] * 16


def test_an_instance_change_after_the_binds_refuses_whatever_the_ack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIP_ACK_DATABASE_INSTANCE_UPDATE", "f" * 64)
    estate = es.Estate()
    cli = es.FakeBundleCli(estate, instance_drift=True)

    with pytest.raises(adopt.AdoptRefused) as refused:
        _apply(estate, cli, tmp_path)

    assert refused.value.result.gate.codes == ("instance_change",)  # type: ignore[union-attr]
    assert estate.bound() == {}


@pytest.mark.parametrize("after", (False, True), ids=("bind-fails", "bind-commits-then-fails"))
def test_a_failed_bind_undoes_every_earlier_bind(tmp_path: Path, after: bool) -> None:
    estate = es.Estate()
    plan = _plan(estate, es.FakeBundleCli(estate))
    fifth = plan.edits[4].key
    failure = {fifth: RuntimeError("bind failed")}
    cli = es.FakeBundleCli(estate, **({"fail_after_bind": failure} if after else {"fail_bind": failure}))

    with pytest.raises(adopt.AdoptRefused) as refused:
        _apply(estate, cli, tmp_path, ack=plan.sha256)

    assert refused.value.code == "bind_failed" and refused.value.inverse_proven
    assert estate.bound() == {}
    unbound = [call[1] for call in cli.edits() if call[0] == "unbind"]
    assert len(unbound) == (5 if after else 4), "only what the state shows this run bound"


def test_an_unprovable_undo_is_reported_as_such(tmp_path: Path) -> None:
    estate = es.Estate()
    plan = _plan(estate, es.FakeBundleCli(estate))
    cli = es.FakeBundleCli(
        estate,
        recreate_dashboards=True,
        fail_unbind={plan.edits[0].key: RuntimeError("unbind failed")},
    )

    with pytest.raises(adopt.AdoptRefused) as refused:
        _apply(estate, cli, tmp_path, ack=plan.sha256)

    assert not refused.value.inverse_proven
    assert estate.bound() == {plan.edits[0].resource: plan.edits[0].live_id}


# --- the authorization bundle_env enforces ---------------------------------------------


def test_only_an_acknowledged_plan_authorizes_a_state_edit(tmp_path: Path) -> None:
    estate = es.Estate()
    plan = _plan(estate, es.FakeBundleCli(estate))
    text = __import__("json").dumps(plan.document)
    edit = plan.edits[0]

    assert bundle_env_adopt.authorize_state_edit(text, plan.sha256, "bind", edit.key, edit.live_id) is None
    assert bundle_env_adopt.authorize_state_edit(text, plan.sha256, "unbind", edit.key, None) is None, "its inverse"
    assert bundle_env_adopt.authorize_state_edit(text, plan.sha256, "bind", edit.key, "999") is not None
    assert bundle_env_adopt.authorize_state_edit(text, plan.sha256, "bind", "not_planned", "1") is not None
    assert bundle_env_adopt.authorize_state_edit(text, "0" * 64, "bind", edit.key, edit.live_id) is not None
    tampered = text.replace(edit.live_id, edit.live_id + "0", 1)
    assert bundle_env_adopt.authorize_state_edit(tampered, plan.sha256, "bind", edit.key, edit.live_id + "0") is not None
    unadopt_plan = _plan(estate, es.FakeBundleCli(estate), "unadopt")
    unadopt_text = __import__("json").dumps(unadopt_plan.document)
    assert bundle_env_adopt.authorize_state_edit(unadopt_text, unadopt_plan.sha256, "bind", edit.key, edit.live_id) is not None


def test_the_bundle_cli_never_inherits_the_signing_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def run(argv: list[str], **kwargs: object) -> object:
        seen.update(argv=argv, env=kwargs["env"])
        return __import__("types").SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", "PLACEHOLDER-signing-key")
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY", "PLACEHOLDER-attestation-key")
    monkeypatch.setenv(bundle_env_adopt.ACK_ENV, "a" * 64)
    monkeypatch.setattr(adopt.subprocess, "run", run)
    cli = adopt.BundleEnvCli(python="python")
    cli.bind("mip_ref_seed", "1001", adopt.Authority(tmp_path / "plan.json", "a" * 64, "lease-1", "0" * 40))

    env = seen["env"]
    assert isinstance(env, dict) and "MIP_AI_GATEWAY_PROOF_SIGNING_KEY" not in env
    assert "MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY" not in env
    assert env[bundle_env_adopt.ACK_ENV] == "a" * 64, "bundle_env checks the operator's own ack"
    assert seen["argv"] == [
        "python", "-m", "tools.databricks.bundle_env", "deployment", "bind", "mip_ref_seed", "1001",
        "-t", "client", "--auto-approve",
        "--adopt-plan", str(tmp_path / "plan.json"), "--adopt-plan-sha256", "a" * 64,
        "--adopt-lease-id", "lease-1", "--adopt-source-git-sha", "0" * 40,
        # B4(c): with nothing pinned yet, bundle_env binds the exactly acked Genie title match.
        bundle_env_adopt.ADOPT_GENIE_FLAG,
    ]


# --- the CLI: public lines carry codes, counts and digests only --------------------------


def _identifiers(estate: es.Estate) -> set[str]:
    values = {"legacy-owner@example.invalid", es.LEGACY_ROOT, es.DEPLOYER_ROOT, es.DEV.strip()}
    values |= {value for value in estate.legacy_ids.values() if not value.startswith("synthetic-")}
    return values


def _main(estate: es.Estate, cli: es.FakeBundleCli, argv: list[str], lease: Lease | None = None) -> int:
    return adopt.main(
        argv,
        inputs=es.inputs,
        workspace_factory=lambda: estate,
        cli_factory=lambda: cli,
        lease_factory=lambda _workspace, _args: lease or Lease(),
    )


def test_the_cli_plans_then_applies_and_prints_no_identifier(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    estate = es.Estate()
    report = tmp_path / "report.json"

    assert _main(estate, es.FakeBundleCli(estate), ["plan", "--phase", "adopt", "--report-out", str(report)]) == 0
    planned = capsys.readouterr()
    sha = __import__("json").loads(report.read_text())["plan_sha256"]
    assert f"MIP_ADOPT_PLAN_ACK={sha}" in planned.out
    assert _main(estate, es.FakeBundleCli(estate), ["plan", "--phase", "adopt", "--ack-plan", "0" * 64]) == 2
    apply_args = ["apply", "--phase", "adopt", "--ack-plan", sha, "--app-name", "a", "--lease-id", "l", "--source-git-sha", "0" * 40]
    assert _main(estate, es.FakeBundleCli(estate), [*apply_args, "--report-out", str(report)]) == 0
    applied = capsys.readouterr()

    text = planned.out + planned.err + applied.out + applied.err
    assert not [value for value in _identifiers(estate) if value in text]
    assert "adopt applied: 16 keys bound" in applied.out
    assert oct(report.stat().st_mode & 0o777) == "0o600"
    assert __import__("json").loads(report.read_text())["post_bind_gate"]["verdict"] == "passed"


def test_the_cli_masks_every_state_id_inside_github_actions(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    estate = es.Estate()

    assert _main(estate, es.FakeBundleCli(estate), ["plan", "--phase", "adopt"]) == 0

    masks = {line.removeprefix("::add-mask::") for line in capsys.readouterr().err.splitlines() if line.startswith("::add-mask::")}
    assert set(estate.legacy_ids.values()) <= masks


def test_a_refused_apply_exits_2_and_an_unproven_undo_exits_3(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    estate = es.Estate()
    sha = _plan(estate, es.FakeBundleCli(estate)).sha256
    args = ["apply", "--phase", "adopt", "--ack-plan", sha, "--app-name", "a", "--lease-id", "l", "--source-git-sha", "0" * 40]

    assert _main(estate, es.FakeBundleCli(estate, recreate_dashboards=True), args) == 2
    assert "nothing is left changed" in capsys.readouterr().err
    # The undone binds moved the deployer's state serial: the old ack is stale.
    assert _main(estate, es.FakeBundleCli(estate), args) == 2
    assert "REFUSED ack_mismatch" in capsys.readouterr().err
    replanned = _plan(estate, es.FakeBundleCli(estate))
    args[4] = replanned.sha256
    stuck = es.FakeBundleCli(estate, recreate_dashboards=True, fail_unbind={replanned.edits[0].key: RuntimeError("x")})
    assert _main(estate, stuck, args) == 3
    assert "the inverse is unproven" in capsys.readouterr().err
