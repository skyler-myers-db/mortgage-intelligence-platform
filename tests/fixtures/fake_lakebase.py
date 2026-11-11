"""A fake psycopg server for the Lakebase handover, on a virtual clock.

``FakeServer`` holds the ``mip_app`` objects and their owners, the roles, and
any long App transaction that holds a lock on a table until ``held_until``.
An ``ALTER ... OWNER TO`` on a held table waits like PostgreSQL does: until
the holder ends, or until the transaction's ``lock_timeout``, whichever is
first, and then raises ``LockNotAvailable`` (SQLSTATE 55P03). Without a
``SET LOCAL lock_timeout`` it waits as long as the holder runs. Every wait is
recorded in ``exclusive_waits``: an App query arriving during one queues
behind it, so the longest wait is the longest any App query was blocked.

It models what spike S3 measured on PostgreSQL 16.15 (each pinned by a test
in tests/unit/test_lakebase_handover_s3.py):

* privileges are ACL entries ``(grantee, grantor, privilege) -> grantable``;
  the owner holds everything, a role inheriting the owner (the legacy caller,
  through its INHERIT membership in the target) too, the database owner holds
  ``pg_database_owner``'s, and nobody else holds anything not granted;
* ``ALTER ... OWNER TO`` rewrites the old owner to the new one as grantee and
  grantor and merges duplicates (``aclnewowner``), so an entry the legacy
  owner granted the target merges into the target's owner entry, and the
  inverse leaves the target nothing (S3, answer 3b);
* the new owner of a schema's object needs CREATE on that schema;
* a view, or a SECURITY DEFINER routine, reads with its owner's privileges
  (``owner_reads``), so one the target owns cannot read a table the legacy
  role still owns: 42501 (S3, answer 3c); a definer routine's owner also
  needs USAGE on its schema, which L-A gives the target only once it owns
  the schema (review of PR #336);
* the rehearsal's ``CREATE SCHEMA`` leaves the target no CREATE there (S3,
  answer 6: the first ``--rehearse`` refused ``target_needs_schema_create``);
* the legacy role keeps the extensions and its ``pg_default_acl`` row the
  handover does not move (S3, answer 3b: ``pgcrypto`` and one row).

Time is virtual (``clock``/``sleep``), a rolled-back transaction leaves
owners and ACLs as they were, and every executed statement is recorded. Reads
go through the migration's read-only guard, as ``read_only_session`` does.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from jobs.lakebase_migration_read_only import assert_read_statement
from tools.clients.lakebase_handover_acl import GRANT_KEYWORDS, SCHEMA_USAGE_SQL, TARGET_ACL_SQL
from tools.clients.lakebase_handover_reach import OBJECT_REACH_SQL
from tools.clients.lakebase_handover_rehearse import (
    INSTALL_SCHEMA_SQL,
    OWNER_SQL,
    REHEARSAL_SCHEMA,
    SETUP,
)
from tools.clients.lakebase_handover_roles import FACTS_SQL, LEGACY_ROLE_SQL, RESIDUE_SQL
from tools.clients.lakebase_handover_sql import AUTHORITY_SQL, INVENTORY_SQL, PgObject

LEGACY = "legacy-owner@example.com"
DEPLOYER = "00000000-0000-4000-8000-000000000001"
APP_ROLE = "00000000-0000-4000-8000-000000000099"
DATABASE_OWNER = "pg_database_owner"
_ALTER = re.compile(r'(?P<head>ALTER [A-Z ]+? .+) OWNER TO "(?P<owner>(?:[^"]|"")+)"')
_BOUND = re.compile(r"SET LOCAL (?P<name>lock_timeout|statement_timeout) = '(?P<seconds>[0-9]+)s'")
_KEYWORDS = "|".join(sorted(set(GRANT_KEYWORDS.values())))
_PRIVILEGES = r"(?P<privileges>[A-Z]+(?:, [A-Z]+)*)"
_ROLE = r'"(?P<role>(?:[^"]|"")+)"'
_GRANT = re.compile(rf"GRANT {_PRIVILEGES} ON (?P<keyword>{_KEYWORDS}) (?P<object>.+) TO {_ROLE}(?P<option> WITH GRANT OPTION)?")
_REVOKE = re.compile(rf"REVOKE {_PRIVILEGES} ON (?P<keyword>{_KEYWORDS}) (?P<object>.+) FROM {_ROLE}")
_SYSTEM_SCHEMA = re.compile(r"pg_.*|information_schema|__db_system")

REHEARSAL_KEY = f"TABLE:{REHEARSAL_SCHEMA}.rehearsal_target"
Acl = dict[tuple[str, str, str], bool]  # (grantee, grantor, privilege) -> grantable


class LockNotAvailable(Exception):
    sqlstate = "55P03"


class SyntaxProblem(Exception):
    sqlstate = "42601"


class InsufficientPrivilege(Exception):
    sqlstate = "42501"


def throwaway() -> FakeServer:
    """A rehearsal's throwaway database: no mip_app, so only the rehearsal's own schema and table appear."""

    return FakeServer(objects=[], acl={})


def objects() -> list[PgObject]:
    """The App database and a small mip_app: tables, a view, routines, a type, one foreign-owned."""

    return [
        PgObject("DATABASE", "", "mip_app_state", "", LEGACY),
        PgObject("SCHEMA", "", "mip_app", "", LEGACY),
        PgObject("TABLE", "mip_app", "campaigns", "", LEGACY),
        PgObject("TABLE", "mip_app", "approvals", "", LEGACY),
        PgObject("TABLE", "mip_app", "action_audit", "", LEGACY),
        PgObject("VIEW", "mip_app", "campaign_summary", "", LEGACY),
        PgObject("SEQUENCE", "mip_app", "standalone_seq", "", LEGACY),
        PgObject("FUNCTION", "mip_app", "audit_guard", "", LEGACY),
        PgObject("FUNCTION", "mip_app", "touch_row", "row_id bigint, reason text", LEGACY),
        PgObject("FUNCTION", "mip_app", "approval_count", "", LEGACY, definer=True),
        PgObject("TYPE", "mip_app", "approval_state", "", LEGACY),
        PgObject("TABLE", "mip_app", "app_owned_scratch", "", APP_ROLE),
    ]


# PostgreSQL's acldefault: the owner's own entry holds every privilege of the
# kind (no grant-option bits; an owner's grant options are implicit).
OWNER_PRIVILEGES = {
    "DATABASE": ("CREATE", "CONNECT", "TEMPORARY"),
    "SCHEMA": ("USAGE", "CREATE"),
    "TABLE": ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"),
    "VIEW": ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"),
    "MATERIALIZED VIEW": ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"),
    "SEQUENCE": ("USAGE", "SELECT", "UPDATE"),
    "FUNCTION": ("EXECUTE",),
    "PROCEDURE": ("EXECUTE",),
    "TYPE": ("USAGE",),
    "DOMAIN": ("USAGE",),
}


# PostgreSQL's acldefault for PUBLIC: what an object with no explicit ACL grants everyone.
PUBLIC_DEFAULTS = {"FUNCTION": ("EXECUTE",), "PROCEDURE": ("EXECUTE",), "DATABASE": ("CONNECT", "TEMPORARY"), "TYPE": ("USAGE",), "DOMAIN": ("USAGE",)}
# The GRANT ... ON keyword of each kind the grants job revokes on.
_REVOKED_KINDS = {
    "TABLE": "TABLE",
    "VIEW": "TABLE",
    "MATERIALIZED VIEW": "TABLE",
    "FOREIGN TABLE": "TABLE",
    "SEQUENCE": "SEQUENCE",
    "FUNCTION": "FUNCTION",
    "PROCEDURE": "PROCEDURE",
}


def owner_entries(key: str, owner: str) -> Acl:
    """The owner's own entry on ``key`` once its ACL is explicit, as PostgreSQL writes it."""

    return {(owner, owner, privilege): False for privilege in OWNER_PRIVILEGES[key.partition(":")[0]]}


def acl() -> dict[str, Acl]:
    """The install's explicit entries: owner entries, the App's grants, and L-A's CREATE on mip_app for the target."""

    return {
        "SCHEMA:mip_app": {**owner_entries("SCHEMA:", LEGACY), (APP_ROLE, LEGACY, "USAGE"): False, (DEPLOYER, LEGACY, "CREATE"): False},
        "TABLE:mip_app.campaigns": {**owner_entries("TABLE:", LEGACY), (APP_ROLE, LEGACY, "SELECT"): False},
        "VIEW:mip_app.campaign_summary": {**owner_entries("VIEW:", LEGACY), (APP_ROLE, LEGACY, "SELECT"): False},
        "FUNCTION:mip_app.approval_count()": {**owner_entries("FUNCTION:", LEGACY), (APP_ROLE, LEGACY, "EXECUTE"): False},
        # Another role's mip_app table: the handover leaves it, so the target
        # holds a privilege on it, or the grants job it runs could not revoke there.
        "TABLE:mip_app.app_owned_scratch": {**owner_entries("TABLE:", APP_ROLE), (DEPLOYER, APP_ROLE, "SELECT"): False},
    }


_TAGS = {
    "TABLE": "relkind:r",
    "VIEW": "relkind:v",
    "MATERIALIZED VIEW": "relkind:m",
    "SEQUENCE": "relkind:S",
    "FOREIGN TABLE": "relkind:f",
    "FUNCTION": "prokind:f",
    "PROCEDURE": "prokind:p",
    "TYPE": "typtype:e",
    "DOMAIN": "typtype:d",
}


def _tag(item: PgObject, *, definer_marker: bool) -> str:
    """The row tag PostgreSQL returns: the ``:definer`` marker only when the SQL selects ``prosecdef``."""

    tag = _TAGS.get(item.kind, item.kind)
    return f"{tag}:definer" if definer_marker and item.definer else tag


@dataclass
class FakeServer:
    caller: str = LEGACY
    roles: set[str] = field(default_factory=lambda: {LEGACY, DEPLOYER, APP_ROLE})
    # The caller's memberships, as PostgreSQL 16 grants them: SET (it may give
    # an object to the role) and INHERIT (it holds the role's privileges, so
    # it may change the owner of what the role owns).
    member_of: frozenset[str] = frozenset({DEPLOYER})
    settable: bool = True
    inherits: bool = True
    # The caller holds ADMIN on the target (it created it with databricks_create_role).
    admin_edge: bool = True
    # The caller's own PostgreSQL preconditions (AUTHORITY_SQL); "schema_create"
    # False forces the target's CREATE off whatever the ACL says.
    authority: dict[str, bool] = field(default_factory=lambda: {"schema_create": True, "database_create": True, "createdb": True})
    objects: list[PgObject] = field(default_factory=objects)
    owners: dict[str, str] = field(default_factory=dict)
    acl: dict[str, Acl] = field(default_factory=acl)
    # Schemas outside the inventory (name -> owner); ``public`` is the database owner's.
    schemas: dict[str, str] = field(default_factory=lambda: {"public": DATABASE_OWNER})
    # What reads with its owner's privileges (a view, a SECURITY DEFINER routine) -> what it reads.
    owner_reads: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: {
            "VIEW:mip_app.campaign_summary": ("TABLE:mip_app.campaigns",),
            "FUNCTION:mip_app.approval_count()": ("TABLE:mip_app.approvals",),
        }
    )
    column_grants: list[tuple[str, str, str, str, str]] = field(default_factory=list)  # key, column, grantee, grantor, privilege
    extensions: dict[str, str] = field(default_factory=lambda: {"pgcrypto": LEGACY, "databricks_auth": "cloud_admin"})
    default_acl_roles: list[str] = field(default_factory=lambda: [LEGACY])
    other_owned: int = 0  # objects the legacy role owns outside this inventory (another database)
    memberships_granted: int = 0  # memberships the legacy role granted other roles
    held_until: dict[str, float] = field(default_factory=dict)  # object key -> release time
    now: float = 0.0
    statements: list[str] = field(default_factory=list)
    transactions: list[list[str]] = field(default_factory=list)
    exclusive_waits: list[tuple[float, float]] = field(default_factory=list)
    connections: int = 0
    fail_on: set[str] = field(default_factory=set)
    after_commit: list[Callable[[list[str]], None]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.owners = {item.key: item.owner for item in self.objects} | self.owners

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def hold(self, key: str, seconds: float) -> None:
        """A long App transaction holds a conflicting lock on ``key`` for ``seconds``."""

        self.held_until[key] = self.now + seconds

    def max_app_block(self) -> float:
        return max((end - start for start, end in self.exclusive_waits), default=0.0)

    def connect(self) -> FakeConnection:
        self.connections += 1
        return FakeConnection(self)

    @contextmanager
    def read_session(self) -> Any:
        yield FakeCursor(self, None, read_only=True)

    # -- privileges ---------------------------------------------------------

    def owner_of(self, key: str) -> str:
        kind, _, name = key.partition(":")
        return self.owners.get(key) or (self.schemas.get(name, "") if kind == "SCHEMA" else "")

    def database_key(self) -> str:
        return next((item.key for item in self.objects if item.kind == "DATABASE"), "DATABASE:throwaway")

    def effective(self, role: str) -> set[str]:
        held = {role, "PUBLIC"}
        if role == self.caller and self.inherits:
            held |= {member for member in self.member_of if member in self.roles}
        if self.owner_of(self.database_key()) in held:
            held.add(DATABASE_OWNER)
        return held

    def entries(self, key: str) -> Acl:
        """``key``'s ACL; with none explicit, PostgreSQL's default (the owner's, and PUBLIC's for some kinds)."""

        if key in self.acl:
            return self.acl[key]
        defaults = PUBLIC_DEFAULTS.get(key.partition(":")[0], ())
        return {("PUBLIC", self.owner_of(key), privilege): False for privilege in defaults}

    def has_privilege(self, role: str, key: str, privilege: str | None = None) -> bool:
        """``privilege`` held by ``role`` (any privilege when None): as owner, inherited, or through PUBLIC."""

        held = self.effective(role)
        if self.owner_of(key) in held:
            return True
        return any(grantee in held and privilege in (None, name) for grantee, _grantor, name in self.entries(key))

    def in_inventory(self, item: PgObject) -> bool:
        """What INVENTORY_SQL and TARGET_ACL_SQL read: the database, mip_app and its objects."""

        return item.kind == "DATABASE" or item.schema == "mip_app" or (item.kind == "SCHEMA" and item.name == "mip_app")

    def check_owner_change(self, item: PgObject, owner: str, new_owner: str) -> None:
        """PostgreSQL 16's ALTER ... OWNER TO checks: own (or inherit) it, SET to the new owner, who has CREATE on the schema."""

        if owner != self.caller and not (owner in self.member_of and self.inherits):
            raise InsufficientPrivilege("must be owner")
        if new_owner != self.caller and not (new_owner in self.member_of and self.settable):
            raise InsufficientPrivilege("must be able to SET ROLE")
        if item.schema and not self.has_privilege(new_owner, f"SCHEMA:{item.schema}", "CREATE"):
            raise InsufficientPrivilege("permission denied for schema")

    def change_owner(self, key: str, old: str, new: str) -> None:
        """``aclnewowner``: substitute the new owner for the old as grantee and grantor, merging duplicates."""

        merged: Acl = {}
        for (grantee, grantor, privilege), grantable in self.acl.get(key, {}).items():
            entry = (new if grantee == old else grantee, new if grantor == old else grantor, privilege)
            merged[entry] = merged.get(entry, False) or grantable
        if key in self.acl:
            self.acl[key] = merged
        self.owners[key] = new

    def app_read(self, key: str) -> None:
        """An App read through ``key``, which reads its sources with its owner's privileges.

        A SECURITY DEFINER routine also resolves the names in its body as its
        owner, so the owner needs USAGE on the routine's schema; a view's
        query was resolved when it was created, so it needs none (both
        measured on PostgreSQL 16.15).
        """

        owner = self.owner_of(key)
        routine = next((item for item in self.objects if item.key == key and item.definer), None)
        if routine is not None and not self.has_privilege(owner, f"SCHEMA:{routine.schema}", "USAGE"):
            raise InsufficientPrivilege("permission denied for schema")
        for source in self.owner_reads.get(key, ()):
            if not self.has_privilege(owner, source, "SELECT"):
                raise InsufficientPrivilege("permission denied for table")

    # -- the statements the fake executes -----------------------------------

    def object_for(self, head: str) -> PgObject:
        for item in self.objects:
            if f"ALTER {item.kind} {item.target()}" == head:
                return item
        raise SyntaxProblem("no such object")

    def granted_object(self, keyword: str, target: str) -> PgObject:
        schemas = [PgObject("SCHEMA", "", name, "", owner) for name, owner in self.schemas.items()]
        for item in [*self.objects, *schemas]:
            if GRANT_KEYWORDS.get(item.kind) == keyword and item.target() == target:
                return item
        raise SyntaxProblem("no such object")

    def grantor_for(self, key: str) -> str | None:
        """Who a GRANT or REVOKE acts as: the owner, when the caller is or inherits it; else nobody (PostgreSQL warns)."""

        owner = self.owner_of(key)
        return owner if owner in self.effective(self.caller) else None

    def residue(self, legacy: str) -> list[tuple[str, int]]:
        owned = sum(1 for key in {*self.owners, *(f"SCHEMA:{name}" for name in self.schemas)} if self.owner_of(key) == legacy)
        named = sum(
            1
            for key, entries in self.acl.items()
            if self.owner_of(key) != legacy and any(legacy in (grantee, grantor) for grantee, grantor, _name in entries)
        )
        found = {
            "owns_extension": sum(1 for owner in self.extensions.values() if owner == legacy),
            "default_acl": self.default_acl_roles.count(legacy),
            "owns_object": owned + self.other_owned,
            "holds_privilege": named,
            "granted_membership": self.memberships_granted,
        }
        return sorted((name, count) for name, count in found.items() if count)


class FakeConnection:
    def __init__(self, server: FakeServer) -> None:
        self.server = server
        self._txn: dict[str, Any] | None = None
        self.closed = False

    @contextmanager
    def transaction(self) -> Any:
        server = self.server
        snapshot = (dict(server.owners), copy.deepcopy(server.acl), dict(server.schemas))
        self._txn = {"statements": [], "bounds": {}}
        try:
            yield self
        except BaseException:
            server.owners, server.acl, server.schemas = snapshot
            server.transactions.append(self._txn["statements"] + ["ROLLBACK"])
            self._txn = None
            raise
        committed = self._txn["statements"]
        server.transactions.append(committed + ["COMMIT"])
        self._txn = None
        for hook in server.after_commit:
            hook(committed)

    @contextmanager
    def cursor(self) -> Any:
        yield FakeCursor(self.server, self._txn)

    def close(self) -> None:
        self.closed = True


class FakeCursor:
    def __init__(self, server: FakeServer, txn: dict[str, Any] | None, *, read_only: bool = False) -> None:
        self.server = server
        self.txn = txn
        self.read_only = read_only
        self._rows: list[tuple[Any, ...]] = []

    def _read(self, statement: str, params: Any) -> list[tuple[Any, ...]] | None:
        server = self.server
        if statement == INVENTORY_SQL:
            marker = "prosecdef" in statement
            return [
                (_tag(item, definer_marker=marker), item.schema, item.name, item.signature, server.owners[item.key])
                for item in server.objects
                if server.in_inventory(item)
            ]
        if statement == AUTHORITY_SQL:
            target = params["target"]
            exists = target in server.roles
            grants = server.authority
            member = target in server.member_of
            schema_create = grants.get("schema_create", True) and server.has_privilege(target, f"SCHEMA:{params['schema']}", "CREATE")
            return [
                (
                    server.caller,
                    exists,
                    exists and member and server.settable,
                    exists and schema_create,
                    grants["database_create"],
                    grants["createdb"],
                    exists and member and server.inherits,
                )
            ]
        if statement == SCHEMA_USAGE_SQL:
            names = {item.name for item in server.objects if item.kind == "SCHEMA"} | set(server.schemas)
            missing = []
            for name in sorted(names):
                key = f"SCHEMA:{name}"
                if _SYSTEM_SCHEMA.fullmatch(name) or name == params["schema"]:
                    continue
                if params["owns_database"] and server.owner_of(key) == DATABASE_OWNER:
                    continue
                if params["target"] not in server.roles or not server.has_privilege(params["target"], key, "USAGE"):
                    missing.append((name,))
            return missing
        if statement == TARGET_ACL_SQL:
            return self._target_acl(params["target"])
        if statement == OBJECT_REACH_SQL:
            return self._unreachable(params["target"], params["legacy"], params["schema"])
        if statement == FACTS_SQL:
            target = params["target"]
            database = server.database_key().partition(":")[2]
            exists = target in server.roles
            return [(server.caller, database, server.owner_of(server.database_key()), exists, exists and server.admin_edge, "databricks_auth" in server.extensions)]
        if statement == LEGACY_ROLE_SQL:
            legacy = params["legacy"]
            return [(legacy in server.roles, sum(1 for role in server.roles if role.lower() == legacy.lower() and role != legacy))]
        if statement == RESIDUE_SQL:
            return server.residue(params["legacy"]) if params["legacy"] in server.roles else []
        if statement == INSTALL_SCHEMA_SQL:
            schema = params["schema"]
            return [(any(item.schema == schema or (item.kind == "SCHEMA" and item.name == schema) for item in server.objects),)]
        if statement == OWNER_SQL:
            return [(server.owners[REHEARSAL_KEY],)]
        return None

    def _unreachable(self, target: str, legacy: str, schema: str) -> list[tuple[Any, ...]]:
        """OBJECT_REACH_SQL: what the grants job revokes on, the handover leaves, and the target holds nothing on."""

        server = self.server
        if target not in server.roles:
            return []
        rows = []
        for item in server.objects:
            owner = server.owner_of(item.key)
            if item.kind == "DATABASE":
                keyword, left_out = "DATABASE", owner == legacy  # the handover moves it
            elif item.kind in _REVOKED_KINDS and not _SYSTEM_SCHEMA.fullmatch(item.schema):
                keyword = _REVOKED_KINDS[item.kind]
                # Moved by the handover, or the provider's own (the grants job skips it).
                left_out = (item.schema == schema and owner == legacy) or (item.schema == "public" and owner == "cloud_admin")
            else:
                continue
            if not left_out and not server.has_privilege(target, item.key):
                rows.append((keyword, item.schema, item.name, item.signature, owner in server.effective(server.caller)))
        return sorted(rows)

    def _target_acl(self, target: str) -> list[tuple[Any, ...]]:
        server = self.server
        rows = []
        for item in server.objects:
            if not server.in_inventory(item):
                continue
            owner = server.owner_of(item.key)
            for (grantee, grantor, privilege), grantable in sorted(server.acl.get(item.key, {}).items()):
                owner_entry = grantee == grantor == owner
                if target in (grantee, grantor) or owner_entry:
                    tag = _tag(item, definer_marker=False)
                    rows.append((tag, item.schema, item.name, item.signature, "", owner, privilege, grantable, grantee == target, grantor, owner_entry))
        for key, column, grantee, grantor, privilege in server.column_grants:
            item = next(item for item in server.objects if item.key == key)
            if target in (grantee, grantor):
                rows.append(("column:r", item.schema, item.name, "", column, server.owner_of(key), privilege, False, grantee == target, grantor, False))
        return rows

    def execute(self, statement: str, params: Any = None) -> None:
        server = self.server
        server.statements.append(statement)
        if self.read_only:
            assert_read_statement(statement)
        rows = self._read(statement, params)
        if rows is not None:
            self._rows = rows
            return
        assert self.txn is not None, "a write outside a transaction"
        self.txn["statements"].append(statement)
        if statement in SETUP:
            if statement == SETUP[0]:
                server.schemas.setdefault(REHEARSAL_SCHEMA, server.caller)
            elif REHEARSAL_KEY not in server.owners:
                rehearsal = PgObject("TABLE", REHEARSAL_SCHEMA, "rehearsal_target", "", server.caller)
                server.objects.append(rehearsal)
                server.owners[rehearsal.key] = rehearsal.owner
            return
        bound = _BOUND.fullmatch(statement)
        if bound:
            self.txn["bounds"][bound.group("name")] = float(bound.group("seconds"))
            return
        for pattern, granting in ((_GRANT, True), (_REVOKE, False)):  # fail_on: an object's REVOKE fails
            match = pattern.fullmatch(statement)
            if match is not None:
                self._grant(match, granting=granting)
                return
        match = _ALTER.fullmatch(statement)
        if match is None:
            raise SyntaxProblem("the fake accepts only lock bounds, owner changes, grants and revokes")
        item = server.object_for(match.group("head"))
        if item.key in server.fail_on:
            raise SyntaxProblem("injected failure")
        new_owner = match.group("owner").replace('""', '"')
        current = server.owners[item.key]
        server.check_owner_change(item, current, new_owner)
        release = server.held_until.get(item.key, 0.0)
        if release > server.now:
            limit = self.txn["bounds"].get("lock_timeout")
            wait = release - server.now if limit is None else min(limit, release - server.now)
            server.exclusive_waits.append((server.now, server.now + wait))
            server.now += wait
            if server.now < release:
                raise LockNotAvailable("canceling statement due to lock timeout")
        server.change_owner(item.key, current, new_owner)

    def _grant(self, match: re.Match[str], *, granting: bool) -> None:
        server = self.server
        item = server.granted_object(match.group("keyword"), match.group("object"))
        if not granting and item.key in server.fail_on:
            raise SyntaxProblem("injected failure")
        role = match.group("role").replace('""', '"')
        grantor = server.grantor_for(item.key)
        if grantor is None:
            return  # PostgreSQL: WARNING no privileges were granted (or revoked)
        if item.key not in server.acl:
            # The first change makes the default ACL explicit: the owner's entry, and PUBLIC's defaults.
            server.acl[item.key] = {**owner_entries(item.key, server.owner_of(item.key)), **server.entries(item.key)}
        entries = server.acl[item.key]
        for privilege in match.group("privileges").split(", "):
            if granting:
                entries[(role, grantor, privilege)] = entries.get((role, grantor, privilege), False) or bool(match.group("option"))
            else:
                entries.pop((role, grantor, privilege), None)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None


class VirtualConcurrent:
    """The rehearsal's long transaction and probe, on the server's virtual clock."""

    def __init__(self, server: FakeServer, hold_s: float) -> None:
        self.server = server
        self.hold_s = hold_s

    def start(self) -> None:
        self.server.hold(REHEARSAL_KEY, self.hold_s)

    def join(self) -> list[tuple[float, float]]:
        # An App query arriving during an ALTER's wait queues behind it: the waits are the samples.
        return list(self.server.exclusive_waits)
