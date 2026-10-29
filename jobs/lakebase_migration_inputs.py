"""Migration inputs: versions, compatibility tags, and the schema-input digest.

Everything here is pure: it reads the repository-authored SQL texts and the
code-owned grant matrix, never a database. ``--mode apply`` writes the digest
of exactly the texts it executes; ``--mode plan`` recomputes it from the same
texts and compares it with the row the last committed apply left behind.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from jobs.lakebase_migration_contracts import (
    _AI_GATEWAY_VERIFIER_TABLE_PRIVILEGES,
    _APP_ROLE_ROUTINE_PRIVILEGES,
    _APP_ROLE_SEQUENCE_PRIVILEGES,
    _APP_ROLE_TABLE_PRIVILEGES,
)
from jobs.lakebase_migration_integrity import _split_schema_sql
from jobs.lakebase_migration_seed import (
    _preflight_seed_schema_parity,
    _tenant_disclosure_seed_sql,
)

MigrationCompat = Literal["expand", "contract"]

SCHEMA_DIGEST_SCOPE = "lakebase_schema"
_DIGEST_DOMAIN = b"mip-lakebase-schema-inputs-v1"
# The four texts apply executes, in order. The digest frames each one by label
# and byte length, so moving bytes from one text to another changes it.
_SQL_TEXT_LABELS = ("schema_pre_seed", "seed", "tenant_disclosure_seed", "schema_post_seed")
_SHA256_HEX_RE = re.compile(r"[0-9a-f]{64}")

_MIGRATION_INSERT_MARKER = "INSERT INTO mip_app.schema_migrations"
# The one reviewed ledger-insert shape. Every marker occurrence must match it
# in full, so a multi-row or reformatted insert cannot hide a version.
_MIGRATION_INSERT_RE = re.compile(
    r"INSERT INTO mip_app\.schema_migrations \(version, description\)\n"
    r"\s*VALUES \(\n"
    r"\s*'(?P<version>[^'\n]+)',\n"
    r"\s*'(?:[^'\n]|'')*'\n"
    r"\s*\)(?:\nON CONFLICT \(version\) DO NOTHING)?;"
)
_VERSION_RE = re.compile(r"\d{4}_\d{2}_\d{2}_[a-z0-9_]+")
# Any comment whose text starts with the tag token is a tag line, and must
# then match the strict grammar: a typo refuses instead of silently counting
# as an untagged (contract) migration.
_TAG_LINE_RE = re.compile(r"^[ \t]*--[ \t]*mip:migration\b.*$", re.MULTILINE)
_TAG_RE = re.compile(
    r"-- mip:migration version=(?P<version>[0-9a-z_]+) compat=(?P<compat>expand|contract)"
)


class MigrationInputError(RuntimeError):
    """The repository migration inputs cannot be read unambiguously."""


@dataclass(frozen=True)
class SchemaMigration:
    """One ``mip_app.schema_migrations`` version declared by schema.sql."""

    version: str
    compat: MigrationCompat
    tagged: bool


def parse_schema_migrations(schema_sql: str) -> tuple[SchemaMigration, ...]:
    """Return every ledger version in file order with its compatibility class.

    ``compat=expand`` claims that the previous release's App keeps working on
    the migrated schema. A version without a tag is ``contract``: the
    conservative default, so a missing review never admits a restart of the
    old App on a schema it may not understand.
    """

    matches = list(_MIGRATION_INSERT_RE.finditer(schema_sql))
    marker_count = schema_sql.count(_MIGRATION_INSERT_MARKER)
    if len(matches) != marker_count:
        raise MigrationInputError(
            "lakebase/schema.sql has a schema_migrations insert outside the reviewed "
            f"single-row shape: parsed={len(matches)}, inserts={marker_count}"
        )
    versions: list[str] = []
    for match in matches:
        version = match.group("version")
        if not _VERSION_RE.fullmatch(version):
            raise MigrationInputError(f"schema_migrations version {version!r} is malformed")
        if version in versions:
            raise MigrationInputError(f"schema_migrations version {version!r} is declared twice")
        versions.append(version)

    tags: dict[str, MigrationCompat] = {}
    for line_match in _TAG_LINE_RE.finditer(schema_sql):
        line = line_match.group(0)
        tag = _TAG_RE.fullmatch(line)
        if tag is None:
            raise MigrationInputError(f"malformed migration compatibility tag: {line.strip()!r}")
        version = tag.group("version")
        if version not in versions:
            raise MigrationInputError(f"compatibility tag names no declared version: {version!r}")
        if version in tags:
            raise MigrationInputError(f"version {version!r} carries two compatibility tags")
        tags[version] = "expand" if tag.group("compat") == "expand" else "contract"

    return tuple(
        SchemaMigration(
            version=version,
            compat=tags.get(version, "contract"),
            tagged=version in tags,
        )
        for version in versions
    )


def grant_matrix_document() -> str:
    """Canonical JSON of the code-owned runtime privilege matrix."""

    return json.dumps(
        {
            "app_role_routine_privileges": {
                f"{name}({arguments})": list(privileges)
                for (name, arguments), privileges in sorted(_APP_ROLE_ROUTINE_PRIVILEGES.items())
            },
            "app_role_sequence_privileges": {
                sequence: list(privileges)
                for sequence, privileges in sorted(_APP_ROLE_SEQUENCE_PRIVILEGES.items())
            },
            "app_role_table_privileges": {
                table: list(privileges)
                for table, privileges in sorted(_APP_ROLE_TABLE_PRIVILEGES.items())
            },
            "ai_gateway_verifier_table_privileges": list(_AI_GATEWAY_VERIFIER_TABLE_PRIVILEGES),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def schema_input_digest(sql_texts: Sequence[str]) -> str:
    """sha256 over the four texts apply executes plus the grant matrix."""

    if len(sql_texts) != len(_SQL_TEXT_LABELS):
        raise MigrationInputError(
            f"schema-input digest needs {len(_SQL_TEXT_LABELS)} SQL texts, got {len(sql_texts)}"
        )
    digest = hashlib.sha256(_DIGEST_DOMAIN + b"\0")
    framed = (
        *zip(_SQL_TEXT_LABELS, sql_texts, strict=True),
        ("grant_matrix", grant_matrix_document()),
    )
    for label, text in framed:
        data = text.encode("utf-8")
        digest.update(label.encode("ascii") + b"\0" + str(len(data)).encode("ascii") + b"\0")
        digest.update(data)
    return digest.hexdigest()


def schema_input_digest_sql(digest: str) -> str:
    """Render the digest-row upsert apply runs inside its schema transaction.

    The row commits atomically with the schema it describes: a rolled-back
    apply leaves the previous digest in place, so plan sees a difference.
    """

    if not _SHA256_HEX_RE.fullmatch(digest):
        raise MigrationInputError("schema-input digest must be 64 lowercase hex characters")
    return f"""
INSERT INTO mip_app.deployment_schema_digest (scope, input_digest)
VALUES ('{SCHEMA_DIGEST_SCOPE}', '{digest}')
ON CONFLICT (scope) DO UPDATE
SET input_digest = EXCLUDED.input_digest,
    recorded_at = now();
"""


def render_schema_inputs(
    repo_root: Path,
    *,
    lender_name: str,
    lender_nmls_id: str,
    tenant_id: str,
) -> tuple[str, tuple[str, str, str, str]]:
    """Return schema.sql and the four texts apply executes, read-only.

    The same rendering steps, in the same order, as ``lakebase_migrate.main``
    (pinned by a unit test), so plan digests exactly what apply records.
    """

    schema_sql = (repo_root / "lakebase" / "schema.sql").read_text(encoding="utf-8")
    seed_sql = (repo_root / "lakebase" / "seed_campaigns.sql").read_text(encoding="utf-8")
    _preflight_seed_schema_parity(schema_sql, seed_sql)
    tenant_disclosure_seed_sql = _tenant_disclosure_seed_sql(
        lender_name=lender_name,
        lender_nmls_id=lender_nmls_id,
        tenant_id=tenant_id,
    )
    pre_seed_schema_sql, post_seed_schema_sql = _split_schema_sql(schema_sql)
    return schema_sql, (
        pre_seed_schema_sql,
        seed_sql,
        tenant_disclosure_seed_sql,
        post_seed_schema_sql,
    )
