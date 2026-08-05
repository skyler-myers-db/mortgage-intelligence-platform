"""Verify the exact Lakebase boundary under the current OAuth M2M identity."""

from __future__ import annotations

import argparse
import uuid

from backend.services.lakebase_identity_gate import verify_lakebase_oauth_identity


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-application-id", required=True)
    parser.add_argument("--lakebase-instance", required=True)
    parser.add_argument("--lakebase-database", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    from databricks.sdk import WorkspaceClient

    args = _parser().parse_args(argv)
    workspace = WorkspaceClient()
    me = workspace.current_user.me()
    authenticated_ids = {
        str(getattr(me, field, "") or "").strip()
        for field in ("application_id", "user_name")
    }
    if args.expected_application_id not in authenticated_ids:
        raise RuntimeError("authenticated M2M identity does not match the expected application id")
    service_principal_id = str(getattr(me, "id", "") or "").strip()
    if not service_principal_id:
        raise RuntimeError("authenticated M2M identity has no immutable id")
    instance = workspace.database.get_database_instance(args.lakebase_instance)
    host = str(getattr(instance, "read_write_dns", "") or "").strip()
    credential = workspace.database.generate_database_credential(
        instance_names=[args.lakebase_instance],
        request_id=str(uuid.uuid4()),
    )
    token = str(getattr(credential, "token", "") or "")
    if not host or not token:
        raise RuntimeError("Lakebase host or credential resolution failed")
    verify_lakebase_oauth_identity(
        host=host,
        port=5432,
        database=args.lakebase_database,
        user=args.expected_application_id,
        password=token,
        sslmode="require",
        expected_application_id=args.expected_application_id,
        expected_service_principal_id=service_principal_id,
    )
    print("Lakebase OAuth identity and replication-denial boundary: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
