"""CLI parser for governed agent-runtime blue/green cutover."""

from __future__ import annotations

import argparse
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "retire", "finalize"):
        command = subparsers.add_parser(name)
        command.add_argument("--canonical-name", default="Mortgage Growth Agent")
        command.add_argument("--replacement-id", required=True)
        command.add_argument("--replacement-endpoint", required=True)
        command.add_argument("--runtime-application-id", required=True)
    for name in ("prepare", "retire"):
        command = subparsers.choices[name]
        command.add_argument("--gateway-endpoint", required=True)
        command.add_argument("--gateway-model", required=True)
        command.add_argument("--gateway-model-version", type=int, required=True)
        command.add_argument("--gateway-inference-table", required=True)
        command.add_argument("--gateway-model-family", required=True)
        command.add_argument("--gateway-experiment-base", required=True)
        command.add_argument("--gateway-table-prefix", required=True)
        command.add_argument("--catalog", required=True)
        command.add_argument("--genie-space-id", required=True)
        command.add_argument("--preserve-endpoint", action="append", default=[])
        command.add_argument("--verifier-application-id", required=True)
        command.add_argument("--verifier-scim-id", required=True)
    retire = subparsers.choices["retire"]
    for flag in (
        "--old-id",
        "--old-endpoint",
        "--old-endpoint-id",
        "--old-creator",
        "--old-create-time",
        "--old-gateway-endpoint",
        "--old-gateway-endpoint-id",
        "--old-gateway-creator",
    ):
        retire.add_argument(flag)
    retire.add_argument("--old-gateway-delete-allowed", action="store_true")
    retire.add_argument("--proxy-application-id", required=True)
    retire.add_argument("--timeout-s", type=int, default=900)
    finalize = subparsers.choices["finalize"]
    finalize.add_argument("--catalog", required=True)
    finalize.add_argument("--genie-space-id", required=True)
    pin = subparsers.add_parser("pin-journal")
    pin.add_argument("--runtime-application-id", required=True)
    pin.add_argument("--canonical-name", default="Mortgage Growth Agent")
    for flag in (
        "--old-id",
        "--old-endpoint",
        "--old-creator",
        "--old-create-time",
        "--old-gateway-endpoint",
    ):
        pin.add_argument(flag)
    export = subparsers.add_parser("export-journal")
    export.add_argument("--runtime-application-id", required=True)
    export.add_argument("--out-env", type=Path, required=True)
    refresh = subparsers.add_parser("refresh-journal-attestation")
    refresh.add_argument("--runtime-application-id", required=True)
    clear = subparsers.add_parser("clear-journal")
    clear.add_argument("--runtime-application-id", required=True)
    clear.add_argument("--app-application-id", required=True)
    clear.add_argument("--app-scim-id", required=True)
    clear.add_argument("--verifier-application-id", required=True)
    clear.add_argument("--verifier-scim-id", required=True)
    clear.add_argument("--proxy-application-id", required=True)
    resume = subparsers.add_parser("resume-stale-journal")
    resume.add_argument("--runtime-application-id", required=True)
    resume.add_argument("--app-application-id", required=True)
    resume.add_argument("--verifier-application-id", required=True)
    resume.add_argument("--verifier-scim-id", required=True)
    resume.add_argument("--proxy-application-id", required=True)
    resume.add_argument("--timeout-s", type=int, default=900)
    acl = subparsers.add_parser("converge-app-acl")
    acl.add_argument("--gateway-endpoint", required=True)
    acl.add_argument("--supervisor-endpoint", required=True)
    for name, command in subparsers.choices.items():
        if name != "export-journal":
            command.add_argument("--app-name", required=True)
            command.add_argument("--deployment-lease-id", required=True)
            command.add_argument("--deployment-source-git-sha", required=True)
    return parser
