"""CLI contract for the agent-proxy effective-boundary verifier."""

from __future__ import annotations

import argparse


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Prove the agent-proxy effective boundary with its own OAuth credential."
    )
    result.add_argument("--expected-application-id", required=True)
    result.add_argument("--expected-inventory-principal")
    result.add_argument("--account-host")
    result.add_argument("--account-id")
    result.add_argument("--app-name")
    result.add_argument("--app-url")
    result.add_argument("--lakebase-instance")
    result.add_argument("--warehouse-id")
    result.add_argument("--supervisor-id")
    result.add_argument("--supervisor-endpoint")
    result.add_argument("--supervisor-endpoint-id")
    result.add_argument("--preserve-supervisor-id")
    result.add_argument("--preserve-supervisor-endpoint")
    result.add_argument("--preserve-supervisor-endpoint-id")
    result.add_argument("--genie-space-id")
    result.add_argument("--target-query-only", action="store_true")
    result.add_argument("--customer-resource-denial", action="store_true")
    result.add_argument(
        "--wait-customer-resource-denial",
        action="store_true",
        help="Wait for a fresh target credential to observe managed-group removal.",
    )
    result.add_argument(
        "--allow-attested-app-401",
        action="store_true",
        help="Accept target-App 401 only with a stable independent admin attestation.",
    )
    result.add_argument(
        "--allow-attested-stopped-app-503",
        action="store_true",
        help=(
            "Accept target-App 503 only when a stable independent admin attestation "
            "proves it is stopped, undeployed, and quarantined."
        ),
    )
    return result
