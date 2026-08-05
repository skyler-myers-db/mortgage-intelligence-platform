"""Render SQL sources with a target Unity Catalog catalog name.

Multi-catalog zero-click deploy (R6-01 rollout).
---------------------------------------------------------------------------

The Module 0 SQL source files under ``sql/`` hardcode the default catalog
prefix ``mip.`` on every governed identifier (e.g.
``mip.gold.borrower_360``, ``mip.silver.lien_current``). A customer who
deploys this bundle with a different Unity Catalog name (``summit_mortgage``,
``cotality_mip``, ``lender_uc``) gets CTAS statements that still write to
``mip.*``, which is either a permission error or, worse, silent writes
into the wrong catalog.

This preprocessor resolves that by emitting ``sql/_rendered/**/*.sql``
alongside the canonical sources, with the governed catalog and schema
prefixes substituted for a caller-provided catalog:

    mip.gold.       -> {catalog}.gold.
    mip.silver.     -> {catalog}.silver.
    mip.ref.        -> {catalog}.ref.
    mip.semantics.  -> {catalog}.semantics.
    mip.raw.        -> {catalog}.raw.
    mip.first_party.-> {catalog}.first_party.
    mip.audit.      -> {catalog}.audit.
    mip.app.        -> {catalog}.app.

It also rewrites the exact DDL contexts ``CREATE CATALOG IF NOT EXISTS mip``
and ``CREATE SCHEMA IF NOT EXISTS mip.<governed-schema>``. Without those
two-part rewrites, a renamed-catalog deploy could create containers in the
default catalog before its three-part table DDL targets the customer catalog.

The canonical sources keep the readable ``mip.*`` identifiers so code
review + git diffs stay legible. The rendered tree is a build artifact
(.gitignore'd) that the Databricks bundle's SQL tasks read from.

The renderer also materializes the Summit Mortgage first-party demo-feed
switch because Databricks SQL does not allow runtime parameter markers inside
the ``CREATE VIEW`` / CTAS statements used by ``demo_first_party_feeds.sql``.
The stand-alone renderer is fail-closed: if no explicit value is provided, it
renders the demo feed disabled so customer/prod workspaces keep
``mip.first_party.*`` empty until real lender feeds arrive. The deploy wrapper
explicitly opts the dev Summit demo into these feeds.

Why the regex is safe (no false-positive substitutions).
---------------------------------------------------------------------------

The three-part prefixes all take the form ``mip.<schema>.`` where ``<schema>``
is a fixed enumeration of known UC schemas. Every match is anchored on a
word boundary before ``mip`` AND a literal trailing dot after the schema,
so the preprocessor rewrites ONLY three-part UC names and nothing else.
Concretely:

    * ``mip.gold.borrower_360`` -> substitutes.
    * ``mip_app.approvals``     -> no match (word boundary sees the ``_``).
    * ``#mip.raw#`` (comment)   -> substitutes (fine -- comments should
                                   reflect the target catalog too).
    * ``mipmap.gold``           -> no match (``\\bmip\\.`` requires a dot
                                   immediately after ``mip``).
    * ``mip.gold`` without a trailing dot (e.g. a prose sentence ending in
      ``mip.gold.``) -> substitutes -- same identifier rewrite the SQL
      would need anyway, and does not trigger on bare ``mip.gold`` in
      running English.

The ``mip`` Lakebase schema name (``mip_app``) does NOT appear anywhere in
sql/ sources because Lakebase is Postgres, not UC -- those identifiers
live in ``lakebase/schema.sql`` which this tool does not scan.

Idempotency.
---------------------------------------------------------------------------

Re-running with the same ``--catalog`` is a byte-stable no-op at the
output-tree level; files that already match the rendered content are
skipped rather than rewritten. Re-running with a different ``--catalog``
rewrites everything.

When ``--catalog mip`` (the default), the output is byte-identical to the
source -- identity substitution. This is the default path for customers
who keep the default catalog name.

CLI.
---------------------------------------------------------------------------

    python tools/render_sql.py                         # uses settings
    python tools/render_sql.py --catalog summit_mortgage
    python tools/render_sql.py --source sql --dest sql/_rendered

Exit status: 0 on success, 2 on arg error, 1 on I/O / permission error.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# The UC schema segments governed by this rewrite. Order does not
# matter (they are disjoint). Documented in docs/multi-catalog-plan.md.
_UC_SCHEMAS: tuple[str, ...] = (
    "gold",
    "silver",
    "ref",
    "semantics",
    "raw",
    "first_party",
    "audit",
    "app",
)

# Pre-compile one regex per prefix. Anchoring: word boundary before
# ``mip`` ensures ``mip_app`` (Lakebase schema) is never matched. The
# literal ``.<schema>.`` after ``mip`` ensures bare ``mip.something_else``
# does not match either.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"\bmip\.{schema}\."), f"{{catalog}}.{schema}.") for schema in _UC_SCHEMAS
]
_CREATE_CATALOG_PATTERN = re.compile(
    r"^(\s*CREATE\s+CATALOG\s+IF\s+NOT\s+EXISTS\s+)mip(?=\s|;|$)",
    re.IGNORECASE | re.MULTILINE,
)
_CREATE_SCHEMA_PATTERN = re.compile(
    r"^(\s*CREATE\s+SCHEMA\s+IF\s+NOT\s+EXISTS\s+)mip\."
    rf"(?P<schema>{'|'.join(re.escape(schema) for schema in _UC_SCHEMAS)})"
    r"(?=\s|;|$)",
    re.IGNORECASE | re.MULTILINE,
)
_DEMO_FIRST_PARTY_TOKEN = "{{mip_enable_demo_first_party_feeds}}"
_UC_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,254}$")

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_SOURCE = _REPO_ROOT / "sql"
_DEFAULT_DEST = _REPO_ROOT / "sql" / "_rendered"


def _resolve_default_catalog() -> str:
    """Read ``settings.mip_default_catalog`` without hard-failing on import.

    The renderer runs before the signed deploy resource phase, possibly in CI
    containers that do not install the backend's runtime requirements. If
    importing settings fails, fall back to the contractual default
    (``mip``) so the tool is usable stand-alone.
    """
    try:
        from backend.config.settings import settings  # noqa: PLC0415

        return str(settings.mip_default_catalog)
    except Exception:  # noqa: BLE001 -- deliberate broad catch, see docstring
        return "mip"


def _parse_bool(raw: str | None, *, default: bool) -> bool:
    """Parse a human/operator boolean with a conservative fallback."""
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(
        "MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS must be one of " "1/0, true/false, yes/no, on/off"
    )


def _resolve_demo_first_party_enabled(cli_value: str | None) -> bool:
    if cli_value is not None:
        return _parse_bool(cli_value, default=False)
    return _parse_bool(os.environ.get("MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS"), default=False)


def _render_text(text: str, catalog: str, *, demo_first_party_enabled: bool) -> tuple[str, int]:
    """Apply UC prefix substitutions and render-time SQL switches."""
    total = 0
    text, n = _CREATE_CATALOG_PATTERN.subn(lambda match: f"{match.group(1)}{catalog}", text)
    total += n
    text, n = _CREATE_SCHEMA_PATTERN.subn(
        lambda match: f"{match.group(1)}{catalog}.{match.group('schema').lower()}",
        text,
    )
    total += n
    for pattern, template in _PATTERNS:
        replacement = template.format(catalog=catalog)
        new_text, n = pattern.subn(replacement, text)
        total += n
        text = new_text
    demo_literal = "TRUE" if demo_first_party_enabled else "FALSE"
    text, n = re.subn(re.escape(_DEMO_FIRST_PARTY_TOKEN), demo_literal, text)
    total += n
    return text, total


def _iter_sql_files(source_root: Path, dest_root: Path) -> list[Path]:
    """Return canonical ``*.sql`` files, skipping every rendered artifact tree."""
    files: list[Path] = []
    for path in source_root.rglob("*.sql"):
        relative_path = path.relative_to(source_root)
        if "_rendered" in relative_path.parts:
            continue
        # Skip anything inside the rendered output directory to keep the
        # run idempotent when a caller chooses a differently named in-tree
        # destination.
        try:
            path.relative_to(dest_root)
            continue
        except ValueError:
            pass
        files.append(path)
    return sorted(files)


def render(
    *,
    catalog: str,
    demo_first_party_enabled: bool,
    source_root: Path = _DEFAULT_SOURCE,
    dest_root: Path = _DEFAULT_DEST,
) -> tuple[int, int, int]:
    """Render every SQL file under source_root into dest_root.

    Returns ``(files_processed, files_written, total_substitutions)``.
    A file is "written" only if its rendered content differs from what is
    already on disk at the destination path -- this keeps the operation
    cheap on repeated deploys.
    """
    if not source_root.is_dir():
        raise FileNotFoundError(f"source directory does not exist: {source_root}")

    files = _iter_sql_files(source_root, dest_root)
    files_written = 0
    total_subs = 0

    for src in files:
        rel = src.relative_to(source_root)
        dst = dest_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)

        src_text = src.read_text(encoding="utf-8")
        rendered, n = _render_text(
            src_text,
            catalog,
            demo_first_party_enabled=demo_first_party_enabled,
        )
        total_subs += n

        # Idempotent write: only touch dst when content differs.
        if dst.exists() and dst.read_text(encoding="utf-8") == rendered:
            continue
        dst.write_text(rendered, encoding="utf-8")
        files_written += 1

    return len(files), files_written, total_subs


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="render_sql",
        description=(
            "Render SQL sources with a target Unity Catalog catalog name. "
            "Writes sql/_rendered/**/*.sql mirroring sql/**/*.sql with the "
            "governed UC prefixes rewritten for the target catalog."
        ),
    )
    parser.add_argument(
        "--catalog",
        default=None,
        help=(
            "Target UC catalog name. Defaults to settings.mip_default_catalog "
            "(env var MIP_DEFAULT_CATALOG), falling back to 'mip' if the "
            "backend settings module is unavailable."
        ),
    )
    parser.add_argument(
        "--source",
        default=str(_DEFAULT_SOURCE),
        help="Source SQL tree root (default: sql/).",
    )
    parser.add_argument(
        "--dest",
        default=str(_DEFAULT_DEST),
        help="Destination tree root (default: sql/_rendered/).",
    )
    parser.add_argument(
        "--enable-demo-first-party-feeds",
        default=None,
        help=(
            "Render demo_first_party_feeds.sql with TRUE/FALSE. Defaults to "
            "MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS, falling back to false. "
            "The deploy wrapper explicitly enables this for the Summit dev demo."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    catalog = args.catalog or _resolve_default_catalog()
    try:
        demo_first_party_enabled = _resolve_demo_first_party_enabled(
            args.enable_demo_first_party_feeds
        )
    except ValueError as exc:
        print(f"render_sql: {exc}", file=sys.stderr)
        return 2

    # Match bundle_env and the pre-bundle namespace bootstrap exactly so an
    # unsafe or divergent identifier never reaches rendered executable SQL.
    if _UC_IDENTIFIER.fullmatch(catalog.strip()) is None:
        print(
            "render_sql: --catalog must be a lowercase unquoted UC identifier",
            file=sys.stderr,
        )
        return 2
    catalog = catalog.strip()

    source_root = Path(args.source).resolve()
    dest_root = Path(args.dest).resolve()

    try:
        processed, written, subs = render(
            catalog=catalog,
            demo_first_party_enabled=demo_first_party_enabled,
            source_root=source_root,
            dest_root=dest_root,
        )
    except FileNotFoundError as exc:
        print(f"render_sql: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"render_sql: I/O error: {exc}", file=sys.stderr)
        return 1

    print(
        f"render_sql: catalog={catalog} processed={processed} "
        f"written={written} substitutions={subs} "
        f"demo_first_party_feeds={'enabled' if demo_first_party_enabled else 'disabled'} "
        f"source={source_root.relative_to(_REPO_ROOT) if source_root.is_relative_to(_REPO_ROOT) else source_root} "
        f"dest={dest_root.relative_to(_REPO_ROOT) if dest_root.is_relative_to(_REPO_ROOT) else dest_root}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
