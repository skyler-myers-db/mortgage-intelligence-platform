"""Provision the nightly M2M OAuth service principal via the Databricks SDK.

Zero-click replacement for the manual Accounts-Console workflow
documented in ``docs/security/m2m-oauth-setup.md``. The user's explicit
requirement is that the full M2M setup is scripted in this repo — no
workspace UI steps.

What this tool does (in order):

1. Create (or reuse) a workspace service principal with the requested
   ``--sp-name``. Idempotent: an SP whose ``displayName`` already matches
   is re-used rather than duplicated.
2. Grant ``CAN USE`` on the deployed Databricks App resource
   (``--app-name``) so the SP can traverse the Apps OAuth proxy.
3. Mint an OAuth client_id + client_secret for the SP. The secret is
   returned ONCE by the API and cannot be retrieved later; the tool
   therefore writes it to one of two sinks depending on ``--set-gh-secrets``:

   - ``--set-gh-secrets`` (opt-in, requires ``gh`` CLI authenticated) →
     the secret is piped into ``gh secret set DATABRICKS_CLIENT_SECRET``
     and never printed to stdout.
   - omitted → the secret is printed to stdout *once* with a loud
     "save this now" banner. The admin pastes it into GitHub manually.

4. Optionally rotate an existing SP's secret (``--rotate``). The old
   secret stays valid until the admin revokes it in the Accounts Console
   — same zero-downtime rotation cadence as the manual flow.

Safety invariants
-----------------
* The secret never touches the repo. It is emitted to stdout or the
  ``gh`` subprocess via stdin; never written to a file in the tree.
* A ``--dry-run`` flag exercises every argument-parsing + SDK-surface
  check without touching the workspace, for CI-safe import coverage.
* SDK failures that indicate "you are not a workspace admin" surface a
  pointed message directing the reader to
  ``docs/security/m2m-oauth-setup.md`` appendix (manual UI path) rather
  than a stack trace.

SDK surface used (pinned to databricks-sdk shipped in requirements.txt)
----------------------------------------------------------------------
* ``w.service_principals.list(filter=...)`` — idempotent lookup.
* ``w.service_principals.create(ServicePrincipal(...))`` — create SP.
* ``w.service_principal_secrets_proxy.create(service_principal_id=...)``
  — mint the OAuth client_secret. Returns a
  ``CreateServicePrincipalSecretResponse`` whose ``.secret`` field is
  the one-shot value. The ``client_id`` is the SP's ``application_id``.
* ``w.apps.set_permissions(app_name, access_control_list=[...])`` —
  grant ``CAN USE`` on the deployed App resource.

Usage
-----
    # canonical happy path (admin auth resolved from ~/.databrickscfg)
    python tools/databricks/provision_m2m_oauth.py \\
        --sp-name mip-nightly-ci-sp \\
        --app-name mip-app \\
        --gh-repo skyler-myers-db/mortgage-intelligence-platform \\
        --set-gh-secrets

    # rotate the existing SP's secret
    python tools/databricks/provision_m2m_oauth.py \\
        --sp-name mip-nightly-ci-sp --rotate --set-gh-secrets

    # CI-safe shape-check (no workspace calls)
    python tools/databricks/provision_m2m_oauth.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DATABRICKS_YML = REPO_ROOT / "databricks.yml"
DOCS_RUNBOOK = "docs/security/m2m-oauth-setup.md"

# Deployed App URL. Written as a GitHub secret alongside the client id/secret
# so the workflow's deployed-path detection flips on in a single admin pass.
DEFAULT_APP_URL = "https://mip-app-2543889327043640.aws.databricksapps.com"


def _diag(msg: str) -> None:
    """Stderr diagnostic. Keeps stdout clean for scripted consumers."""
    print(f"[mip-m2m-provision] {msg}", file=sys.stderr)


def _load_app_name_from_bundle(path: Path = DATABRICKS_YML) -> str:
    """Best-effort parse of the deployed App name out of databricks.yml.

    We want a default that matches what ``databricks bundle deploy`` will
    actually create, without pulling PyYAML in just for this lookup —
    a shallow regex over the known shape is sufficient and keeps this
    tool's dependency surface to the already-present databricks-sdk.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "mip-app"
    # Look for
    #   apps:
    #     mip_app:
    #       name: mip-app
    match = re.search(
        r"^\s+apps:\s*\n(?:\s+#[^\n]*\n)*\s+\w+:\s*\n\s+name:\s*([A-Za-z0-9_-]+)",
        text,
        re.MULTILINE,
    )
    if match:
        return match.group(1)
    return "mip-app"


def _infer_gh_repo() -> str | None:
    """Infer ``owner/repo`` from the ``origin`` remote. None if unresolved."""
    try:
        out = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    # Accept both SSH (git@github.com:owner/repo.git) and HTTPS
    # (https://github.com/owner/repo[.git]).
    match = re.search(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/.\s]+)", out)
    if not match:
        return None
    return f"{match.group('owner')}/{match.group('repo')}"


# ---------------------------------------------------------------------------
# Provisioning state container
# ---------------------------------------------------------------------------


@dataclass
class ProvisionResult:
    """Structured return so tests and callers can assert without parsing stdout."""

    sp_id: str
    sp_application_id: str
    sp_display_name: str
    created_sp: bool
    granted_can_use: bool
    client_id: str
    client_secret: str | None  # None when --rotate wasn't requested & SP existed
    secret_written_to_gh: bool
    gh_repo: str | None


# ---------------------------------------------------------------------------
# Core steps (each accepts an injected client so unit tests can mock)
# ---------------------------------------------------------------------------


def _find_existing_sp(client: Any, display_name: str) -> Any | None:
    """Return the first SP whose displayName matches exactly, else None.

    SCIM ``filter=displayName eq 'X'`` is the idiomatic lookup. We iterate
    the generator in case the workspace has SPs with similar prefixes and
    the server is flexible about matching; only an exact ``display_name``
    match is accepted.
    """
    filter_expr = f"displayName eq '{display_name}'"
    try:
        candidates = list(client.service_principals.list(filter=filter_expr))
    except Exception as exc:  # noqa: BLE001 — SDK raises a grab-bag of types
        raise _wrap_admin_error(exc, step="list service_principals") from exc
    for sp in candidates:
        if getattr(sp, "display_name", None) == display_name:
            return sp
    return None


def _create_sp(client: Any, display_name: str) -> Any:
    """Create a new SP with the requested display name."""
    from databricks.sdk.service.iam import ServicePrincipal  # type: ignore

    _diag(f"creating service principal display_name={display_name!r}")
    try:
        return client.service_principals.create(
            ServicePrincipal(display_name=display_name, active=True),
        )
    except Exception as exc:  # noqa: BLE001
        raise _wrap_admin_error(exc, step="create service_principal") from exc


def _grant_can_use_on_app(
    client: Any,
    app_name: str,
    sp_application_id: str,
) -> None:
    """Grant CAN USE on the Databricks App to the SP.

    Uses ``AppsAPI.set_permissions`` (POST
    ``/api/2.0/preview/permissions/apps/{app_name}/accessControlList``
    under the hood). ``service_principal_name`` expects the SP's
    ``application_id`` (a.k.a. the OAuth client_id), NOT the SCIM ``id``.
    """
    from databricks.sdk.service.apps import (  # type: ignore
        AppAccessControlRequest,
        AppPermissionLevel,
    )

    _diag(f"granting CAN_USE on app={app_name} to application_id={sp_application_id}")
    try:
        client.apps.set_permissions(
            app_name=app_name,
            access_control_list=[
                AppAccessControlRequest(
                    service_principal_name=sp_application_id,
                    permission_level=AppPermissionLevel.CAN_USE,
                )
            ],
        )
    except Exception as exc:  # noqa: BLE001
        # Common failure here is "app not found" when the bundle has not
        # been deployed yet. Give the operator that hint directly.
        msg = str(exc).lower()
        if "not found" in msg or "does not exist" in msg:
            raise SystemExit(
                f"App {app_name!r} not found. Run `databricks bundle deploy -t dev` "
                "first so the App resource exists, then re-run this provisioner."
            ) from exc
        raise _wrap_admin_error(exc, step="set_permissions on app") from exc


def _mint_oauth_secret(client: Any, sp_id: str) -> Any:
    """Mint a new OAuth client_secret for the SP. Returned once, never again."""
    _diag(f"minting OAuth secret for service_principal_id={sp_id}")
    try:
        return client.service_principal_secrets_proxy.create(service_principal_id=sp_id)
    except Exception as exc:  # noqa: BLE001
        raise _wrap_admin_error(exc, step="mint OAuth secret") from exc


def _wrap_admin_error(exc: Exception, *, step: str) -> SystemExit:
    """Turn an SDK exception into a pointed, actionable SystemExit.

    The common failure mode when this tool is run by a non-admin is a
    generic ``PermissionDenied``/403. Rather than surface a stack trace
    we point to the manual UI path documented in the runbook appendix.
    """
    msg = str(exc)
    is_admin_err = any(
        token in msg.lower()
        for token in ("forbidden", "permission denied", "403", "not authorized", "unauthorized")
    )
    hint_lines = [f"[mip-m2m-provision] {step} failed: {type(exc).__name__}: {msg[:400]}"]
    if is_admin_err:
        hint_lines.append(
            "[mip-m2m-provision] this step requires workspace-admin auth; your current "
            f"profile cannot perform {step!r}."
        )
        hint_lines.append(
            f"[mip-m2m-provision] ask an admin to run this tool, or follow the manual "
            f"appendix in {DOCS_RUNBOOK}."
        )
    return SystemExit("\n".join(hint_lines))


# ---------------------------------------------------------------------------
# GitHub secret upload
# ---------------------------------------------------------------------------


def _gh_available() -> bool:
    """True iff the GitHub CLI is installed AND authenticated."""
    if not _which("gh"):
        return False
    try:
        subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False
    return True


def _which(binary: str) -> str | None:
    for path in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(path) / binary
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _set_gh_secret(repo: str, name: str, value: str) -> None:
    """Pipe ``value`` into ``gh secret set NAME --repo repo`` via stdin.

    Value is passed via stdin (not argv) so it never appears in process
    listings. ``gh`` redacts the value server-side; the local process is
    the only place it's ever in plaintext.
    """
    # Emit the GitHub Actions masking directive inside Actions only.
    # Outside Actions the ``::add-mask::`` prefix has no effect, and
    # writing the plaintext secret to stdout (even once) is a leak
    # vector in local terminals / shell history (raised by Copilot
    # 2026-04-22). ``GITHUB_ACTIONS == "true"`` is the canonical
    # marker that a step is running on a GitHub-hosted runner.
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::add-mask::{value}")
    _diag(f"uploading gh secret {name!r} to {repo}")
    try:
        subprocess.run(
            ["gh", "secret", "set", name, "--repo", repo],
            input=value.encode("utf-8"),
            check=True,
            capture_output=True,
            timeout=30,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
        raise SystemExit(
            f"gh secret set {name} failed (exit={exc.returncode}): {stderr[:400]}"
        ) from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(f"gh secret set {name} raised {type(exc).__name__}: {exc}") from exc


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def provision(
    *,
    sp_name: str,
    app_name: str,
    grant_can_use: bool,
    gh_repo: str | None,
    set_gh_secrets: bool,
    rotate: bool,
    app_url: str,
    client_factory: Any | None = None,
) -> ProvisionResult:
    """Provision or refresh the M2M SP and return a structured result.

    ``client_factory`` exists for unit tests — when None we resolve a
    real ``WorkspaceClient`` via the SDK's standard auth chain (env
    vars → ``~/.databrickscfg`` → workspace identity).
    """
    if client_factory is None:

        def client_factory() -> Any:
            from databricks.sdk import WorkspaceClient  # type: ignore

            return WorkspaceClient()

    client = client_factory()

    sp = _find_existing_sp(client, sp_name)
    created_sp = False
    if sp is None:
        sp = _create_sp(client, sp_name)
        created_sp = True
        _diag(f"created SP id={sp.id} application_id={sp.application_id}")
    else:
        _diag(f"reusing existing SP id={sp.id} application_id={sp.application_id}")

    granted = False
    if grant_can_use:
        _grant_can_use_on_app(client, app_name, sp.application_id)
        granted = True
    else:
        _diag("skipping CAN_USE grant (--grant-can-use=false)")

    # Secret minting rules:
    #   - SP was freshly created → always mint (caller needs the secret)
    #   - SP existed + --rotate → mint a new one
    #   - SP existed + no --rotate → skip, we can't read the existing secret
    secret_value: str | None = None
    client_id = sp.application_id
    if created_sp or rotate:
        resp = _mint_oauth_secret(client, sp.id)
        secret_value = getattr(resp, "secret", None)
        if not secret_value:
            raise SystemExit(
                "mint returned no .secret value; SDK contract violation. "
                f"Response fields: {list(resp.__dict__.keys()) if hasattr(resp, '__dict__') else 'unknown'}"
            )
    else:
        _diag(
            "SP already exists and --rotate was not passed; skipping mint. "
            "Pass --rotate to generate a fresh secret."
        )

    # GitHub secret push (only when we have a secret to push).
    wrote_secrets = False
    if set_gh_secrets and secret_value is not None:
        if not gh_repo:
            raise SystemExit(
                "--set-gh-secrets requires --gh-repo (or a git remote from which the "
                "repo can be inferred)."
            )
        if not _gh_available():
            _diag(
                "WARNING: gh CLI unavailable or unauthenticated — skipping secret "
                "upload. Run these manually after installing gh + `gh auth login`:"
            )
            _diag(f"  gh secret set DATABRICKS_CLIENT_ID --repo {gh_repo} --body '{client_id}'")
            _diag(f"  gh secret set DATABRICKS_CLIENT_SECRET --repo {gh_repo}  # pipe secret via stdin")
            _diag(f"  gh secret set MIP_APP_URL --repo {gh_repo} --body '{app_url}'")
        else:
            _set_gh_secret(gh_repo, "DATABRICKS_CLIENT_ID", client_id)
            _set_gh_secret(gh_repo, "DATABRICKS_CLIENT_SECRET", secret_value)
            _set_gh_secret(gh_repo, "MIP_APP_URL", app_url)
            wrote_secrets = True

    return ProvisionResult(
        sp_id=sp.id,
        sp_application_id=sp.application_id,
        sp_display_name=sp.display_name,
        created_sp=created_sp,
        granted_can_use=granted,
        client_id=client_id,
        client_secret=secret_value,
        secret_written_to_gh=wrote_secrets,
        gh_repo=gh_repo,
    )


def _print_summary(result: ProvisionResult, *, set_gh_secrets: bool) -> None:
    """Human-readable summary on stderr; one-shot secret on stdout if needed."""
    lines = [
        "",
        "=== M2M OAuth provisioning summary ===",
        f"  service_principal:        {result.sp_display_name} (id={result.sp_id})",
        f"  application_id (client_id): {result.client_id}",
        f"  created this run:         {result.created_sp}",
        f"  granted CAN_USE on app:   {result.granted_can_use}",
    ]
    if result.client_secret is None:
        lines.append("  client_secret:            (not rotated; existing SP secret unchanged)")
    elif result.secret_written_to_gh:
        lines.append("  client_secret:            uploaded to GitHub Actions secrets")
        lines.append(f"  gh repo:                  {result.gh_repo}")
    else:
        lines.append("  client_secret:            written to stdout below -- SAVE NOW")
    lines.append("")
    for line in lines:
        _diag(line)

    # One-shot stdout emission of the secret ONLY when caller didn't opt
    # into gh upload. This is the same contract as oauth_m2m_mint.py:
    # stdout carries exactly one payload, stderr carries diagnostics.
    if result.client_secret is not None and not result.secret_written_to_gh:
        print("### DATABRICKS_CLIENT_SECRET (save this now; it will not be shown again) ###")
        print(result.client_secret)
        print("### end secret ###")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="provision_m2m_oauth",
        description=(
            "Create the nightly M2M OAuth service principal + secret via the "
            "Databricks SDK. Replaces the manual Accounts-Console workflow."
        ),
    )
    parser.add_argument(
        "--sp-name",
        default="mip-nightly-ci-sp",
        help="Display name for the service principal (default: mip-nightly-ci-sp).",
    )
    parser.add_argument(
        "--app-name",
        default=None,
        help=(
            "Deployed App name to grant CAN_USE on "
            f"(default: resolved from {DATABRICKS_YML.name})."
        ),
    )
    parser.add_argument(
        "--app-url",
        default=os.environ.get("MIP_APP_URL", DEFAULT_APP_URL),
        help="Deployed App URL written as MIP_APP_URL GitHub secret.",
    )
    parser.add_argument(
        "--grant-can-use",
        dest="grant_can_use",
        action="store_true",
        default=True,
        help="Grant CAN_USE on the App to the SP (default: on).",
    )
    parser.add_argument(
        "--no-grant-can-use",
        dest="grant_can_use",
        action="store_false",
        help="Skip the CAN_USE grant (use if an admin grants it separately).",
    )
    parser.add_argument(
        "--gh-repo",
        default=None,
        help="GitHub repo owner/name (default: inferred from `git remote get-url origin`).",
    )
    parser.add_argument(
        "--set-gh-secrets",
        action="store_true",
        help=(
            "Write client_id / client_secret / MIP_APP_URL to the GitHub repo's "
            "Actions secrets via the `gh` CLI. Requires `gh auth login`."
        ),
    )
    parser.add_argument(
        "--rotate",
        action="store_true",
        help=(
            "If the SP already exists, mint a fresh OAuth secret. Old secret "
            "stays valid until revoked in the Accounts Console."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve defaults and validate the argument set; no SDK calls.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    app_name = args.app_name or _load_app_name_from_bundle()
    gh_repo = args.gh_repo or _infer_gh_repo()

    _diag(
        f"provisioning plan: sp_name={args.sp_name!r} app_name={app_name!r} "
        f"gh_repo={gh_repo!r} set_gh_secrets={args.set_gh_secrets} rotate={args.rotate}"
    )

    if args.dry_run:
        _diag("--dry-run requested; no SDK calls will be made")
        if args.set_gh_secrets and not gh_repo:
            _diag("WARNING: --set-gh-secrets requires --gh-repo (or a detectable origin)")
        if args.set_gh_secrets and not _gh_available():
            _diag("NOTE: gh CLI unavailable; a real run would skip secret upload")
        return 0

    try:
        result = provision(
            sp_name=args.sp_name,
            app_name=app_name,
            grant_can_use=args.grant_can_use,
            gh_repo=gh_repo,
            set_gh_secrets=args.set_gh_secrets,
            rotate=args.rotate,
            app_url=args.app_url,
        )
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — surface root cause
        _diag(f"ERROR unhandled {type(exc).__name__}: {exc}")
        return 1

    _print_summary(result, set_gh_secrets=args.set_gh_secrets)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
