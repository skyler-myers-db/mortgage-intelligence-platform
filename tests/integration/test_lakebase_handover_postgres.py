"""Real-PostgreSQL contract for the Lakebase ownership handover (PR #336 review).

The unit suites drive the handover against tests/fixtures/fake_lakebase.py,
which matches each catalog read by its text and re-implements its meaning,
so a regression in the shipped SQL stays green there. Here the real SQL runs
as the real roles on a disposable cluster (``MIP_TEST_POSTGRES_DSN``, a
superuser; CI's serial step runs every ``test_*_postgres.py``): an App
database migrated by lakebase/schema.sql and granted by the real grants job
as the legacy owner, then the plan, ``--preconditions``, the apply, the
grants job run as the target, the revert and a re-plan, and the residue read
across two databases.

Lakebase stand-ins on vanilla PostgreSQL: the legacy owner owns both
databases (``CREATEDB``), L-A's membership is granted by the superuser, the
provider's own routines in ``public`` belong to ``cloud_admin`` (pgcrypto's
here, which the grants job skips as it does on Lakebase), and the grants job
runs with ``allow_absent_*``, which skips its public-schema gate and closure;
``Install.close_public_schema`` runs that closure for real, against the
provider's writer-less ``public`` ACL (spike S2b) with a
``databricks_superuser`` stand-in. Each test builds and drops its own
databases and roles; ``cloud_admin`` and ``databricks_superuser`` are dropped
only when the test created them.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql as psql
from psycopg.conninfo import make_conninfo

from jobs import lakebase_migrate
from jobs import lakebase_migration_provider_plane as provider_plane
from jobs.lakebase_migration_read_only import read_only_session
from jobs.lakebase_migration_seed import render_seed_sql
from tools.clients import handover_journal as journals
from tools.clients import lakebase_handover as handover
from tools.clients import lakebase_handover_roles as roles
from tools.clients import lakebase_handover_sql as sql

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
_PGCRYPTO_TO_CLOUD_ADMIN = """
DO $$
DECLARE routine regprocedure;
BEGIN
    FOR routine IN
        SELECT member.objid::regprocedure FROM pg_depend member
        WHERE member.refclassid = 'pg_extension'::regclass AND member.classid = 'pg_proc'::regclass
          AND member.refobjid = (SELECT oid FROM pg_extension WHERE extname = 'pgcrypto')
    LOOP
        EXECUTE format('ALTER FUNCTION %s OWNER TO cloud_admin', routine);
    END LOOP;
END$$
"""
# Owners and ACL entries of the database, mip_app and everything in it, as sets.
_SNAPSHOT_SQL = """
SELECT kind, name, owner, acl IS NULL,
       ARRAY(SELECT format('%s=%s/%s%s', pg_get_userbyid(entry.grantee), entry.privilege_type,
                           pg_get_userbyid(entry.grantor), CASE WHEN entry.is_grantable THEN '*' ELSE '' END)
             FROM aclexplode(acl) entry ORDER BY 1)
FROM (
    SELECT 'database' AS kind, datname::text AS name, pg_get_userbyid(datdba)::text AS owner, datacl AS acl
    FROM pg_database WHERE datname = current_database()
    UNION ALL
    SELECT 'schema', nspname::text, pg_get_userbyid(nspowner)::text, nspacl FROM pg_namespace WHERE nspname = 'mip_app'
    UNION ALL
    SELECT 'relation', relation.oid::regclass::text, pg_get_userbyid(relation.relowner)::text, relation.relacl
    FROM pg_class relation JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = 'mip_app'
    UNION ALL
    SELECT 'routine', routine.oid::regprocedure::text, pg_get_userbyid(routine.proowner)::text, routine.proacl
    FROM pg_proc routine JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace
    WHERE namespace.nspname = 'mip_app'
    UNION ALL
    SELECT 'type', type_object.oid::regtype::text, pg_get_userbyid(type_object.typowner)::text, type_object.typacl
    FROM pg_type type_object JOIN pg_namespace namespace ON namespace.oid = type_object.typnamespace
    WHERE namespace.nspname = 'mip_app'
) AS objects
ORDER BY 1, 2
"""
VIEW = "mip_app.handover_probe_view"
DEFINER = "mip_app.handover_probe_definer"


@dataclass(frozen=True)
class Install:
    dsn: str
    database: str
    other: str
    legacy: str
    target: str
    app: str
    extra_roles: list[str] = field(default_factory=list)  # dropped after the databases

    def kwargs(self, role: str, database: str = "") -> dict[str, Any]:
        return {"conninfo": make_conninfo(self.dsn, dbname=database or self.database), "options": f"-c role={role}"}

    def run(self, role: str, *statements: str, database: str = "") -> None:
        with psycopg.connect(**self.kwargs(role, database), autocommit=True) as connection:
            for statement in statements:
                connection.execute(statement)

    def read_session(self) -> Any:
        return read_only_session(self.kwargs(self.legacy))

    def connect(self) -> psycopg.Connection[Any]:
        return psycopg.connect(**self.kwargs(self.legacy), autocommit=True)

    def plan(self) -> handover.LakebasePlan:
        return handover.plan(self.read_session, target=self.target)

    def preconditions(self) -> tuple[list[str], list[str]]:
        with self.read_session() as cursor:
            lines, refusals = roles.preconditions(cursor, target=self.target)
        return [line for line in lines if not line.startswith("--")], refusals

    def grants_job(self, role: str) -> None:
        """The real grants job (jobs/lakebase_migration_grants.py), run as ``role``."""

        lakebase_migrate._apply_app_role_grants(
            self.kwargs(role),
            resolved_roles=(self.app, None),
            role_wait_timeout_s=0,
            role_wait_interval_s=1,
            allow_absent_managed_event_triggers=True,
            allow_absent_provider_schema=True,
        )

    def apply(self, path: Path) -> journals.Journal:
        return handover.apply(self.read_session, self.connect, target=self.target, ack=self.plan().sha256, journal_path=path)

    def revert(self, path: Path) -> bool:
        journal = journals.load(path, kind=handover.KIND)
        document, refusals = handover.revert_plan(self.read_session, journal)
        assert refusals == []
        ack = journals.plan_sha256(document, domain=handover.REVERT_DOMAIN)
        return handover.revert(self.read_session, self.connect, journal, ack=ack)

    def role(self, stem: str) -> str:
        """A NOLOGIN role for one test, dropped with the install."""

        name = f"{self.legacy}_{stem}"
        with psycopg.connect(self.dsn, autocommit=True) as admin:
            admin.execute(f"CREATE ROLE {_identifier(name)} NOLOGIN")
        self.extra_roles.append(name)
        return name

    def provider_public_acl(self) -> None:
        """``public`` as a 2026-11 Lakebase database leaves it (spike S2b's writer-less revision), PUBLIC's USAGE closed."""

        with psycopg.connect(self.dsn, autocommit=True) as admin:
            if not admin.execute("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'databricks_superuser')").fetchone()[0]:
                admin.execute("CREATE ROLE databricks_superuser NOLOGIN")
                self.extra_roles.append("databricks_superuser")
        with psycopg.connect(make_conninfo(self.dsn, dbname=self.database), autocommit=True) as admin:
            admin.execute("GRANT USAGE, CREATE ON SCHEMA public TO databricks_superuser WITH GRANT OPTION")

    def close_public_schema(self, role: str) -> None:
        """The grants job's real public-schema closure (gate, authority, revoke, gate), run as ``role``."""

        with psycopg.connect(**self.kwargs(role)) as connection, connection.cursor() as cursor:
            provider_plane._close_public_schema_boundary(cursor, (self.app,), principal_label="handover test")

    def snapshot(self) -> list[tuple[Any, ...]]:
        with psycopg.connect(make_conninfo(self.dsn, dbname=self.database)) as connection:
            return list(connection.execute(_SNAPSHOT_SQL).fetchall())


def _identifier(name: str) -> str:
    return psql.Identifier(name).as_string()


@pytest.fixture
def install(monkeypatch: pytest.MonkeyPatch) -> Iterator[Install]:
    dsn = os.environ.get("MIP_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("MIP_TEST_POSTGRES_DSN is not configured")
    monkeypatch.setenv("APP_ENV", "test")  # the grants job then omits the AI Gateway verifier
    suffix = uuid4().hex[:10]
    current = Install(dsn, f"mip_ho_db_{suffix}", f"mip_ho_other_{suffix}", f"mip_ho_legacy_{suffix}", f"mip_ho_target_{suffix}", f"mip_ho_app_{suffix}")
    legacy, target = _identifier(current.legacy), _identifier(current.target)
    created_cloud_admin = False
    with psycopg.connect(dsn, autocommit=True) as admin:
        for role in (current.legacy, current.target, current.app):
            admin.execute(f"CREATE ROLE {_identifier(role)} LOGIN NOREPLICATION INHERIT")
        admin.execute(f"ALTER ROLE {legacy} CREATEDB")
        admin.execute(f"GRANT {target} TO {legacy} WITH INHERIT TRUE, SET TRUE")
        if not admin.execute("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cloud_admin')").fetchone()[0]:
            admin.execute("CREATE ROLE cloud_admin NOLOGIN")
            created_cloud_admin = True
        for database in (current.database, current.other):
            admin.execute(f"CREATE DATABASE {_identifier(database)} OWNER {legacy}")
    try:
        with psycopg.connect(make_conninfo(dsn, dbname=current.database), autocommit=True) as admin:
            admin.execute("CREATE EXTENSION pgcrypto")
            admin.execute(_PGCRYPTO_TO_CLOUD_ADMIN)
        # The dedicated database's posture (as CI's own database takes it), then an install as the legacy owner.
        current.run(current.legacy, "REVOKE ALL ON SCHEMA public FROM PUBLIC")
        schema = (REPO / "lakebase" / "schema.sql").read_text(encoding="utf-8")
        seed = render_seed_sql((REPO / "lakebase" / "seed_campaigns.sql").read_text(encoding="utf-8"))
        pre_seed, post_seed = lakebase_migrate._split_schema_sql(schema)
        lakebase_migrate._run_transaction(
            (pre_seed, seed, post_seed),
            current.kwargs(current.legacy),
            app_role=current.app,
            verify_outreach_integrity=True,
            allow_absent_managed_event_triggers=True,
            allow_absent_provider_schema=True,
        )
        current.grants_job(current.legacy)
        current.run(current.legacy, f"GRANT CREATE ON SCHEMA mip_app TO {target}")  # L-A step 3
        yield current
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            for database in (current.database, current.other):
                admin.execute(f"DROP DATABASE IF EXISTS {_identifier(database)} WITH (FORCE)")
            for role in (*reversed(current.extra_roles), current.app, current.target, current.legacy):
                admin.execute(f"DROP ROLE IF EXISTS {_identifier(role)}")
            if created_cloud_admin:
                admin.execute("DROP ROLE IF EXISTS cloud_admin")


def _extras(install: Install) -> None:
    """A legacy-owned schema outside mip_app (a leftover or synced-table one), then the grants job as before."""

    install.run(
        install.legacy,
        "CREATE SCHEMA extras",
        "CREATE TABLE extras.synced (id integer)",
        "CREATE SEQUENCE extras.standalone",
        "CREATE FUNCTION extras.refresh(since date) RETURNS integer LANGUAGE sql AS $$SELECT 1$$",
    )
    install.grants_job(install.legacy)  # revokes PUBLIC EXECUTE on extras.refresh, as on every install


def test_a_clean_plan_means_the_grants_job_run_as_the_target_passes(install: Install, tmp_path: Path) -> None:
    _extras(install)
    assert sorted(install.plan().refusals) == ["target_needs_object_privilege"] * 3 + ["target_needs_schema_usage"]
    statements, refusals = install.preconditions()
    target = _identifier(install.target)
    assert statements == [
        f'GRANT USAGE ON SCHEMA "extras" TO {target};',
        f'GRANT EXECUTE ON FUNCTION "extras"."refresh"(since date) TO {target};',
        f'GRANT SELECT ON SEQUENCE "extras"."standalone" TO {target};',
        f'GRANT SELECT ON TABLE "extras"."synced" TO {target};',
    ]
    assert refusals == []
    install.run(install.legacy, *statements)
    assert install.plan().refusals == [] and install.preconditions() == ([], [])
    assert install.apply(tmp_path / "j.json").status == "applied"
    install.grants_job(install.target)
    install.run(install.app, "SELECT count(*) FROM mip_app.campaigns")


def test_with_usage_alone_the_grants_job_run_as_the_target_fails(
    install: Install, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive control at the broken layer: the plan before the fix, which read schema usage only."""

    _extras(install)
    install.run(install.legacy, f'GRANT USAGE ON SCHEMA "extras" TO {_identifier(install.target)}')
    monkeypatch.setattr(handover, "unreachable_objects", lambda *_args, **_kwargs: [])
    assert install.plan().refusals == []
    assert install.apply(tmp_path / "j.json").status == "applied"
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="permission denied for"):
        install.grants_job(install.target)


def _probe_reads(install: Install, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """After every committed object change, an App read through a view and through a SECURITY DEFINER routine."""

    install.run(
        install.legacy,
        f"CREATE VIEW {VIEW} AS SELECT campaign_id FROM mip_app.campaigns",
        f"CREATE FUNCTION {DEFINER}() RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog "
        "AS $$BEGIN RETURN (SELECT count(*) FROM mip_app.campaigns); END$$",
        f"GRANT SELECT ON {VIEW} TO {_identifier(install.app)}",
        f"GRANT EXECUTE ON FUNCTION {DEFINER}() TO {_identifier(install.app)}",
    )
    failures: list[tuple[str, str]] = []
    original = handover.run_bounded

    def probed(connection: Any, statement: Any, **kwargs: Any) -> int:
        attempts = original(connection, statement, **kwargs)
        with psycopg.connect(**install.kwargs(install.app), autocommit=True) as app:
            for name, query in (("view", f"SELECT count(*) FROM {VIEW}"), ("definer", f"SELECT {DEFINER}()")):
                try:
                    app.execute(query)
                except psycopg.Error as exc:
                    failures.append((name, str(exc.sqlstate)))
        return attempts

    monkeypatch.setattr(handover, "run_bounded", probed)
    return failures


def test_apply_revert_and_re_plan_are_acl_exact_and_no_app_read_fails(
    install: Install, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failures = _probe_reads(install, monkeypatch)
    install.run(install.legacy, f"GRANT SELECT ON mip_app.campaigns TO {_identifier(install.target)}")
    before, snapshot = install.plan(), install.snapshot()
    assert before.refusals == []
    journal = install.apply(tmp_path / "j.json")
    assert journal.status == "applied" and failures == []
    owners = {row[2] for row in install.snapshot()}
    assert owners == {install.target}, "every object, the schema and the database"
    restores = {entry["key"]: entry["revert_grants"] for entry in journal.entries if entry["revert_grants"]}
    assert set(restores) == {"SCHEMA:mip_app", "TABLE:mip_app.campaigns"}
    assert install.revert(tmp_path / "j.json") and failures == []
    assert install.snapshot() == snapshot
    after = install.plan()
    assert after.refusals == [] and after.sha256 == before.sha256


def test_a_definer_moved_before_the_schema_fails_on_postgres(
    install: Install, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive control for the order: SECURITY DEFINER routines before the schema (the PR's first order)."""

    failures = _probe_reads(install, monkeypatch)
    first = ("TYPE", "DOMAIN", "TABLE", "SEQUENCE", "FUNCTION", "PROCEDURE", "VIEW", "MATERIALIZED VIEW", sql.DEFINER_PHASE, "SCHEMA", "DATABASE")
    monkeypatch.setattr(sql, "ORDER", first)
    install.apply(tmp_path / "j.json")
    assert failures == [("definer", "42501")]


def test_entries_the_revert_cannot_restore_exactly_refuse(install: Install) -> None:
    target, app = _identifier(install.target), _identifier(install.app)
    # USAGE, granted by the legacy owner, is restorable: the target needs it to grant in mip_app.
    install.run(install.legacy, f"GRANT USAGE ON SCHEMA mip_app TO {target}", f"GRANT SELECT ON mip_app.approvals TO {target} WITH GRANT OPTION")
    assert install.plan().refusals == ["target_grant_unrevertible"], "the grant option"
    install.run(install.target, f"GRANT SELECT ON mip_app.approvals TO {app}")  # the target as grantor
    assert install.plan().refusals == ["target_grant_unrevertible"] * 2
    # The legacy owner's own entry lacks what it granted the target: the revert would leave it holding it.
    install.run(install.legacy, "REVOKE TRUNCATE ON mip_app.campaigns FROM CURRENT_USER", f"GRANT TRUNCATE ON mip_app.campaigns TO {target}")
    assert install.plan().refusals == ["target_grant_unrevertible"] * 3


def test_preconditions_name_every_unusable_schema_and_residue_reads_every_database(install: Install, tmp_path: Path) -> None:
    install.run(install.legacy, "CREATE SCHEMA extra_a", "CREATE SCHEMA extra_b", "CREATE EXTENSION citext SCHEMA extra_a")
    install.run(
        install.legacy,
        "CREATE EXTENSION citext",
        "ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC",
        database=install.other,
    )
    statements, refusals = install.preconditions()
    target = _identifier(install.target)
    assert statements == [f'GRANT USAGE ON SCHEMA "extra_a" TO {target};', f'GRANT USAGE ON SCHEMA "extra_b" TO {target};']
    assert refusals == []
    install.run(install.legacy, *statements)
    assert install.apply(tmp_path / "j.json").status == "applied"
    with install.read_session() as cursor:
        residue = roles.legacy_residue(cursor, legacy=install.legacy)
        # Both databases: the grants job's own row and the other database's; citext in each.
        assert residue["default_acl"] == 2 and residue["owns_extension"] == 2
        assert residue["owns_object"] >= 3, "the other database and the two schemas"
        with pytest.raises(roles.LegacyRoleNotFound, match="1 role"):
            roles.legacy_residue(cursor, legacy=install.legacy.upper())


def _membership(install: Install, role: str, member: str, *, inherit: bool, set_option: bool) -> None:
    """``role``'s owner (the superuser here) makes ``member`` a member, replacing any membership it had."""

    with psycopg.connect(install.dsn, autocommit=True) as admin:
        admin.execute(f"REVOKE {_identifier(role)} FROM {_identifier(member)}")
        if inherit or set_option:
            admin.execute(f"GRANT {_identifier(role)} TO {_identifier(member)} WITH INHERIT {inherit}, SET {set_option}")


def test_a_database_another_role_owns_refuses_until_the_target_may_act_as_its_owner(install: Install, tmp_path: Path) -> None:
    """Round 2: the handover leaves the App database to another role.

    Before, the plan refused ``target_needs_schema_usage`` for ``public`` and
    ``--preconditions`` printed a USAGE grant there. That planned clean, but
    the grants job's exact public-schema gate refuses the row it adds, and its
    closure needs the database owner's SET. The plan now refuses until the
    target holds that owner's SET and INHERIT, and prints no SQL for it.
    """

    owner, target = install.role("database_owner"), _identifier(install.target)
    _membership(install, owner, install.legacy, inherit=True, set_option=True)
    with psycopg.connect(install.dsn, autocommit=True) as admin:
        admin.execute(f"ALTER DATABASE {_identifier(install.database)} OWNER TO {_identifier(owner)}")
    install.provider_public_acl()
    install.close_public_schema(install.legacy)  # the reviewed ACL passes the gate, and the legacy role may close it
    assert install.plan().refusals == ["target_will_not_own_database"]
    assert install.preconditions() == ([], ["target_will_not_own_database"])
    with pytest.raises(RuntimeError, match="closure authority mismatch"):
        install.close_public_schema(install.target)
    # What --preconditions printed before: the gate refuses its row, and the plan still refuses.
    install.run(install.legacy, f"GRANT USAGE ON SCHEMA public TO {target}")
    with pytest.raises(RuntimeError, match="public-schema ACL mismatch"):
        install.close_public_schema(install.legacy)
    assert install.plan().refusals == ["target_will_not_own_database"]
    install.run(install.legacy, f"REVOKE USAGE ON SCHEMA public FROM {target}")
    # SET alone passes the closure's authority but not the revokes; INHERIT alone the reverse.
    for inherit, set_option in ((False, True), (True, False)):
        _membership(install, owner, install.target, inherit=inherit, set_option=set_option)
        assert install.plan().refusals == ["target_will_not_own_database"], (inherit, set_option)
    _membership(install, owner, install.target, inherit=True, set_option=True)
    assert install.plan().refusals == [] and install.preconditions() == ([], [])
    journal = install.apply(tmp_path / "j.json")
    assert journal.status == "applied" and not any(entry["kind"] == "DATABASE" for entry in journal.entries)
    install.grants_job(install.target)
    install.close_public_schema(install.target)


def test_with_the_owners_set_alone_the_grants_job_run_as_the_target_fails(install: Install, tmp_path: Path) -> None:
    """Positive control for the INHERIT half: SET passes the closure, and the revokes in ``public`` fail."""

    owner = install.role("database_owner")
    _membership(install, owner, install.legacy, inherit=True, set_option=True)
    with psycopg.connect(install.dsn, autocommit=True) as admin:
        admin.execute(f"ALTER DATABASE {_identifier(install.database)} OWNER TO {_identifier(owner)}")
    _membership(install, owner, install.target, inherit=False, set_option=True)
    install.provider_public_acl()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(handover, "acts_as_database_owner", lambda *_args, **_kwargs: True)
        assert install.apply(tmp_path / "j.json").status == "applied"
    install.close_public_schema(install.target)
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="permission denied for schema public"):
        install.grants_job(install.target)


def test_an_object_whose_owner_the_legacy_role_cannot_act_for_prints_no_grant(install: Install, tmp_path: Path) -> None:
    """Round 2: ``caller_can_grant`` on real PostgreSQL. A GRANT the legacy owner cannot run is never printed."""

    vendor, target = install.role("vendor"), _identifier(install.target)
    install.run(install.legacy, "CREATE SCHEMA extras", f"GRANT USAGE ON SCHEMA extras TO {target}, {_identifier(vendor)}")
    with psycopg.connect(make_conninfo(install.dsn, dbname=install.database), autocommit=True) as admin:
        admin.execute("CREATE TABLE extras.vendor (id integer)")
        admin.execute(f"ALTER TABLE extras.vendor OWNER TO {_identifier(vendor)}")
    assert install.plan().refusals == ["target_needs_object_privilege"]
    assert install.preconditions() == ([], ["target_needs_object_privilege"])
    install.run(vendor, f"GRANT SELECT ON extras.vendor TO {target}")  # its owner grants it
    assert install.plan().refusals == [] and install.preconditions() == ([], [])
    assert install.apply(tmp_path / "j.json").status == "applied"
    install.grants_job(install.target)


def test_only_a_privilege_the_revoke_accepts_reaches_an_object(install: Install, tmp_path: Path) -> None:
    """Round 2: the reach SQL's privilege lists, then the grants job run as the target.

    REVOKE ALL on a relation also checks every column, where only a column
    privilege counts: TRIGGER alone on a table and USAGE alone on a sequence
    refuse, and --preconditions prints SELECT for each; REFERENCES and UPDATE,
    privileges other than the remedy's, are enough.
    """

    _extras(install)
    target = _identifier(install.target)
    install.run(
        install.legacy,
        f"GRANT USAGE ON SCHEMA extras TO {target}",
        f"GRANT TRIGGER ON extras.synced TO {target}",
        f"GRANT USAGE ON SEQUENCE extras.standalone TO {target}",
        f"GRANT EXECUTE ON FUNCTION extras.refresh(date) TO {target}",
    )
    assert install.plan().refusals == ["target_needs_object_privilege"] * 2
    assert install.preconditions() == (
        [f'GRANT SELECT ON SEQUENCE "extras"."standalone" TO {target};', f'GRANT SELECT ON TABLE "extras"."synced" TO {target};'],
        [],
    )
    install.run(
        install.legacy,
        f"GRANT REFERENCES ON extras.synced TO {target}",
        f"GRANT UPDATE ON SEQUENCE extras.standalone TO {target}",
    )
    assert install.plan().refusals == [] and install.preconditions() == ([], [])
    assert install.apply(tmp_path / "j.json").status == "applied"
    install.grants_job(install.target)


@pytest.mark.parametrize(
    ("held", "relation"),
    [("TRIGGER ON TABLE", "synced"), ("USAGE ON SEQUENCE", "standalone")],
    ids=("trigger_on_a_table", "usage_on_a_sequence"),
)
def test_with_a_privilege_the_revoke_does_not_accept_the_grants_job_fails(
    install: Install, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, held: str, relation: str
) -> None:
    """Positive control at the broken layer: the fix round's "any privilege" lists planned these clean."""

    _extras(install)
    target = _identifier(install.target)
    reached = {"synced": "SELECT ON TABLE", "standalone": "SELECT ON SEQUENCE"}
    reached[relation] = held
    install.run(
        install.legacy,
        f"GRANT USAGE ON SCHEMA extras TO {target}",
        f"GRANT EXECUTE ON FUNCTION extras.refresh(date) TO {target}",
        *(f"GRANT {privilege} extras.{name} TO {target}" for name, privilege in reached.items()),
    )
    monkeypatch.setattr(handover, "unreachable_objects", lambda *_args, **_kwargs: [])
    assert install.plan().refusals == []
    assert install.apply(tmp_path / "j.json").status == "applied"
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match=f'permission denied for column "tableoid" of relation "{relation}"'):
        install.grants_job(install.target)
