"""The Unity Catalog ownership handover: plan, digest gate, journal, exact revert (M2-PR8).

Run against ``tests/fixtures/uc_handover_fake.FakeUc``, which has no delete,
create or grant call at all. The plan's statements are pinned by a golden
file; every emitted statement is an owner change and nothing else; a wrong or
stale digest changes nothing; the journal is private; revert restores every
prior owner exactly, also from a partial journal; and the metastore, a share,
a foreign or system catalog and System-user objects are refused or left alone.
"""

from __future__ import annotations

import json
import re
import stat
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from tests.fixtures.uc_handover_fake import LEGACY, OTHER, TARGET, FakeUc, estate
from tools.clients import handover_journal as journals
from tools.clients import handover_uc_ownership as handover
from tools.clients.handover_uc_plan import SYSTEM_USER, HandoverRefused, change, compute
from tools.databricks.uc_interim_owner_pairs import InterimOwnerPairs, parse_pairs

REPO = Path(__file__).resolve().parents[2]
GOLDEN = REPO / "tests" / "fixtures" / "handover_uc_plan_golden.json"
EXPIRES = date(2026, 11, 18)
DESTRUCTIVE = re.compile(r"\b(DROP|DELETE|TRUNCATE|REVOKE|GRANT|CREATE|REPLACE)\b", re.IGNORECASE)


def _install(pairs: InterimOwnerPairs | None = None, *, catalog: str = "mip", sync: str | None = "mip_app_state") -> handover.Install:
    return handover.Install(catalog=catalog, sync_catalog=sync, target=TARGET, pairs=pairs or InterimOwnerPairs())


def _plan(fake: FakeUc, **kwargs: object) -> object:
    return handover.plan(fake, _install(**kwargs), interim_expires=EXPIRES)  # type: ignore[arg-type]


def test_the_plan_matches_its_golden_statements() -> None:
    report = _plan(FakeUc()).report()  # type: ignore[attr-defined]
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert report == golden


def test_every_emitted_statement_is_an_owner_change_and_nothing_destructive() -> None:
    current = _plan(FakeUc())
    changes = current.changes  # type: ignore[attr-defined]
    assert changes, "positive control: the fixture plans changes"
    for item in changes:
        for statement in (item["sql"], item["api"]):
            assert not DESTRUCTIVE.search(statement.replace(item["full_name"], "")), statement
        assert item["api"].startswith("PATCH /api/2.1/unity-catalog/")
        assert item["api"].endswith(f'{{"owner":"{TARGET}"}}')
        assert not item["sql"] or (item["sql"].startswith("ALTER ") and item["sql"].endswith(f"OWNER TO `{TARGET}`"))
    assert DESTRUCTIVE.search("DROP TABLE x"), "positive control: the scan sees a DROP"


def test_the_plan_moves_leaves_first_and_leaves_every_other_owner_alone() -> None:
    current = _plan(FakeUc())
    kinds = [item["kind"] for item in current.changes]  # type: ignore[attr-defined]
    assert kinds == sorted(kinds, key=["FUNCTION", "REGISTERED_MODEL", "VOLUME", "VIEW", "TABLE", "SCHEMA", "CATALOG"].index)
    assert kinds[-2:] == ["CATALOG", "CATALOG"] or kinds[-1] == "CATALOG"
    names = {item["full_name"] for item in current.changes}  # type: ignore[attr-defined]
    assert "mip.gold.owned_elsewhere" not in names and "mip.audit" not in names
    assert not any(name.startswith("mip_app_state.") for name in names), "the sync catalog at catalog level only"
    assert current.document["left_alone"] == {  # type: ignore[attr-defined]
        "other_owner": 3,
        "system_owned": 1,
        "follows_catalog": 0,
        "sync_catalog_child": 2,
    }


def test_pipeline_datasets_are_excluded_with_suggested_interim_pairs() -> None:
    current = _plan(FakeUc())
    pipeline = [row["full_name"] for row in current.document["pipeline_managed"]]  # type: ignore[attr-defined]
    assert pipeline == ["mip.silver.parcel_rollup", "mip.silver.parcel_stream"]
    assert not {item["full_name"] for item in current.changes} & set(pipeline)  # type: ignore[attr-defined]
    suggested = current.suggested_pairs  # type: ignore[attr-defined]
    assert suggested == [{"full_name": name, "owner": LEGACY, "expires_at": EXPIRES.isoformat()} for name in pipeline]
    pairs = parse_pairs(json.dumps(suggested), now=datetime(2026, 11, 4, tzinfo=UTC))
    assert all(pairs.verdict(full_name=name, owner=LEGACY) == "match" for name in pipeline)


def test_an_expired_configured_pair_refuses_the_plan() -> None:
    expired = parse_pairs(
        json.dumps([{"full_name": "mip.silver.parcel_stream", "owner": LEGACY, "expires_at": "2026-11-01"}]),
        now=datetime(2026, 11, 4, tzinfo=UTC),
    )
    current = _plan(FakeUc(), pairs=expired)
    assert [refusal.code for refusal in current.refusals] == ["interim_pair_expired"]  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("catalog", "code"),
    [
        ("cotality_share", "share_catalog"),
        ("lakehouse_federation", "foreign_catalog"),
        ("system", "system_catalog"),
        ("mip_app_state", "catalog_role"),
    ],
)
def test_a_share_foreign_or_system_catalog_is_refused(catalog: str, code: str) -> None:
    fake = FakeUc()
    current = handover.plan(fake, _install(catalog=catalog, sync=None))
    assert [refusal.code for refusal in current.refusals] == [code]
    assert current.changes == [] and fake.updates == []


def test_the_metastore_and_the_system_user_are_never_a_change() -> None:
    with pytest.raises(HandoverRefused, match="never changes the metastore"):
        change("METASTORE", "metastore", prior=LEGACY, owner=TARGET)
    with pytest.raises(HandoverRefused, match="System user"):
        change("TABLE", "mip.gold.x", prior=SYSTEM_USER, owner=TARGET)
    with pytest.raises(HandoverRefused, match="System user"):
        change("TABLE", "mip.gold.x", prior=LEGACY, owner="system USER")
    with pytest.raises(HandoverRefused, match="one exact principal"):
        change("TABLE", "mip.gold.x", prior=LEGACY, owner="account users")
    assert "METASTORE" not in handover.UPDATERS


def test_a_foreign_or_unreviewed_table_refuses_the_plan() -> None:
    doc = estate()
    doc["tables"]["mip.gold.mirror"] = {"owner": LEGACY, "type": "FOREIGN"}
    doc["tables"]["mip.gold.clone"] = {"owner": LEGACY, "type": "MANAGED_SHALLOW_CLONE"}
    current = _plan(FakeUc(doc))
    assert sorted(refusal.code for refusal in current.refusals) == ["foreign_securable", "unreviewed_type"]  # type: ignore[attr-defined]


def test_the_target_must_be_the_group_not_vocabulary_or_the_legacy_owner() -> None:
    for target, code in (("account users", "target_vocabulary"), (LEGACY.upper(), "target_is_legacy")):
        current = compute(FakeUc(), catalog="mip", sync_catalog=None, target=target, legacy_owner=LEGACY)
        assert [refusal.code for refusal in current.refusals] == [code]


def _digest(fake: FakeUc) -> str:
    return handover.plan(fake, _install()).sha256


@pytest.mark.parametrize("ack", ["", "0" * 64, "stale"])
def test_a_wrong_or_stale_digest_changes_nothing(tmp_path: Path, ack: str) -> None:
    fake = FakeUc()
    if ack == "stale":
        ack = _digest(fake)
        fake.doc["tables"]["mip.gold.created_since"] = {"owner": LEGACY, "type": "MANAGED"}
    before = fake.owners()
    with pytest.raises(HandoverRefused, match="not the acknowledged digest"):
        handover.apply(fake, _install(), ack=ack, journal_path=tmp_path / "journal.json")
    assert fake.updates == [] and fake.owners() == before
    assert not (tmp_path / "journal.json").exists()


def test_apply_journals_privately_and_moves_exactly_the_plan(tmp_path: Path) -> None:
    fake = FakeUc()
    planned = {item["full_name"] for item in handover.plan(fake, _install()).changes}
    journal = handover.apply(fake, _install(), ack=_digest(fake), journal_path=tmp_path / "j.json")
    assert stat.S_IMODE((tmp_path / "j.json").stat().st_mode) == 0o600
    assert journal.status == "applied" and journal.counts()["applied"] == len(planned)
    assert {name for _section, name, _owner in fake.updates} == planned
    assert all(owner == TARGET for _section, _name, owner in fake.updates)
    with pytest.raises(journals.JournalRefused, match="already exists"):
        handover.apply(fake, _install(), ack=_digest(fake), journal_path=tmp_path / "j.json")


def _revert_all(fake: FakeUc, path: Path) -> dict[str, int]:
    journal = journals.load(path, kind=handover.KIND)
    document, refusals = handover.revert_plan(fake, journal)
    assert refusals == []
    return handover.revert(fake, journal, ack=journals.plan_sha256(document, domain=handover.REVERT_DOMAIN))


def test_revert_is_the_exact_inverse(tmp_path: Path) -> None:
    fake = FakeUc()
    before = fake.owners()
    handover.apply(fake, _install(), ack=_digest(fake), journal_path=tmp_path / "j.json")
    assert fake.owners() != before
    counts = _revert_all(fake, tmp_path / "j.json")
    assert fake.owners() == before
    assert counts["noop"] == 0 and counts["moved"] == len(fake.updates) // 2
    reverted = [(name, owner) for _section, name, owner in fake.updates[counts["moved"] :]]
    assert reverted[0][0] == "mip_app_state" or reverted[0][0] == "mip", "catalogs first"
    assert all(owner == LEGACY for _name, owner in reverted)


@pytest.mark.parametrize("failure", ["fail_on", "land_then_fail"])
def test_revert_from_a_partial_journal_restores_every_prior_owner(tmp_path: Path, failure: str) -> None:
    fake = FakeUc()
    before = fake.owners()
    getattr(fake, failure).add("mip.gold.borrower_360")
    with pytest.raises(RuntimeError, match="injected"):
        handover.apply(fake, _install(), ack=_digest(fake), journal_path=tmp_path / "j.json")
    journal = journals.load(tmp_path / "j.json", kind=handover.KIND)
    assert journal.status == "refused"
    assert journal.entry("TABLE:mip.gold.borrower_360")["state"] == "applying"
    assert journal.counts()["pending"] > 0, "the apply stopped at the first failure"
    getattr(fake, failure).clear()
    counts = _revert_all(fake, tmp_path / "j.json")
    assert fake.owners() == before
    assert counts["noop"] == (1 if failure == "fail_on" else 0)


def test_revert_refuses_an_object_re_owned_outside_the_handover(tmp_path: Path) -> None:
    fake = FakeUc()
    handover.apply(fake, _install(), ack=_digest(fake), journal_path=tmp_path / "j.json")
    fake.doc["functions"]["mip.gold.fn_lead_score"] = OTHER
    after = fake.owners()
    journal = journals.load(tmp_path / "j.json", kind=handover.KIND)
    _document, refusals = handover.revert_plan(fake, journal)
    assert refusals == ["owner_changed"]
    with pytest.raises(HandoverRefused, match="nothing was reverted"):
        handover.revert(fake, journal, ack="0" * 64)
    assert fake.owners() == after


def test_verify_passes_only_with_an_exact_pair_for_every_pipeline_dataset(tmp_path: Path) -> None:
    fake = FakeUc()
    handover.apply(fake, _install(), ack=_digest(fake), journal_path=tmp_path / "j.json")
    assert handover.verify(fake, _install()) == ["interim_pair_missing", "interim_pair_missing"]
    rows = handover.plan(fake, _install(), interim_expires=date.today() + timedelta(days=7)).suggested_pairs
    pairs = parse_pairs(json.dumps(rows))
    assert handover.verify(fake, _install(pairs)) == []
    fake.doc["tables"]["mip.gold.new_since"] = {"owner": LEGACY, "type": "MANAGED"}
    assert handover.verify(fake, _install(pairs)) == ["legacy_owned"]


def test_the_cli_prints_codes_counts_and_digests_never_names(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    fake = FakeUc()
    run = lambda *argv: handover.main(list(argv), install=_install, workspace_factory=lambda: fake)  # noqa: E731
    assert run("--plan", "--report-out", str(tmp_path / "report.json")) == 0
    out = capsys.readouterr()
    digest = _digest(fake)
    assert digest in out.out
    for name in (LEGACY, TARGET, "borrower_360", "parcel_stream", "mip_app_state"):
        assert name not in out.out + out.err
    assert stat.S_IMODE((tmp_path / "report.json").stat().st_mode) == 0o600
    with pytest.raises(SystemExit):
        run("--apply", "--ack-plan", digest)
    assert run("--apply", "--ack-plan", "0" * 64, "--journal", str(tmp_path / "j.json")) == 2
    assert fake.updates == []
    assert run("--apply", "--ack-plan", digest, "--journal", str(tmp_path / "j.json")) == 0
    assert run("--revert", "--journal", str(tmp_path / "j.json")) == 0
    revert_digest = re.search(r"sha256 ([0-9a-f]{64})", capsys.readouterr().out)
    assert revert_digest is not None
    assert run("--revert", "--journal", str(tmp_path / "j.json"), "--ack-plan", revert_digest.group(1)) == 0
    assert fake.owners() == FakeUc().owners()


def test_a_partial_apply_exits_three_and_names_the_revert(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeUc()
    digest = _digest(fake)
    fake.fail_on.add("mip.gold.borrower_360")
    rc = handover.main(
        ["--apply", "--ack-plan", digest, "--journal", str(tmp_path / "j.json")],
        install=_install,
        workspace_factory=lambda: fake,
    )
    assert rc == 3
    err = capsys.readouterr().err
    assert "Revert with --revert --journal" in err and "injected" not in err


def test_a_shared_or_foreign_journal_refuses(tmp_path: Path) -> None:
    fake = FakeUc()
    handover.apply(fake, _install(), ack=_digest(fake), journal_path=tmp_path / "j.json")
    (tmp_path / "j.json").chmod(0o644)
    with pytest.raises(journals.JournalRefused, match="mode 0600"):
        journals.load(tmp_path / "j.json", kind=handover.KIND)
    (tmp_path / "j.json").chmod(0o600)
    with pytest.raises(journals.JournalRefused, match="other handover"):
        journals.load(tmp_path / "j.json", kind="lakebase")
    (tmp_path / "link.json").symlink_to(tmp_path / "j.json")
    with pytest.raises(journals.JournalRefused, match="not a regular file"):
        journals.load(tmp_path / "link.json", kind=handover.KIND)
