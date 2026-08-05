"""Secret-free operator summary for M2M OAuth provisioning."""

from __future__ import annotations

import sys

from tools.databricks.m2m_identity_contract import ProvisionResult


def print_summary(result: ProvisionResult) -> None:
    lines = [
        "",
        "=== M2M OAuth provisioning summary ===",
        f"  service_principal:        {result.sp_display_name} (id={result.sp_id})",
        f"  application_id (client_id): {result.client_id}",
        f"  created this run:         {result.created_sp}",
        f"  granted CAN_USE on app:   {result.granted_can_use}",
        f"  identity group:           {result.group_name or '(none)'}",
        f"  group membership added:   {result.added_to_group}",
        f"  Lakebase instance:        {result.lakebase_instance or '(none)'}",
        f"  Lakebase role created:    {result.created_lakebase_role}",
        f"  Gateway endpoint:         {result.gateway_endpoint or '(none)'}",
        f"  granted CAN_QUERY:        {result.granted_can_query}",
        f"  SQL warehouse:            {result.warehouse_id or '(none)'}",
        f"  granted warehouse CAN_USE:{result.granted_warehouse_can_use}",
        f"  OAuth secret minted:      {result.secret_minted}",
        f"  GitHub secrets updated:   {result.secret_written_to_gh}",
    ]
    if result.secret_written_to_gh:
        lines.append(f"  gh repo:                  {result.gh_repo}")
    lines.append("")
    for line in lines:
        print(f"[mip-m2m-provision] {line}", file=sys.stderr)
