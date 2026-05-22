"""Export append-only Lakebase audit rows to compressed JSONL cold storage.

This is the DR/retention helper for ``mip_app.action_audit``. It deliberately
does not delete from Lakebase: the table is governed append-only, and any
retention compaction needs a customer-approved policy change. The helper copies
older rows to a portable archive file and records the export in
``mip_app.action_audit_archive_runs`` so operators can prove what was copied.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_EXPORT_SQL = """
SELECT
    audit_id::text AS audit_id,
    event_type,
    actor_email,
    entity_type,
    entity_id,
    subject_clip,
    subject_segment,
    request_id,
    correlation_id,
    evidence_ids,
    metadata,
    event_at
FROM mip_app.action_audit
WHERE event_at < %(cutoff_event_at)s
ORDER BY event_at ASC
"""

_RECORD_ARCHIVE_SQL = """
INSERT INTO mip_app.action_audit_archive_runs (
    cutoff_event_at,
    destination_uri,
    row_count,
    requested_by,
    status,
    metadata
) VALUES (
    %(cutoff_event_at)s,
    %(destination_uri)s,
    %(row_count)s,
    %(requested_by)s,
    'completed',
    %(metadata)s::jsonb
)
"""


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return str(value)


def _parse_cutoff(value: str | None, *, days: int) -> datetime:
    if value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    return datetime.now(UTC) - timedelta(days=days)


def _default_output(cutoff: datetime) -> Path:
    stamp = cutoff.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path("artifacts") / "action_audit" / f"action_audit_before_{stamp}.jsonl.gz"


def export_action_audit(*, cutoff: datetime, output: Path, requested_by: str) -> int:
    import psycopg
    from psycopg.rows import dict_row

    from jobs.lakebase_migrate import _resolve_connection

    output.parent.mkdir(parents=True, exist_ok=True)
    conn_kwargs = _resolve_connection()
    row_count = 0

    with psycopg.connect(**conn_kwargs) as conn:
        with conn.cursor(row_factory=dict_row) as cur, gzip.open(output, "wt", encoding="utf-8") as fh:
            cur.execute(_EXPORT_SQL, {"cutoff_event_at": cutoff})
            for row in cur:
                fh.write(json.dumps(dict(row), sort_keys=True, separators=(",", ":"), default=_json_default))
                fh.write("\n")
                row_count += 1

        with conn.cursor() as cur:
            cur.execute(
                _RECORD_ARCHIVE_SQL,
                {
                    "cutoff_event_at": cutoff,
                    "destination_uri": str(output),
                    "row_count": row_count,
                    "requested_by": requested_by,
                    "metadata": json.dumps({"format": "jsonl.gz", "source": "mip_app.action_audit"}),
                },
            )
        conn.commit()

    return row_count


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    cutoff = parser.add_mutually_exclusive_group()
    cutoff.add_argument(
        "--cutoff-days",
        type=int,
        default=365,
        help="Export rows older than this many days. Default: 365.",
    )
    cutoff.add_argument(
        "--cutoff-iso",
        help="Explicit UTC cutoff timestamp, for example 2026-05-18T00:00:00Z.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Destination JSONL.GZ file. Defaults under artifacts/action_audit/.",
    )
    parser.add_argument(
        "--requested-by",
        default="system@databricks-apps",
        help="Operator identity recorded in mip_app.action_audit_archive_runs.",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    cutoff = _parse_cutoff(args.cutoff_iso, days=args.cutoff_days)
    output = args.output or _default_output(cutoff)
    row_count = export_action_audit(cutoff=cutoff, output=output, requested_by=args.requested_by)
    print(
        "[action-audit-export] "
        f"exported {row_count} rows older than {cutoff.isoformat()} to {output}"
    )


if __name__ == "__main__":
    main()
