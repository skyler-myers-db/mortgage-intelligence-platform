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

One more closed shape carries the catalog as a NAME rather than a prefix: an
equality filter of a ``*_catalog`` column against the exact literal ``'mip'``
(``source_table_catalog = 'mip'``), which a view over a system table uses to
keep only this install's rows (``sql/ddl/audit_asset_lineage_view.sql``).
Only ``=`` against exactly ``'mip'`` is rewritten: ``'mip_app'``, other
operators and string literals anywhere else are left alone.

The Cotality source share.
---------------------------------------------------------------------------

The sources name the Cotality share by its canonical names
(``backend/config/source_names.py``): catalog ``cotality_mortgage_data``,
schema ``corelogic``, tables ``entrada_eval_<stem>``. An install whose share
is mounted under other names (``MIP_COTALITY_SOURCE_CATALOG``, ``_SCHEMA``,
``_TABLE_PREFIX``) renders them through a CLOSED-SHAPE rewrite: only the exact
lowercase canonical forms below are rewritten, in one pass, and never an
identifier that merely resembles one.

    cotality_mortgage_data.corelogic.entrada_eval_<rest>
                                   -> {catalog}.{schema}.{prefix}<rest>
    cotality_mortgage_data.corelogic  (no identifier character after it)
                                   -> {catalog}.{schema}
    <column>_catalog = 'cotality_mortgage_data'
                                   -> <column>_catalog = '{catalog}'
    entrada_eval_<rest>  (unqualified: comments and COMMENT text)
                                   -> {prefix}<rest>

A form outside that set is not guessed at. Instead, a non-canonical render is
checked after the rewrite: any canonical token left for a name that changed
(any case, at identifier boundaries) refuses the whole render before a file
is written, naming the file and line. The one exemption is the vendor's
former brand, ``CoreLogic``, on a reviewed prose line: the whole line must
equal one in ``_VENDOR_BRAND_PROSE_LINES``, and on that line the brand is
inside a comment or a string literal in every lexical state. Anywhere else,
``CoreLogic`` is the schema token (an unquoted identifier resolves case-
insensitively) and refuses. The canonical source is not rewritten at all, so
its render is byte-identical to what it was before the rewrite existed.

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

The Cotality source names come from ``MIP_COTALITY_SOURCE_*`` in the
environment (canonical when unset); scripts/deploy.sh exports the resolved
values.

Exit status: 0 on success, 2 on arg error or a canonical source token left
by a non-canonical render, 1 on I/O / permission error.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.config import source_names  # noqa: E402
from backend.config.source_names import CotalitySource  # noqa: E402

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
# ``<column>_catalog = 'mip'``: the catalog as a name in a system-table filter.
_CATALOG_LITERAL_PATTERN = re.compile(r"(\b[a-z_]+_catalog\s*=\s*)'mip'", re.IGNORECASE)
_DEMO_FIRST_PARTY_TOKEN = "{{mip_enable_demo_first_party_feeds}}"
# The install catalog's managed storage (the client profile's
# MIP_UC_CATALOG_STORAGE_ROOT, M1-PR3): the profile's shape, re-checked here so
# no quote or space can reach the rendered DDL literal.
_STORAGE_ROOT = re.compile(r"(?:s3|abfss|gs)://[^\s'\"<>\\]+")


def _storage_root(value: str | None) -> str | None:
    """``value`` as a storage root for MANAGED LOCATION; ``None`` for unset or blank."""

    root = (value or "").strip()
    if not root:
        return None
    if _STORAGE_ROOT.fullmatch(root) is None:
        raise ValueError("catalog storage root must be an s3://, abfss:// or gs:// root with no quote or space")
    return root
_UC_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,254}$")

# The Cotality source's closed shapes (module docstring), one alternation so no
# rule rescans another's output. A left boundary excludes ``.`` as well as
# identifier characters, so a canonical name inside a longer dotted name is
# not a match and is left for the post-render check to refuse.
_SRC_CATALOG = re.escape(source_names.CANONICAL.catalog)
_SRC_SCHEMA = re.escape(source_names.CANONICAL.schema)
_SRC_PREFIX = re.escape(source_names.CANONICAL.table_prefix)
_SOURCE_SHAPES = re.compile(
    rf"(?<![A-Za-z0-9_.])(?P<table>{_SRC_CATALOG}\.{_SRC_SCHEMA}\.{_SRC_PREFIX})"
    rf"|(?<![A-Za-z0-9_.])(?P<schema>{_SRC_CATALOG}\.{_SRC_SCHEMA})(?![A-Za-z0-9_])"
    rf"|(?P<column>(?i:\b[a-z_]+_catalog)\s*=\s*)'{_SRC_CATALOG}'"
    rf"|(?<![A-Za-z0-9_.])(?P<bare>{_SRC_PREFIX})"
)
# The post-render check: each canonical name as a token in any case. The
# prefix is a token at its start only, since a table stem follows it.
_CANONICAL_SOURCE_TOKENS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("catalog", re.compile(rf"(?i)(?<![A-Za-z0-9_]){_SRC_CATALOG}(?![A-Za-z0-9_])")),
    ("schema", re.compile(rf"(?i)(?<![A-Za-z0-9_]){_SRC_SCHEMA}(?![A-Za-z0-9_])")),
    ("table_prefix", re.compile(rf"(?i)(?<![A-Za-z0-9_]){_SRC_PREFIX}")),
)
# Prose, not a name: the vendor's former brand on a reviewed line. The match
# must be spelled exactly so AND its whole line (ended by ``\n`` or ``\r``, as
# the Spark SQL lexer ends a ``--`` comment) must be one of these. Each line is
# reviewed so that the brand is never code in any lexical state the line can
# start in: a ``--`` comment from code; string text from inside a ``'`` string
# (its quotes pair up before the brand); and no ``"``, backtick or ``*/`` lets
# a double-quoted string, a quoted identifier or a bracketed comment end on it.
# A prose spelling anywhere else refuses, rather than guessing at prose.
_VENDOR_BRAND = "CoreLogic"
_VENDOR_BRAND_PROSE_LINES = frozenset(
    {
        # sql/ddl/silver_property_master.sql
        "--            owner_occupancy_code    'O'/'A'/'T' per CoreLogic dictionary.",
    }
)
_LINE_BREAKS = re.compile(r"[\r\n]")


class CanonicalSourceLeft(ValueError):
    """A non-canonical render still carries a canonical Cotality source token."""


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


def _is_vendor_brand(text: str, match: re.Match[str]) -> bool:
    """The brand on a reviewed prose line (``_VENDOR_BRAND_PROSE_LINES``)."""

    if match.group(0) != _VENDOR_BRAND:
        return False
    start = max(text.rfind("\n", 0, match.start()), text.rfind("\r", 0, match.start())) + 1
    end = _LINE_BREAKS.search(text, match.end())
    return text[start : end.start() if end else len(text)] in _VENDOR_BRAND_PROSE_LINES


def canonical_source_tokens(text: str, cotality: CotalitySource) -> list[tuple[str, int]]:
    """``(name, line)`` for each canonical token left for a name ``cotality`` changes."""

    found: list[tuple[str, int]] = []
    for name, pattern in _CANONICAL_SOURCE_TOKENS:
        if getattr(cotality, name) == getattr(source_names.CANONICAL, name):
            continue
        for match in pattern.finditer(text):
            if name == "schema" and _is_vendor_brand(text, match):
                continue
            found.append((name, text.count("\n", 0, match.start()) + 1))
    return sorted(found, key=lambda item: item[1])


def _require_rewritable(cotality: CotalitySource) -> None:
    """A configured name that itself holds a canonical token could never be proven."""

    configured = " ".join((cotality.catalog, cotality.schema, f"{cotality.table_prefix}x"))
    names = sorted({name for name, _ in canonical_source_tokens(configured, cotality)})
    if names:
        raise CanonicalSourceLeft(
            "a configured Cotality source name contains the canonical "
            f"{', '.join(names)}; choose names the render can tell apart from it"
        )


def _rewrite_cotality_source(text: str, cotality: CotalitySource) -> tuple[str, int]:
    """Apply the closed-shape rewrite (identity for the canonical source)."""

    def replace(match: re.Match[str]) -> str:
        if match.group("table") is not None:
            return f"{cotality.catalog}.{cotality.schema}.{cotality.table_prefix}"
        if match.group("schema") is not None:
            return f"{cotality.catalog}.{cotality.schema}"
        if match.group("column") is not None:
            return f"{match.group('column')}'{cotality.catalog}'"
        return cotality.table_prefix

    return _SOURCE_SHAPES.subn(replace, text)


def render_cotality_source(text: str, cotality: CotalitySource, *, where: str) -> tuple[str, int]:
    """Rewrite the Cotality source in ``text`` and prove no canonical token is left.

    The canonical source returns ``text`` unchanged. ``where`` names the text
    in a refusal (a file path, or a spec name).
    """

    if cotality.is_canonical:
        return text, 0
    _require_rewritable(cotality)
    rendered, count = _rewrite_cotality_source(text, cotality)
    left = canonical_source_tokens(rendered, cotality)
    if left:
        lines = ", ".join(f"{name} at line {line}" for name, line in left)
        raise CanonicalSourceLeft(
            f"{where}: canonical Cotality source token left after the rewrite ({lines}); "
            "write the reference in one of the closed shapes tools/render_sql.py rewrites"
        )
    return rendered, count


def _render_text(
    text: str,
    catalog: str,
    *,
    demo_first_party_enabled: bool,
    cotality: CotalitySource = source_names.CANONICAL,
    where: str = "<text>",
    storage_root: str | None = None,
) -> tuple[str, int]:
    """Apply UC prefix substitutions and render-time SQL switches.

    With ``storage_root`` the catalog DDL creates the catalog with that
    ``MANAGED LOCATION``; without it the DDL is byte-identical to before.
    """
    total = 0
    location = "" if storage_root is None else f"\nMANAGED LOCATION '{storage_root}'"
    text, n = _CREATE_CATALOG_PATTERN.subn(lambda match: f"{match.group(1)}{catalog}{location}", text)
    total += n
    text, n = _CREATE_SCHEMA_PATTERN.subn(
        lambda match: f"{match.group(1)}{catalog}.{match.group('schema').lower()}",
        text,
    )
    total += n
    text, n = _CATALOG_LITERAL_PATTERN.subn(lambda match: f"{match.group(1)}'{catalog}'", text)
    total += n
    for pattern, template in _PATTERNS:
        replacement = template.format(catalog=catalog)
        new_text, n = pattern.subn(replacement, text)
        total += n
        text = new_text
    demo_literal = "TRUE" if demo_first_party_enabled else "FALSE"
    text, n = re.subn(re.escape(_DEMO_FIRST_PARTY_TOKEN), demo_literal, text)
    total += n
    # Last, so the source names are emitted verbatim: no install-catalog rule
    # runs over them.
    text, n = render_cotality_source(text, cotality, where=where)
    total += n
    return text, total


def render_for_catalog(
    text: str,
    *,
    catalog: str,
    cotality: CotalitySource = source_names.CANONICAL,
) -> str:
    """One canonical SQL source rendered for ``catalog``, demo feeds disabled.

    For deploy helpers that execute a single reviewed file through the
    statement API instead of a bundle SQL task; the catalog is held to the
    same lowercase unquoted identifier rule as the CLI.
    """
    if _UC_IDENTIFIER.fullmatch(catalog) is None:
        raise ValueError("catalog must be a lowercase unquoted UC identifier")
    return _render_text(text, catalog, demo_first_party_enabled=False, cotality=cotality)[0]


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
    cotality: CotalitySource = source_names.CANONICAL,
    storage_root: str | None = None,
) -> tuple[int, int, int]:
    """Render every SQL file under source_root into dest_root.

    ``storage_root`` (the profile's MIP_UC_CATALOG_STORAGE_ROOT) renders the
    catalog DDL with that ``MANAGED LOCATION``; unset or blank changes nothing.

    Returns ``(files_processed, files_written, total_substitutions)``.
    A file is "written" only if its rendered content differs from what is
    already on disk at the destination path -- this keeps the operation
    cheap on repeated deploys. Every file is rendered (and, for a
    non-canonical Cotality source, checked) before the first write, so a
    refusal leaves the destination tree as it was.
    """
    if not source_root.is_dir():
        raise FileNotFoundError(f"source directory does not exist: {source_root}")
    root = _storage_root(storage_root)

    files = _iter_sql_files(source_root, dest_root)
    files_written = 0
    total_subs = 0
    outputs: list[tuple[Path, str]] = []

    for src in files:
        rel = src.relative_to(source_root)
        src_text = src.read_text(encoding="utf-8")
        rendered, n = _render_text(
            src_text,
            catalog,
            demo_first_party_enabled=demo_first_party_enabled,
            cotality=cotality,
            where=str(rel),
            storage_root=root,
        )
        total_subs += n
        outputs.append((dest_root / rel, rendered))

    for dst, rendered in outputs:
        dst.parent.mkdir(parents=True, exist_ok=True)
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
    parser.add_argument(
        "--storage-root-env",
        default=None,
        metavar="NAME",
        help=(
            "Name of the environment variable that holds the install catalog's "
            "storage root (the client profile's MIP_UC_CATALOG_STORAGE_ROOT); the "
            "catalog DDL then creates it with that MANAGED LOCATION. Named, never "
            "passed, so the value stays out of the printed command. Unset or blank "
            "changes nothing."
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
    try:
        cotality = source_names.from_env(os.environ)
        storage_root = _storage_root(os.environ.get(args.storage_root_env) if args.storage_root_env else None)
    except ValueError as exc:
        print(f"render_sql: {exc}", file=sys.stderr)
        return 2

    source_root = Path(args.source).resolve()
    dest_root = Path(args.dest).resolve()

    try:
        processed, written, subs = render(
            catalog=catalog,
            demo_first_party_enabled=demo_first_party_enabled,
            source_root=source_root,
            dest_root=dest_root,
            cotality=cotality,
            storage_root=storage_root,
        )
    except CanonicalSourceLeft as exc:
        print(f"render_sql: {exc}", file=sys.stderr)
        return 2
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
        f"cotality_source={cotality.describe()} "
        f"source={source_root.relative_to(_REPO_ROOT) if source_root.is_relative_to(_REPO_ROOT) else source_root} "
        f"dest={dest_root.relative_to(_REPO_ROOT) if dest_root.is_relative_to(_REPO_ROOT) else dest_root}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
