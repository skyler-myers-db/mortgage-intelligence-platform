"""What the last committed apply left behind: ledger versions, digests, catalog.

Apply records, in the same transaction as the schema it describes, the digest
of its inputs, one digest per input component, and a fingerprint of the
``mip_app`` catalog state those inputs produced. ``--mode plan`` reads them
back read-only. Equal inputs alone do not prove that apply would change
nothing: an owner-level actor can rewrite a reviewed function body, drop a
CHECK or a unique index, or delete seeded reference rows, and only apply's
replay of schema.sql repairs that. The fingerprint is how plan sees it.

The fingerprint reads catalog columns and stored expression trees, never
``pg_get_*def`` or ``regclass`` text, so it does not depend on the session's
search_path, DateStyle or TimeZone: apply (inside its schema transaction,
after schema.sql set its own search_path) and plan (in a fresh read-only
session) compute the same value for the same state. It leaves out what apply
does not own or what changes at runtime: ownership (the executor probe
checks it), ACLs (verify checks them exactly), sequence positions,
statistics, and the rows of every table the App may write.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from jobs.lakebase_migration_inputs import (
    SCHEMA_DIGEST_SCOPE,
    SCHEMA_INPUT_COMPONENTS,
    schema_input_components,
    schema_input_digest,
    validated_digest,
)
from jobs.lakebase_migration_read_only import ReadOnlyCursor

# Reference tables whose rows only apply writes (seed and schema texts); the
# App has SELECT and nothing else on each, pinned by a unit test. Their
# timestamps are left out: apply restamps the tenant seed on every run.
SEEDED_REFERENCE_TABLES = (
    "activation_destinations",
    "loan_officers",
    "sales_team",
    "tenant_disclosures",
)

# One line per catalog fact, hashed in byte order. Each branch is labelled,
# and %L quotes every value (NULL stays NULL), so no two facts collide.
CATALOG_FINGERPRINT_SQL = """
SELECT encode(
    sha256(convert_to(
        COALESCE(string_agg(entry.line, chr(10) ORDER BY entry.line COLLATE "C"), ''),
        'UTF8'
    )),
    'hex'
)
FROM (
    SELECT format(
        'relation|%L|%L|%L|%L|%L|%L|%L',
        relation.relname, relation.relkind, relation.relpersistence,
        relation.relrowsecurity, relation.relforcerowsecurity, relation.reloptions,
        relation.relispartition
    )
    FROM pg_class relation
    JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = 'mip_app'
    UNION ALL
    SELECT format(
        'column|%L|%L|%L|%L|%L|%L|%L|%L|%L',
        relation.relname, attribute.attname, attribute.atttypid, attribute.atttypmod,
        attribute.attnotnull, attribute.attidentity, attribute.attgenerated,
        attribute.attcollation, column_default.adbin
    )
    FROM pg_attribute attribute
    JOIN pg_class relation ON relation.oid = attribute.attrelid
    JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
    LEFT JOIN pg_attrdef column_default
      ON column_default.adrelid = attribute.attrelid
     AND column_default.adnum = attribute.attnum
    WHERE namespace.nspname = 'mip_app'
      AND attribute.attnum > 0
      AND NOT attribute.attisdropped
    UNION ALL
    SELECT format(
        'constraint|%L|%L|%L|%L|%L|%L|%L|%L|%L|%L|%L|%L|%L|%L',
        relation.relname, constraint_object.contypid, constraint_object.conname,
        constraint_object.contype, constraint_object.condeferrable,
        constraint_object.condeferred, constraint_object.convalidated,
        constraint_object.conkey, referenced.relname, constraint_object.confkey,
        constraint_object.confupdtype, constraint_object.confdeltype,
        constraint_object.confmatchtype, constraint_object.conbin
    )
    FROM pg_constraint constraint_object
    JOIN pg_namespace namespace ON namespace.oid = constraint_object.connamespace
    LEFT JOIN pg_class relation ON relation.oid = constraint_object.conrelid
    LEFT JOIN pg_class referenced ON referenced.oid = constraint_object.confrelid
    WHERE namespace.nspname = 'mip_app'
    UNION ALL
    SELECT format(
        'index|%L|%L|%L|%L|%L|%L|%L|%L|%L|%L|%L|%L|%L|%L|%L',
        index_relation.relname, relation.relname, index_relation.relam,
        index_object.indisunique, index_object.indnullsnotdistinct,
        index_object.indisprimary, index_object.indisexclusion,
        index_object.indimmediate, index_object.indisvalid, index_object.indkey,
        index_object.indcollation, index_object.indclass, index_object.indoption,
        index_object.indexprs, index_object.indpred
    )
    FROM pg_index index_object
    JOIN pg_class index_relation ON index_relation.oid = index_object.indexrelid
    JOIN pg_class relation ON relation.oid = index_object.indrelid
    JOIN pg_namespace namespace ON namespace.oid = index_relation.relnamespace
    WHERE namespace.nspname = 'mip_app'
    UNION ALL
    SELECT format(
        'function|%L|%L|%L|%L|%L|%L|%L|%L|%L|%L|%L|%L|%L|%L|%L|%L|%L|%L|%L',
        routine.proname, routine.proargtypes, routine.prokind, routine.prolang,
        routine.prosecdef, routine.proleakproof, routine.proisstrict, routine.proretset,
        routine.provolatile, routine.proparallel, routine.prorettype,
        routine.proallargtypes, routine.proargmodes, routine.proargnames,
        routine.proargdefaults, routine.proconfig, routine.prosrc, routine.probin,
        routine.prosqlbody
    )
    FROM pg_proc routine
    JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace
    WHERE namespace.nspname = 'mip_app'
    UNION ALL
    SELECT format(
        'trigger|%L|%L|%L|%L|%L|%L|%L|%L|%L|%L|%L|%L|%L|%L',
        relation.relname, trigger_object.tgname, function_namespace.nspname,
        trigger_function.proname, trigger_function.proargtypes, trigger_object.tgtype,
        trigger_object.tgenabled, trigger_object.tgdeferrable,
        trigger_object.tginitdeferred, trigger_object.tgattr,
        encode(trigger_object.tgargs, 'hex'), trigger_object.tgqual,
        trigger_object.tgoldtable, trigger_object.tgnewtable
    )
    FROM pg_trigger trigger_object
    JOIN pg_class relation ON relation.oid = trigger_object.tgrelid
    JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
    JOIN pg_proc trigger_function ON trigger_function.oid = trigger_object.tgfoid
    JOIN pg_namespace function_namespace
      ON function_namespace.oid = trigger_function.pronamespace
    WHERE namespace.nspname = 'mip_app'
      AND NOT trigger_object.tgisinternal
    UNION ALL
    SELECT format(
        'rule|%L|%L|%L|%L|%L|%L|%L',
        relation.relname, rule_object.rulename, rule_object.ev_type,
        rule_object.ev_enabled, rule_object.is_instead, rule_object.ev_qual,
        rule_object.ev_action
    )
    FROM pg_rewrite rule_object
    JOIN pg_class relation ON relation.oid = rule_object.ev_class
    JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = 'mip_app'
    UNION ALL
    SELECT format(
        'policy|%L|%L|%L|%L|%L|%L|%L',
        relation.relname, policy.polname, policy.polcmd, policy.polpermissive,
        policy.polroles, policy.polqual, policy.polwithcheck
    )
    FROM pg_policy policy
    JOIN pg_class relation ON relation.oid = policy.polrelid
    JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = 'mip_app'
    UNION ALL
    SELECT format(
        'sequence|%L|%L|%L|%L|%L|%L|%L|%L',
        relation.relname, sequence_object.seqtypid, sequence_object.seqstart,
        sequence_object.seqincrement, sequence_object.seqmax, sequence_object.seqmin,
        sequence_object.seqcache, sequence_object.seqcycle
    )
    FROM pg_sequence sequence_object
    JOIN pg_class relation ON relation.oid = sequence_object.seqrelid
    JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = 'mip_app'
    UNION ALL
    SELECT format(
        'type|%L|%L|%L|%L|%L|%L|%L',
        type_object.typname, type_object.typtype, type_object.typbasetype,
        type_object.typtypmod, type_object.typnotnull, type_object.typdefaultbin,
        (
            SELECT string_agg(label.enumlabel, ',' ORDER BY label.enumsortorder)
            FROM pg_enum label
            WHERE label.enumtypid = type_object.oid
        )
    )
    FROM pg_type type_object
    JOIN pg_namespace namespace ON namespace.oid = type_object.typnamespace
    WHERE namespace.nspname = 'mip_app'
    UNION ALL
    SELECT format('row|activation_destinations|%L', to_jsonb(seeded) - ARRAY['created_at', 'updated_at'])
    FROM mip_app.activation_destinations seeded
    UNION ALL
    SELECT format('row|loan_officers|%L', to_jsonb(seeded) - ARRAY['created_at', 'updated_at'])
    FROM mip_app.loan_officers seeded
    UNION ALL
    SELECT format('row|sales_team|%L', to_jsonb(seeded) - ARRAY['created_at', 'updated_at'])
    FROM mip_app.sales_team seeded
    UNION ALL
    SELECT format('row|tenant_disclosures|%L', to_jsonb(seeded) - ARRAY['created_at', 'updated_at'])
    FROM mip_app.tenant_disclosures seeded
) AS entry(line)
"""

_LEDGER_PRESENCE_SQL = """
SELECT
    to_regclass('mip_app.schema_migrations') IS NOT NULL,
    to_regclass('mip_app.deployment_schema_digest') IS NOT NULL
"""
_APPLIED_VERSIONS_SQL = "SELECT version FROM mip_app.schema_migrations ORDER BY version"
_RECORDED_DIGEST_SQL = """
SELECT
    input_digest,
    schema_digest,
    seed_digest,
    tenant_disclosure_seed_digest,
    grant_matrix_digest,
    catalog_fingerprint
FROM mip_app.deployment_schema_digest
WHERE scope = %s
"""


def schema_input_digest_sql(sql_texts: Sequence[str]) -> str:
    """Render the digest-row upsert apply runs as its schema transaction's last text.

    The row commits atomically with the schema it describes, and its catalog
    fingerprint is computed inside the same statement from the state the four
    texts left: a rolled-back apply leaves the previous row in place, so plan
    sees a difference.
    """

    digests = [validated_digest(schema_input_digest(sql_texts))]
    components = schema_input_components(sql_texts)
    digests.extend(validated_digest(components[name]) for name in SCHEMA_INPUT_COMPONENTS)
    columns = ", ".join(f"{name}_digest" for name in SCHEMA_INPUT_COMPONENTS)
    values = ", ".join(f"'{digest}'" for digest in digests)
    updates = "".join(
        f"    {column} = EXCLUDED.{column},\n"
        for column in ("input_digest", *(f"{name}_digest" for name in SCHEMA_INPUT_COMPONENTS))
    )
    return f"""
INSERT INTO mip_app.deployment_schema_digest (
    scope, input_digest, {columns}, catalog_fingerprint
)
SELECT '{SCHEMA_DIGEST_SCOPE}', {values}, fingerprint.value
FROM ({CATALOG_FINGERPRINT_SQL}) AS fingerprint(value)
ON CONFLICT (scope) DO UPDATE
SET
{updates}    catalog_fingerprint = EXCLUDED.catalog_fingerprint,
    recorded_at = now();
"""


@dataclass(frozen=True)
class RecordedDigest:
    """The digest row of the last committed apply."""

    input_digest: str
    components: dict[str, str]
    catalog_fingerprint: str


def read_ledger(cur: ReadOnlyCursor) -> tuple[set[str], RecordedDigest | None]:
    """Return the applied versions and the recorded digest row; absent reads empty."""

    cur.execute(_LEDGER_PRESENCE_SQL)
    presence = cur.fetchone()
    migrations_present, digest_present = presence if presence is not None else (False, False)
    applied: set[str] = set()
    if migrations_present:
        cur.execute(_APPLIED_VERSIONS_SQL)
        applied = {str(row[0]) for row in cur.fetchall()}
    recorded: RecordedDigest | None = None
    if digest_present:
        cur.execute(_RECORDED_DIGEST_SQL, (SCHEMA_DIGEST_SCOPE,))
        row = cur.fetchone()
        if row is not None:
            input_digest, *component_digests, catalog_fingerprint = (str(value) for value in row)
            recorded = RecordedDigest(
                input_digest=input_digest,
                components=dict(zip(SCHEMA_INPUT_COMPONENTS, component_digests, strict=True)),
                catalog_fingerprint=catalog_fingerprint,
            )
    return applied, recorded


def read_catalog_fingerprint(cur: ReadOnlyCursor) -> str:
    """Recompute, read-only, the fingerprint apply recorded at its commit."""

    cur.execute(CATALOG_FINGERPRINT_SQL)
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("Lakebase catalog fingerprint returned no row")
    return str(row[0])
