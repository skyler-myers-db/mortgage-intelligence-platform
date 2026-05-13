"""Sync Lakebase approvals + outreach state into `mip.gold.borrower_lifecycle_state`.

Runs as a Databricks Jobs Spark-Python task (``mip_sync_lifecycle_state``
in ``databricks.yml``). Reads the authoritative approval + outreach state
from Lakebase (``mip_app.approvals`` + ``mip_app.action_audit`` filtered
to outreach events) and writes a keyed-by-borrower_id mirror into
``mip.gold.borrower_lifecycle_state`` so UC metric views can expose
``approval_rate`` and ``outreach_rate`` without a runtime federated join.

Authority model (non-negotiable):
    Lakebase is authoritative for approval + outreach state. This job is a
    one-directional mirror. It NEVER writes back to Lakebase.

Run cadence:
    Hourly in the bundle. Also runnable on-demand via
    ``databricks bundle run mip_sync_lifecycle_state``. Idempotent — the
    job rewrites the gold table via CREATE OR REPLACE on every run. At
    Module 0 scale (~10k rows) a full rewrite is cheaper than a MERGE.

Auth model (identical to jobs/lakebase_migrate.py):
    On Databricks the task runs under the workspace identity. We fetch a
    short-lived Postgres credential via
    ``POST /api/2.0/database/credentials`` and use the identity's
    ``user_name`` as the Postgres user. ``LAKEBASE_*`` env-var overrides
    are honored for local development.

Exit codes:
    0 -- sync complete.
    2 -- psycopg / Postgres error.
    3 -- SDK / auth error resolving connection parameters.
    4 -- Spark session not available (not running on Databricks or no
         session bound). The DDL in sql/ddl/003_gold_tables.sql §7 provides
         an empty fallback row via the sql/transformations/
         gold_borrower_lifecycle_state.sql script for this case.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


def _resolve_connection() -> dict:
    """Return psycopg.connect kwargs. Mirrors jobs/lakebase_migrate.py."""
    instance_name = os.environ.get("LAKEBASE_INSTANCE_NAME", "mip-app-state")
    host = os.environ.get("LAKEBASE_HOST")
    user = os.environ.get("LAKEBASE_USER")
    password = os.environ.get("LAKEBASE_PASSWORD")

    if host and user and password:
        return {
            "host": host,
            "port": int(os.environ.get("LAKEBASE_PORT", "5432")),
            "dbname": os.environ.get("LAKEBASE_DATABASE", "mip_app_state"),
            "user": user,
            "password": password,
            "sslmode": os.environ.get("LAKEBASE_SSLMODE", "require"),
        }

    try:
        from databricks.sdk import WorkspaceClient
    except ImportError:
        print(
            "[sync-lifecycle] databricks-sdk is not installed; install it or "
            "set LAKEBASE_* env vars explicitly.",
            file=sys.stderr,
        )
        sys.exit(3)

    try:
        w = WorkspaceClient()
        me = w.current_user.me()
        identity = me.user_name or me.display_name
        if not identity:
            print(
                "[sync-lifecycle] could not resolve workspace identity.",
                file=sys.stderr,
            )
            sys.exit(3)

        inst = w.api_client.do("GET", f"/api/2.0/database/instances/{instance_name}")
        resolved_host = host or inst.get("read_write_dns")
        if not resolved_host:
            print(
                f"[sync-lifecycle] instance {instance_name!r} has no "
                f"read_write_dns; check provisioning state.",
                file=sys.stderr,
            )
            sys.exit(3)

        cred = w.api_client.do(
            "POST",
            "/api/2.0/database/credentials",
            body={
                "request_id": (
                    f"mip-sync-lifecycle-"
                    f"{os.environ.get('DATABRICKS_JOB_RUN_ID','local')}"
                ),
                "instance_names": [instance_name],
            },
        )
        cred_token = cred.get("token")
        if not cred_token:
            print(
                f"[sync-lifecycle] credential response missing token: {cred}",
                file=sys.stderr,
            )
            sys.exit(3)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 -- operator-facing
        print(f"[sync-lifecycle] SDK auth/resolution failed: {exc}", file=sys.stderr)
        sys.exit(3)

    return {
        "host": resolved_host,
        "port": int(os.environ.get("LAKEBASE_PORT", "5432")),
        "dbname": os.environ.get("LAKEBASE_DATABASE", "mip_app_state"),
        "user": user or identity,
        "password": password or cred_token,
        "sslmode": os.environ.get("LAKEBASE_SSLMODE", "require"),
    }


# Latest approval per borrower, plus a flag for whether an outreach event
# has been recorded in the append-only action_audit ledger. outreach_status
# is 'actioned' when any event with event_type starting with 'OUTREACH_' has
# fired for that borrower, 'queued' when an approve exists but no outreach
# yet, and 'none' otherwise. action_audit.subject_clip is a hash of the
# CLIP -- outreach_actions isn't a dedicated table, so we key on
# approvals.borrower_id and correlate against action_audit.entity_id which
# the router writes as the borrower_id for outreach events.
_LAKEBASE_QUERY = """
WITH latest_approvals AS (
    SELECT DISTINCT ON (a.borrower_id)
        a.borrower_id,
        a.action,
        a.offer_code,
        a.decided_at
    FROM mip_app.approvals a
    ORDER BY a.borrower_id, a.decided_at DESC
),
latest_outreach AS (
    SELECT
        entity_id AS borrower_id,
        MAX(event_at) AS outreach_at
    FROM mip_app.action_audit
    WHERE event_type LIKE 'OUTREACH_%'
      AND entity_type = 'borrower'
      AND entity_id IS NOT NULL
      AND entity_id <> ''
    GROUP BY entity_id
)
SELECT
    COALESCE(a.borrower_id, o.borrower_id)              AS borrower_id,
    CASE
        WHEN a.action = 'approve' THEN 'approved'
        WHEN a.action = 'reject'  THEN 'rejected'
        WHEN a.action = 'hold'    THEN 'hold'
        ELSE 'pending'
    END                                                  AS approval_status,
    CASE
        WHEN o.outreach_at IS NOT NULL          THEN 'actioned'
        WHEN a.action = 'approve'               THEN 'queued'
        ELSE 'none'
    END                                                  AS outreach_status,
    a.offer_code                                         AS offer_code,
    CASE WHEN a.action = 'approve' THEN a.decided_at
         ELSE NULL
    END                                                  AS approved_at,
    o.outreach_at                                        AS outreach_at
FROM latest_approvals a
FULL OUTER JOIN latest_outreach o USING (borrower_id)
"""


def _fetch_lakebase_rows(conn_kwargs: dict) -> list[dict[str, Any]]:
    import psycopg

    with psycopg.connect(**conn_kwargs) as conn, conn.cursor() as cur:
        cur.execute(_LAKEBASE_QUERY)
        cols = [d.name for d in cur.description or ()]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]


def _get_spark() -> Any:
    """Return the active SparkSession. Exits 4 when unavailable."""
    try:
        from pyspark.sql import SparkSession  # type: ignore[import-not-found]
    except ImportError:
        print("[sync-lifecycle] pyspark unavailable; must run on Databricks.", file=sys.stderr)
        sys.exit(4)
    spark = SparkSession.getActiveSession() or SparkSession.builder.getOrCreate()
    if spark is None:
        print("[sync-lifecycle] no active SparkSession.", file=sys.stderr)
        sys.exit(4)
    return spark


def _write_gold(rows: list[dict[str, Any]]) -> None:
    """Write the rows into mip.gold.borrower_lifecycle_state.

    Full rewrite is acceptable at Module 0 scale (~10k borrowers). The table
    must pre-exist (created by sql/ddl/003_gold_tables.sql §7). A missing
    borrower in Lakebase resolves to the schema default via the metric-view
    LEFT JOIN COALESCE — so we union against borrower_360 here so every
    known borrower gets a row (pending / none) even if Lakebase has no
    record for them yet.
    """
    spark = _get_spark()
    # Local imports: pyspark isn't available off-Databricks; keep import out of
    # module scope so the file remains importable by lint/tests.
    from pyspark.sql import Row  # type: ignore[import-not-found]

    # Build the Lakebase-side DataFrame.
    lakebase_rows = [
        Row(
            borrower_id=str(r["borrower_id"]),
            approval_status=str(r["approval_status"]),
            outreach_status=str(r["outreach_status"]),
            offer_code=(str(r["offer_code"]) if r.get("offer_code") else None),
            approved_at=r.get("approved_at"),
            outreach_at=r.get("outreach_at"),
        )
        for r in rows
        if r.get("borrower_id")
    ]
    # Always pass an explicit schema: Spark Connect cannot infer types when
    # rows contain None values (offer_code/approved_at/outreach_at are
    # optional), which surfaces as CANNOT_DETERMINE_TYPE. The schema string
    # is the same for empty-input and populated-input cases.
    _LIFECYCLE_SCHEMA = (
        "borrower_id STRING, approval_status STRING, outreach_status STRING, "
        "offer_code STRING, approved_at TIMESTAMP, outreach_at TIMESTAMP"
    )
    lb_df = spark.createDataFrame(lakebase_rows, schema=_LIFECYCLE_SCHEMA)
    lb_df.createOrReplaceTempView("_mip_lifecycle_lakebase")

    # Only mirror decisions that reconcile to the current gold borrower
    # population. Lakebase can contain historical sandbox probes or stale
    # deleted-population rows; those stay in Lakebase/audit history but must
    # not create phantom borrowers in the gold lifecycle table.
    spark.sql("""
        CREATE OR REPLACE TEMP VIEW _mip_lifecycle_valid AS
        SELECT l.*
        FROM _mip_lifecycle_lakebase AS l
        INNER JOIN mip.gold.borrower_360 AS b
          ON b.borrower_id = l.borrower_id
    """)

    # Seed every known borrower to the default. A LEFT ANTI against the
    # valid Lakebase rows guarantees no duplicate.
    spark.sql("""
        CREATE OR REPLACE TABLE mip.gold.borrower_lifecycle_state AS
        WITH sync_anchor AS (
          SELECT CURRENT_TIMESTAMP() AS mirror_refreshed_at
        )
        SELECT
            l.borrower_id,
            l.approval_status,
            l.outreach_status,
            l.offer_code,
            l.approved_at,
            l.outreach_at,
            a.mirror_refreshed_at AS synced_at,
            a.mirror_refreshed_at AS refreshed_at
        FROM _mip_lifecycle_valid AS l
        CROSS JOIN sync_anchor AS a
        UNION ALL
        SELECT
            b.borrower_id,
            'pending'                  AS approval_status,
            'none'                     AS outreach_status,
            CAST(NULL AS STRING)       AS offer_code,
            CAST(NULL AS TIMESTAMP)    AS approved_at,
            CAST(NULL AS TIMESTAMP)    AS outreach_at,
            a.mirror_refreshed_at      AS synced_at,
            a.mirror_refreshed_at      AS refreshed_at
        FROM mip.gold.borrower_360 AS b
        CROSS JOIN sync_anchor AS a
        LEFT ANTI JOIN _mip_lifecycle_valid AS l
          ON l.borrower_id = b.borrower_id
    """)


def main() -> None:
    conn_kwargs = _resolve_connection()
    try:
        rows = _fetch_lakebase_rows(conn_kwargs)
    except Exception as exc:  # noqa: BLE001 -- operator-facing
        print(f"[sync-lifecycle] Lakebase read failed: {exc}", file=sys.stderr)
        sys.exit(2)

    print(f"[sync-lifecycle] read {len(rows)} rows from Lakebase")
    _write_gold(rows)
    print("[sync-lifecycle] gold mirror refreshed")


# Tolerate being exec()'d without __file__ (some Databricks notebook paths).
_ = Path


if __name__ == "__main__":
    main()
