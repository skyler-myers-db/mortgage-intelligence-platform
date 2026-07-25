"""Argument parser for role-separated M2M OAuth provisioning."""

from __future__ import annotations

import argparse

from tools.databricks.m2m_identity_contract import (
    DEFAULT_ADMIN_GROUP,
    DEFAULT_LAKEBASE_INSTANCE,
    IDENTITY_DEFAULTS,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="provision_m2m_oauth",
        description=(
            "Create or converge an operator, admin, release-probe, verifier, "
            "agent-runtime, or agent-proxy M2M identity without printing "
            "one-shot OAuth secrets."
        ),
    )
    parser.add_argument(
        "--identity-role",
        choices=tuple(IDENTITY_DEFAULTS),
        default="normal",
        help="Identity contract to provision (default: normal operator).",
    )
    parser.add_argument(
        "--pre-app-bootstrap",
        action="store_true",
        help=(
            "Before the App exists, create only a new role-bound service principal, "
            "optional reviewed admin group membership, and role-owned GitHub OAuth "
            "credential sinks. Refuses existing principals and forbids all App and "
            "data-resource access."
        ),
    )
    parser.add_argument(
        "--sp-name",
        default=None,
        help=("Role-specific reserved service-principal name; overrides must match " "it exactly."),
    )
    parser.add_argument(
        "--expected-application-id",
        default=None,
        help="Fail closed unless the resolved SP has this OAuth application/client id.",
    )
    parser.add_argument(
        "--app-name",
        default=None,
        help="Deployed App name to grant CAN_USE on (default: databricks.yml).",
    )
    parser.add_argument(
        "--app-url",
        default=None,
        help="Deployed App URL written as MIP_APP_URL GitHub secret.",
    )
    parser.add_argument(
        "--grant-can-use",
        dest="grant_can_use",
        action="store_true",
        default=None,
        help="Grant CAN_USE on the App to the SP.",
    )
    parser.add_argument(
        "--no-grant-can-use",
        dest="grant_can_use",
        action="store_false",
        help="Skip the CAN_USE grant.",
    )
    parser.add_argument(
        "--group-name",
        default=None,
        help=(
            "Role-reserved group (admin default: "
            f"{DEFAULT_ADMIN_GROUP}; release_probe also uses that group; "
            "normal/operator2/verifier/agent_runtime/agent_proxy: none)."
        ),
    )
    parser.add_argument(
        "--create-group",
        action="store_true",
        help=(
            "Create the configured role group if absent. Without this explicit "
            "flag, a missing group fails closed."
        ),
    )
    parser.add_argument(
        "--lakebase-instance",
        default=None,
        help=(
            "Provision an OAuth role on this Lakebase instance "
            f"(verifier default: {DEFAULT_LAKEBASE_INSTANCE})."
        ),
    )
    parser.add_argument(
        "--gateway-endpoint",
        default=None,
        help="Serving endpoint on which the verifier receives CAN_QUERY.",
    )
    parser.add_argument(
        "--revoke-gateway-endpoint",
        action="append",
        default=[],
        help=(
            "Obsolete endpoint on which this identity must retain no effective "
            "query access; repeat for migrations."
        ),
    )
    parser.add_argument(
        "--preserve-gateway-endpoint",
        action="append",
        default=[],
        help=(
            "Signed-blue Gateway whose existing verifier access must remain unchanged "
            "during a green cutover; repeat only for immutable rollback resources."
        ),
    )
    parser.add_argument(
        "--warehouse-id",
        default=None,
        help="SQL warehouse on which the verifier receives CAN_USE.",
    )
    parser.add_argument(
        "--gh-repo",
        default=None,
        help=(
            "GitHub repo owner/name; defaults to `git remote get-url origin`. "
            "Secret minting binds it to that origin or MIP_M2M_GITHUB_REPOSITORY."
        ),
    )
    parser.add_argument(
        "--set-gh-secrets",
        action="store_true",
        help=(
            "Write role-owned OAuth values to GitHub Actions secrets via the "
            "`gh` CLI. Requires `gh auth login`."
        ),
    )
    parser.add_argument(
        "--client-id-secret-name",
        default=None,
        help="Role-owned GitHub client-id sink; overrides must match it exactly.",
    )
    parser.add_argument(
        "--client-secret-secret-name",
        default=None,
        help="Role-owned GitHub client-secret sink; overrides must match it exactly.",
    )
    parser.add_argument(
        "--app-url-secret-name",
        default=None,
        help="Role-owned app-URL sink; only the normal role owns MIP_APP_URL.",
    )
    parser.add_argument(
        "--credential-id-secret-name",
        default=None,
        help="Role-owned credential-id sink; only agent_proxy owns one.",
    )
    parser.add_argument(
        "--no-app-url-secret",
        action="store_true",
        help=(
            "Retain no app-URL sink for operator2/admin/verifier/agent_runtime/"
            "agent_proxy; invalid for normal."
        ),
    )
    parser.add_argument(
        "--no-mint-secret",
        dest="mint_secret",
        action="store_false",
        default=True,
        help="Converge grants/membership without minting or rotating an OAuth secret.",
    )
    parser.add_argument(
        "--rotate",
        action="store_true",
        help=(
            "If the SP exists, mint a fresh secret. Deployment revokes every "
            "non-active credential after cutover."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve defaults and validate the argument set; no SDK calls.",
    )
    return parser
