"""Provision the Mortgage Lead Intelligence Genie Space declaratively.

Reads ``genie/mortgage_lead_intelligence_space.yml`` as the source of truth
and creates or updates the corresponding Genie Space in the configured
Databricks workspace via the ``databricks-sdk`` Python client.

Design choices (Module 0 / DAIS booth):

* Idempotent. Re-running with unchanged YAML should be a no-op.
* No new Python deps beyond ``databricks-sdk`` (already in requirements.txt).
* Auth resolution mirrors ``databricks`` CLI: env vars first
  (``DATABRICKS_HOST`` / ``DATABRICKS_TOKEN``), then the named CLI profile
  (``--profile`` flag or ``DATABRICKS_CONFIG_PROFILE``, default ``DEFAULT``).
* Warehouse ID comes from ``DATABRICKS_WAREHOUSE_ID`` (same variable the
  bundle picks up via ``BUNDLE_VAR_sql_warehouse_id``).
* On success, writes the resolved space id to ``genie/space_id.txt``
  (gitignored) and prints an ``export`` line the operator can paste.
* On partial success (e.g. SDK rejects serialized_space payload), the tool
  still reports what it found and prints a workspace deep-link so the
  operator can finish in the UI.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover - PyYAML ships with databricks-sdk deps
    raise SystemExit(
        "PyYAML is required. It is a transitive dep of databricks-sdk; "
        "re-run `pip install -r requirements.txt`."
    ) from exc


REPO_ROOT = Path(__file__).resolve().parents[2]
SPACE_YAML = REPO_ROOT / "genie" / "mortgage_lead_intelligence_space.yml"
SPACE_ID_FILE = REPO_ROOT / "genie" / "space_id.txt"

DEFAULT_SPACE_NAME = "Mortgage Lead Intelligence"
DEFAULT_PROFILE = "DEFAULT"


@dataclass(frozen=True)
class SpaceSpec:
    """In-memory representation of the curated Genie Space YAML."""

    name: str
    description: str
    catalog: str
    schema: str
    instructions: str
    trusted_assets: list[dict[str, str]]
    sample_questions: list[str]

    @classmethod
    def load(cls, path: Path) -> SpaceSpec:
        raw = yaml.safe_load(path.read_text())
        if not isinstance(raw, dict):
            raise ValueError(f"{path} is not a YAML mapping")
        return cls(
            name=str(raw.get("name", DEFAULT_SPACE_NAME)).strip(),
            description=str(raw.get("description", "")).strip(),
            catalog=str(raw.get("catalog", "mip_demo")).strip(),
            schema=str(raw.get("schema", "gold")).strip(),
            instructions=str(raw.get("instructions", "")).strip(),
            trusted_assets=list(raw.get("trusted_assets") or []),
            sample_questions=list(raw.get("sample_questions") or []),
        )

    def to_serialized_payload(self) -> str:
        """Best-effort serialized_space JSON for the SDK create/update calls.

        The Genie ``serialized_space`` format is not documented in the
        public SDK; this shape carries the curated fields so the backend
        has everything it needs. If the workspace rejects it, the tool
        prints a clear fallback message rather than crashing.
        """
        payload: dict[str, Any] = {
            "title": self.name,
            "description": self.description,
            "instructions": self.instructions,
            "default_catalog": self.catalog,
            "default_schema": self.schema,
            "trusted_assets": [
                {
                    "name": asset.get("name", ""),
                    "kind": asset.get("kind", "table"),
                    "description": asset.get("description", ""),
                }
                for asset in self.trusted_assets
            ],
            "sample_questions": list(self.sample_questions),
        }
        return json.dumps(payload, indent=2, sort_keys=True)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create or update the Mortgage Lead Intelligence Genie Space."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan (create / update / no-op) without calling the SDK.",
    )
    parser.add_argument(
        "--profile",
        default=os.environ.get("DATABRICKS_CONFIG_PROFILE", DEFAULT_PROFILE),
        help=f"Databricks CLI profile to use (default: {DEFAULT_PROFILE}).",
    )
    parser.add_argument(
        "--space-name",
        default=None,
        help="Override the target space name (default: value of YAML `name`).",
    )
    parser.add_argument(
        "--workspace-host",
        default=os.environ.get("DATABRICKS_HOST"),
        help="Override the workspace host URL.",
    )
    parser.add_argument(
        "--warehouse-id",
        default=os.environ.get("DATABRICKS_WAREHOUSE_ID"),
        help="SQL warehouse to bind the space to (required when creating).",
    )
    parser.add_argument(
        "--spec",
        default=str(SPACE_YAML),
        help="Path to the space YAML (default: genie/mortgage_lead_intelligence_space.yml).",
    )
    return parser.parse_args(argv)


def _build_client(args: argparse.Namespace):
    """Instantiate WorkspaceClient using CLI-equivalent auth resolution."""
    from databricks.sdk import WorkspaceClient

    kwargs: dict[str, Any] = {}
    if args.workspace_host:
        kwargs["host"] = args.workspace_host
    token = os.environ.get("DATABRICKS_TOKEN")
    if token:
        kwargs["token"] = token
    if not token and args.profile:
        kwargs["profile"] = args.profile
    return WorkspaceClient(**kwargs)


def _find_space(client: Any, target_name: str) -> Any | None:
    """Page through all spaces and return the first whose title matches."""
    page_token: str | None = None
    while True:
        resp = client.genie.list_spaces(page_token=page_token)
        spaces = getattr(resp, "spaces", None) or []
        for space in spaces:
            title = getattr(space, "title", None) or getattr(space, "name", None)
            if title and title.strip() == target_name:
                return space
        page_token = getattr(resp, "next_page_token", None)
        if not page_token:
            return None


def _workspace_ui_url(host: str | None, space_id: str | None = None) -> str:
    base = (host or "").rstrip("/")
    if not base:
        return ""
    if space_id:
        return f"{base}/genie/rooms/{space_id}"
    return f"{base}/genie"


def _write_space_id(space_id: str) -> None:
    SPACE_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
    SPACE_ID_FILE.write_text(space_id + "\n")


def _plan(spec: SpaceSpec, existing: Any | None) -> str:
    if existing is None:
        return "CREATE"
    existing_desc = getattr(existing, "description", "") or ""
    if existing_desc.strip() == spec.description.strip():
        return "NO-OP"
    return "UPDATE"


def run(args: argparse.Namespace) -> int:
    spec_path = Path(args.spec)
    if not spec_path.exists():
        print(f"error: spec not found at {spec_path}", file=sys.stderr)
        return 2

    spec = SpaceSpec.load(spec_path)
    target_name = (args.space_name or spec.name).strip() or DEFAULT_SPACE_NAME

    print("Mortgage Lead Intelligence Genie Space provisioner")
    print(f"  spec:          {spec_path}")
    print(f"  target name:   {target_name}")
    print(f"  trusted assets:{len(spec.trusted_assets)}")
    print(f"  sample qs:     {len(spec.sample_questions)}")
    print(f"  profile:       {args.profile}")
    print(f"  dry-run:       {args.dry_run}")

    if args.dry_run:
        print()
        print("Plan (dry-run, no SDK calls made):")
        print(f"  - Resolve workspace via profile={args.profile!r} (or env).")
        print(f"  - List spaces, search for title == {target_name!r}.")
        print("  - If not found: CREATE via genie.create_space() with")
        print("      warehouse_id = ${DATABRICKS_WAREHOUSE_ID}")
        print(f"      title        = {target_name!r}")
        print(f"      description  = {spec.description[:60]!r}...")
        print("  - If found: UPDATE title / description / serialized_space if drift.")
        print(f"  - Write resolved space_id to {SPACE_ID_FILE.relative_to(REPO_ROOT)}")
        print("  - Print export line: BUNDLE_VAR_genie_space_id=<id>")
        return 0

    try:
        client = _build_client(args)
    except Exception as exc:  # noqa: BLE001 - surface CLI-friendly error
        print(f"error: could not build WorkspaceClient: {exc}", file=sys.stderr)
        return 3

    host = getattr(client.config, "host", None)
    print(f"  workspace:     {host}")

    try:
        existing = _find_space(client, target_name)
    except Exception as exc:  # noqa: BLE001
        print(f"error: list_spaces failed: {exc}", file=sys.stderr)
        print(f"hint:  open {_workspace_ui_url(host)} to create it manually.", file=sys.stderr)
        return 4

    plan = _plan(spec, existing)
    print(f"  plan:          {plan}")

    serialized = spec.to_serialized_payload()

    if plan == "CREATE":
        if not args.warehouse_id:
            print(
                "error: DATABRICKS_WAREHOUSE_ID (or --warehouse-id) is required to create a space.",
                file=sys.stderr,
            )
            print(f"hint:  open {_workspace_ui_url(host)} to create manually.", file=sys.stderr)
            return 5
        try:
            created = client.genie.create_space(
                warehouse_id=args.warehouse_id,
                serialized_space=serialized,
                title=target_name,
                description=spec.description,
            )
            space_id = getattr(created, "space_id", None) or ""
        except Exception as exc:  # noqa: BLE001
            print(f"error: create_space failed: {exc}", file=sys.stderr)
            print(
                "hint:  the SDK's serialized_space schema is not public; "
                f"create the space manually at {_workspace_ui_url(host)} and "
                "re-run this tool with the existing space in place (it will "
                "become a NO-OP / UPDATE path).",
                file=sys.stderr,
            )
            return 6
    elif plan == "UPDATE":
        space_id = getattr(existing, "space_id", "") or ""
        try:
            client.genie.update_space(
                space_id=space_id,
                description=spec.description,
                serialized_space=serialized,
                title=target_name,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"warning: update_space failed: {exc}", file=sys.stderr)
            print(
                "         space already exists; continuing with existing id.",
                file=sys.stderr,
            )
    else:  # NO-OP
        space_id = getattr(existing, "space_id", "") or ""

    if not space_id:
        print("error: could not resolve space_id after provisioning.", file=sys.stderr)
        return 7

    _write_space_id(space_id)
    print()
    print(f"Resolved space_id: {space_id}")
    print(f"Written to:        {SPACE_ID_FILE.relative_to(REPO_ROOT)}")
    print(f"Deep link:         {_workspace_ui_url(host, space_id)}")
    print()
    print("Paste into your shell (or .env.local, not committed):")
    print(f"  export BUNDLE_VAR_genie_space_id={space_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(_parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
