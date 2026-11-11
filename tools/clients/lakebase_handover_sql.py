"""The Lakebase ownership handover's SQL: a read-only inventory, owner changes, lock bounds (M2-PR8).

The handover moves the App database and every ``mip_app`` object the legacy
owner's Postgres role owns to the deployer's role, one object per short
transaction (cutover step 8):

  BEGIN;
  SET LOCAL lock_timeout = '3s';
  SET LOCAL statement_timeout = '30s';
  ALTER <kind> <object> OWNER TO "<deployer role>";
  COMMIT;

``ALTER ... OWNER`` takes an ACCESS EXCLUSIVE lock on a table. While it waits
behind a long App transaction, every App query that arrives queues behind it,
so ``lock_timeout`` is what bounds an App query's wait: after 3 s the request
is cancelled, the queue drains, and the object is retried later (attempt and
total budgets in ``LockBudget``). Once granted, the change is a catalog row
update and commits at once. Ownership never touches grants: every privilege
the App, verifier and jobs-reader roles hold stays as it is.

The apply sends only ``ALTER ... OWNER TO`` and the two ``SET LOCAL`` bounds;
the revert adds, in the same transaction as each object's inverse, the
target's own explicit grants the apply merged away (journaled before the
first change, tools/clients/lakebase_handover_acl.py). There is no DROP,
DELETE, TRUNCATE or REASSIGN, and roles are never created or dropped (the
owner creates the deployer's role by spike S3's L-A mechanism,
``--preconditions``).

Apply order (spike S3, answer 6): a view, a materialized view's refresh and a
SECURITY DEFINER routine read with their OWNER's privileges. Under L-A the
legacy role inherits the target's privileges, but the target holds none on
what the legacy role still owns, so tables and standalone sequences move
first (a column's own sequence moves with its table), then invoker routines,
then views and materialized views, then the schema, then SECURITY DEFINER
routines, then the database. Moving views before tables made every App read
through a view fail with 42501 for as long as the handover ran (spike S3,
R1-R3). A SECURITY DEFINER routine also resolves the names in its body as its
owner, which needs USAGE on ``mip_app``: L-A gives the target only CREATE
there, so a definer routine moved before the schema failed 42501 "permission
denied for schema mip_app" until the schema followed, in both directions
(PostgreSQL 16.15, review of PR #336); after the schema, a legacy-owned definer
resolves through the target's ownership, which the legacy role inherits. A
view that calls a SECURITY DEFINER routine would still fail for that
routine's window; the schema has neither today.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

SCHEMA_NAME = "mip_app"
LOCK_TIMEOUT = "3s"
STATEMENT_TIMEOUT = "30s"
LOCK_BOUNDS = (
    f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'",
    f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'",
)
# SQLSTATEs a bounded wait ends with: lock_not_available, query_canceled.
RETRYABLE_SQLSTATES = frozenset({"55P03", "57014"})
# The object kinds that have a reviewed owner change.
KINDS = ("TYPE", "DOMAIN", "TABLE", "SEQUENCE", "FUNCTION", "PROCEDURE", "VIEW", "MATERIALIZED VIEW", "SCHEMA", "DATABASE")
# Apply phases, in order (revert: reverse): what is read before what reads it
# with its owner's privileges (module docstring). A SECURITY DEFINER routine's
# phase is DEFINER_PHASE; every other object's phase is its kind.
DEFINER_PHASE = "SECURITY DEFINER"
ORDER = ("TYPE", "DOMAIN", "TABLE", "SEQUENCE", "FUNCTION", "PROCEDURE", "VIEW", "MATERIALIZED VIEW", "SCHEMA", DEFINER_PHASE, "DATABASE")
_RELKINDS = {"r": "TABLE", "p": "TABLE", "v": "VIEW", "m": "MATERIALIZED VIEW", "S": "SEQUENCE", "c": "TYPE", "f": "FOREIGN TABLE"}
_PROKINDS = {"f": "FUNCTION", "p": "PROCEDURE"}
_TYPTYPES = {"e": "TYPE", "r": "TYPE", "d": "DOMAIN"}
# pg_get_function_identity_arguments output for the reviewed signatures.
_SIGNATURE = re.compile(r'[A-Za-z0-9_ ,."\[\]()]*')

# Read-only: the database owner and every mip_app object with its owner. A
# sequence a column owns (serial or identity) moves with its table, and a
# table's row type and an array type with their element, so neither is listed.
INVENTORY_SQL = f"""
SELECT 'DATABASE', '', datname::text, '', pg_get_userbyid(datdba)::text
FROM pg_database WHERE datname = current_database()
UNION ALL
SELECT 'SCHEMA', '', nspname::text, '', pg_get_userbyid(nspowner)::text
FROM pg_namespace WHERE nspname = '{SCHEMA_NAME}'
UNION ALL
SELECT 'relkind:' || relation.relkind::text, namespace.nspname::text, relation.relname::text, '',
       pg_get_userbyid(relation.relowner)::text
FROM pg_class relation
JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
WHERE namespace.nspname = '{SCHEMA_NAME}'
  AND relation.relkind IN ('r', 'p', 'v', 'm', 'S', 'c', 'f')
  AND NOT (relation.relkind = 'S' AND EXISTS (
      SELECT 1 FROM pg_depend dependency
      WHERE dependency.objid = relation.oid AND dependency.deptype IN ('a', 'i')
        AND dependency.refclassid = 'pg_class'::regclass))
UNION ALL
SELECT 'prokind:' || routine.prokind::text || CASE WHEN routine.prosecdef THEN ':definer' ELSE '' END,
       namespace.nspname::text, routine.proname::text,
       pg_get_function_identity_arguments(routine.oid)::text, pg_get_userbyid(routine.proowner)::text
FROM pg_proc routine
JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace
WHERE namespace.nspname = '{SCHEMA_NAME}'
UNION ALL
SELECT 'typtype:' || type_object.typtype::text, namespace.nspname::text, type_object.typname::text, '',
       pg_get_userbyid(type_object.typowner)::text
FROM pg_type type_object
JOIN pg_namespace namespace ON namespace.oid = type_object.typnamespace
WHERE namespace.nspname = '{SCHEMA_NAME}'
  AND type_object.typtype IN ('e', 'r', 'd')
  AND type_object.typelem = 0
"""
# Read-only preconditions PostgreSQL sets for ALTER ... OWNER TO, in both
# directions: the session can SET ROLE to the target (apply); it holds the
# target's privileges, USAGE (INHERIT) membership, because after the apply the
# target owns every object and only an owner may change an owner (revert: a
# SET-only membership applies and then cannot revert, 42501 "must be owner",
# proven on PostgreSQL 16); for a schema's objects the target has CREATE on
# the schema; for the schema the session has CREATE on the database; for the
# database the session has CREATEDB. Which mechanism grants them is spike S3's;
# the plan refuses before any change when one is missing.
AUTHORITY_SQL = """
SELECT current_user::text,
       EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %(target)s),
       CASE WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %(target)s)
            THEN pg_has_role(current_user, %(target)s, 'SET') ELSE FALSE END,
       CASE WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %(target)s)
             AND EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = %(schema)s)
            THEN has_schema_privilege(%(target)s, %(schema)s, 'CREATE') ELSE FALSE END,
       has_database_privilege(current_user, current_database(), 'CREATE'),
       COALESCE((SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname = current_user), FALSE),
       CASE WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %(target)s)
            THEN pg_has_role(current_user, %(target)s, 'USAGE') ELSE FALSE END
"""


class LakebaseHandoverRefused(RuntimeError):
    """A refusal before or during the handover; the message names no role or object."""


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


@dataclass(frozen=True)
class PgObject:
    kind: str  # a KINDS kind, or "FOREIGN TABLE"/"UNREVIEWED"
    schema: str
    name: str
    signature: str
    owner: str
    definer: bool = False  # a SECURITY DEFINER routine

    @property
    def phase(self) -> str:
        return DEFINER_PHASE if self.definer and self.kind in _PROKINDS.values() else self.kind

    @property
    def key(self) -> str:
        qualified = f"{self.schema}.{self.name}" if self.schema else self.name
        return f"{self.kind}:{qualified}({self.signature})" if self.kind in _PROKINDS.values() else f"{self.kind}:{qualified}"

    def target(self) -> str:
        if self.kind in {"DATABASE", "SCHEMA"}:
            return quote_ident(self.name)
        qualified = f"{quote_ident(self.schema)}.{quote_ident(self.name)}"
        if self.kind in _PROKINDS.values():
            if not _SIGNATURE.fullmatch(self.signature):
                raise LakebaseHandoverRefused("a routine signature is outside the reviewed shape")
            return f"{qualified}({self.signature})"
        return qualified


def kind_of(raw_kind: object) -> tuple[str, bool]:
    """An inventory tag (``relkind:r``, ``prokind:f:definer``, ``DATABASE``) as (kind, definer)."""

    tag, _, code = str(raw_kind).partition(":")
    if not code:
        return tag, False
    code, _, marker = code.partition(":")
    kind = {"relkind": _RELKINDS, "prokind": _PROKINDS, "typtype": _TYPTYPES}[tag].get(code, "UNREVIEWED")
    return kind, marker == "definer"


def parse_inventory(rows: Iterable[tuple[Any, ...]]) -> list[PgObject]:
    found: list[PgObject] = []
    for raw_kind, schema, name, signature, owner in rows:
        kind, definer = kind_of(raw_kind)
        found.append(PgObject(kind, str(schema or ""), str(name), str(signature or ""), str(owner or ""), definer))
    return found


def owner_statement(item: PgObject, owner: str) -> str:
    """``ALTER <kind> <object> OWNER TO <owner>``: the only change statement there is."""

    if item.kind not in KINDS:
        raise LakebaseHandoverRefused("the object kind has no reviewed owner change")
    if not owner or owner == item.owner:
        raise LakebaseHandoverRefused("the new owner must be one role other than the current owner")
    return f"ALTER {item.kind} {item.target()} OWNER TO {quote_ident(owner)}"


def sort_changes(entries: Iterable[Mapping[str, Any]], *, reverse: bool = False) -> list[dict[str, Any]]:
    """Apply order by phase (``ORDER``), then key; ``reverse`` is the revert's order."""

    def rank(entry: Mapping[str, Any]) -> int:
        position = ORDER.index(str(entry.get("phase") or entry["kind"]))
        return -position if reverse else position

    return sorted((dict(entry) for entry in entries), key=lambda entry: (rank(entry), entry["key"]))


@dataclass(frozen=True)
class LockBudget:
    """Bounded retry: ``attempts`` per object, ``total_s`` for the whole run, backoff between."""

    attempts: int = 10
    total_s: float = 600.0
    backoff_s: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 15.0)

    def pause(self, attempt: int) -> float:
        return self.backoff_s[min(attempt - 1, len(self.backoff_s) - 1)]


DEFAULT_BUDGET = LockBudget()


class BudgetExhausted(LakebaseHandoverRefused):
    """An object's lock never came within the budget: the owner picks --window or --revert."""


@contextmanager
def _transaction(connection: Any) -> Iterator[Any]:
    with connection.transaction(), connection.cursor() as cursor:
        yield cursor


def run_bounded(
    connection: Any,
    statement: str | Sequence[str],
    *,
    budget: LockBudget,
    deadline: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    on_retry: Callable[[int, str], None] = lambda _attempt, _code: None,
) -> int:
    """One statement (or one object's statements) in its own lock-bounded transaction; the attempts it took.

    A lock or statement timeout rolls the transaction back and retries after a
    backoff, at most ``budget.attempts`` times and never past ``deadline``;
    then ``BudgetExhausted``. Any other error raises at once.
    """

    statements = [statement] if isinstance(statement, str) else list(statement)
    for attempt in range(1, budget.attempts + 1):
        try:
            with _transaction(connection) as cursor:
                for bound in LOCK_BOUNDS:
                    cursor.execute(bound)
                for text in statements:
                    cursor.execute(text)
            return attempt
        except Exception as exc:
            code = str(getattr(exc, "sqlstate", "") or "")
            if code not in RETRYABLE_SQLSTATES:
                raise
            on_retry(attempt, code)
            pause = budget.pause(attempt)
            if attempt == budget.attempts or clock() + pause >= deadline:
                break
            sleep(pause)
    raise BudgetExhausted("an object's lock was not granted within the retry budget")


def statements_are_owner_changes(statements: Iterable[str]) -> list[str]:
    """Every statement that is not a lock bound or an ``ALTER ... OWNER TO`` (should be none)."""

    allowed = re.compile(r"ALTER [A-Z ]+ .+ OWNER TO \"(?:[^\"]|\"\")+\"")
    return [text for text in statements if text not in LOCK_BOUNDS and not allowed.fullmatch(text)]


__all__ = [
    "AUTHORITY_SQL",
    "DEFINER_PHASE",
    "INVENTORY_SQL",
    "KINDS",
    "LOCK_BOUNDS",
    "LOCK_TIMEOUT",
    "ORDER",
    "STATEMENT_TIMEOUT",
    "DEFAULT_BUDGET",
    "BudgetExhausted",
    "LakebaseHandoverRefused",
    "LockBudget",
    "PgObject",
    "kind_of",
    "owner_statement",
    "parse_inventory",
    "quote_ident",
    "run_bounded",
    "sort_changes",
    "statements_are_owner_changes",
]
