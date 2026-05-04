"""Provision the Mortgage Lead Intelligence Genie Space declaratively.

Reads ``genie/mortgage_lead_intelligence_space.yml`` as the source of truth
and creates or updates the corresponding Genie Space in the configured
Databricks workspace via the ``databricks-sdk`` Python client.

Discovered ``serialized_space`` schema (see docs/genie-sdk-notes.md):

    {
      "version": 2,
      "data_sources": {
        "tables": [
          {"identifier": "cat.sch.tbl", "description": ["..."]}
        ]
      },
      "config": {
        "sample_questions": [
          {"id": "<32-lowercase-hex>", "question": ["..."]}
        ]
      },
      "instructions": {
        "text_instructions": [
          {"id": "<32-lowercase-hex>", "content": ["..."]}
        ]
      }
    }

Notes baked into this tool:

* ``id`` fields are mandatory on sample_questions and text_instructions. We
  derive them deterministically via md5(seed) so re-running with unchanged
  YAML produces the same ids and the space is truly idempotent.
* ``description``, ``content``, and ``question`` must be JSON arrays even
  when they wrap a single string — the server rejects bare strings with
  "Expected an array".
* Tables must reference catalogs/schemas that already exist in Unity
  Catalog. When the target catalog (``mip`` by default) has not yet
  been created by the Lakeflow pipeline, we create/update the space with
  ``tables: []`` and print a clear next step. The rest of the curation
  (questions, instructions, title, description, warehouse) still lands.

Design choices (Module 0):

* Idempotent. Re-running with unchanged YAML is a no-op (at the API level,
  re-running still PUTs but the payload is byte-identical).
* No new Python deps beyond ``databricks-sdk`` / ``pyyaml`` / ``python-dotenv``.
* Auth resolution mirrors ``databricks`` CLI: env vars first
  (``DATABRICKS_HOST`` / ``DATABRICKS_TOKEN``), then the named CLI profile
  (``--profile`` flag or ``DATABRICKS_CONFIG_PROFILE``, default ``DEFAULT``).
* Warehouse ID comes from ``DATABRICKS_WAREHOUSE_ID`` (same variable the
  bundle picks up via ``BUNDLE_VAR_sql_warehouse_id``).
* On success, writes the resolved space id to ``genie/space_id.txt``
  (gitignored) and prints an ``export`` line the operator can paste.
* Runs a live smoke-test conversation (``--smoke-test``, default on)
  against the new space so operators see that Genie actually
  answers before serving real traffic.
"""

from __future__ import annotations

import argparse
import hashlib
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

# Load .env.local (if present) so DATABRICKS_HOST / DATABRICKS_WAREHOUSE_ID /
# GENIE_SPACE_ID flow through to the SDK. python-dotenv tolerates unquoted
# spaces and angle-bracket placeholders that plain bash sourcing chokes on.
try:
    from dotenv import load_dotenv

    _env_local = REPO_ROOT / ".env.local"
    if _env_local.exists():
        load_dotenv(_env_local, override=False)
except ImportError:  # pragma: no cover — dotenv is pinned in requirements.txt
    pass

DEFAULT_SPACE_NAME = "Mortgage Lead Intelligence"
DEFAULT_PROFILE = "DEFAULT"
DEFAULT_SMOKE_QUESTION = "How many borrowers are currently in-the-money?"
SMOKE_TIMEOUT_SECONDS = 60


def _hex_id(*parts: str) -> str:
    """Return a lowercase 32-hex id derived from the given seed parts.

    The Genie backend requires each sample_question and text_instruction to
    carry a lowercase-hex id without hyphens. We derive it deterministically
    from the YAML content so re-provisioning is idempotent.
    """
    h = hashlib.md5("||".join(parts).encode("utf-8"))
    return h.hexdigest()


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
            catalog=str(raw.get("catalog", "mip")).strip(),
            schema=str(raw.get("schema", "gold")).strip(),
            instructions=str(raw.get("instructions", "")).strip(),
            trusted_assets=list(raw.get("trusted_assets") or []),
            sample_questions=list(raw.get("sample_questions") or []),
        )

    def table_identifiers(self) -> list[str]:
        """Return the `cat.schema.table` identifiers for trusted_assets.

        Metric views (``kind: metric_view``) live under a different schema
        and are returned just like tables — the Genie schema does not
        distinguish the two in the ``data_sources.tables`` list. If the
        Unity Catalog side exposes metric views under their own object
        type in future SDK versions, promote them to their own bucket here.
        """
        return [str(a["name"]).strip() for a in self.trusted_assets if a.get("name")]

    def to_serialized_payload(self, *, include_tables: bool = True) -> str:
        """Build the serialized_space JSON the Genie API accepts.

        ``include_tables=False`` drops table bindings but keeps questions,
        instructions, and version. Used as a fallback when the target
        catalog has not yet been materialized in Unity Catalog.
        """
        tables: list[dict[str, Any]] = []
        if include_tables:
            for asset in self.trusted_assets:
                name = str(asset.get("name", "")).strip()
                if not name:
                    continue
                desc = str(asset.get("description", "")).strip()
                entry: dict[str, Any] = {"identifier": name}
                if desc:
                    entry["description"] = [desc]
                tables.append(entry)
            # Genie's proto validator rejects unsorted `data_sources.tables`
            # with "Invalid export proto: data_sources.tables must be sorted
            # by identifier". Sort here so whatever order the YAML puts
            # trusted_assets in, the serialised payload is always valid.
            tables.sort(key=lambda e: str(e.get("identifier", "")))

        sample_questions: list[dict[str, Any]] = []
        for idx, q in enumerate(self.sample_questions):
            text = str(q).strip()
            if not text:
                continue
            sample_questions.append(
                {
                    "id": _hex_id("sample_question", str(idx), text),
                    "question": [text],
                }
            )

        text_instructions: list[dict[str, Any]] = []
        if self.instructions:
            text_instructions.append(
                {
                    "id": _hex_id("text_instruction", self.instructions),
                    "content": [self.instructions],
                }
            )

        payload: dict[str, Any] = {"version": 2, "data_sources": {"tables": tables}}
        if sample_questions:
            payload["config"] = {"sample_questions": sample_questions}
        if text_instructions:
            payload["instructions"] = {"text_instructions": text_instructions}

        # sort_keys=True keeps the payload byte-stable across runs so an
        # "update with no YAML change" is deterministically a no-op diff.
        return json.dumps(payload, sort_keys=True)


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
    parser.add_argument(
        "--smoke-test",
        dest="smoke_test",
        action="store_true",
        default=True,
        help="After provisioning, run a live sample conversation (default: on).",
    )
    parser.add_argument(
        "--no-smoke-test",
        dest="smoke_test",
        action="store_false",
        help="Skip the post-provisioning conversation.",
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


def _call_create(
    client: Any,
    *,
    warehouse_id: str,
    serialized: str,
    title: str,
    description: str,
) -> Any:
    return client.genie.create_space(
        warehouse_id=warehouse_id,
        serialized_space=serialized,
        title=title,
        description=description,
    )


def _is_missing_catalog_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "catalog" in msg and "does not exist" in msg


def _create_with_fallback(
    client: Any,
    spec: SpaceSpec,
    *,
    warehouse_id: str,
    title: str,
) -> tuple[Any, bool]:
    """Attempt full create; on missing-catalog error, retry with empty tables.

    Returns (created_space, tables_bound_flag).
    """
    serialized_full = spec.to_serialized_payload(include_tables=True)
    try:
        created = _call_create(
            client,
            warehouse_id=warehouse_id,
            serialized=serialized_full,
            title=title,
            description=spec.description,
        )
        return created, True
    except Exception as exc:  # noqa: BLE001
        if not _is_missing_catalog_error(exc):
            raise
        print(
            f"warning: table bindings rejected ({exc}); "
            f"creating space without tables so curation (questions, instructions) still lands.",
            file=sys.stderr,
        )
        serialized_empty = spec.to_serialized_payload(include_tables=False)
        created = _call_create(
            client,
            warehouse_id=warehouse_id,
            serialized=serialized_empty,
            title=title,
            description=spec.description,
        )
        return created, False


def _update_with_fallback(
    client: Any,
    spec: SpaceSpec,
    *,
    space_id: str,
    title: str,
    warehouse_id: str | None,
) -> bool:
    """Attempt full update; fall back to empty tables on missing-catalog error.

    Returns tables_bound_flag.
    """
    serialized_full = spec.to_serialized_payload(include_tables=True)
    try:
        kwargs: dict[str, Any] = {
            "space_id": space_id,
            "description": spec.description,
            "serialized_space": serialized_full,
            "title": title,
        }
        if warehouse_id:
            kwargs["warehouse_id"] = warehouse_id
        client.genie.update_space(**kwargs)
        return True
    except Exception as exc:  # noqa: BLE001
        if not _is_missing_catalog_error(exc):
            raise
        print(
            f"warning: table bindings rejected on update ({exc}); "
            f"updating without tables so questions/instructions still land.",
            file=sys.stderr,
        )
        serialized_empty = spec.to_serialized_payload(include_tables=False)
        kwargs = {
            "space_id": space_id,
            "description": spec.description,
            "serialized_space": serialized_empty,
            "title": title,
        }
        if warehouse_id:
            kwargs["warehouse_id"] = warehouse_id
        client.genie.update_space(**kwargs)
        return False


def _verify_round_trip(client: Any, space_id: str) -> dict[str, Any]:
    """Re-fetch the space with its serialized payload and return the dict.

    Callers assert structure, not content — e.g. that config.sample_questions
    echoes the same count we sent.
    """
    full = client.genie.get_space(space_id, include_serialized_space=True)
    d = full.as_dict() if hasattr(full, "as_dict") else {}
    ss = d.get("serialized_space")
    if isinstance(ss, str):
        try:
            d["_parsed_serialized_space"] = json.loads(ss)
        except Exception:  # noqa: BLE001
            d["_parsed_serialized_space"] = {}
    return d


def _run_smoke_test(client: Any, space_id: str) -> None:
    """Ask the space a deterministic warm-up question.

    If the space is still initializing (common on a freshly-created space
    with empty tables), we surface a friendly note rather than failing —
    the space itself is still correctly provisioned.
    """
    print()
    print(f"smoke-test: asking Genie {DEFAULT_SMOKE_QUESTION!r} (timeout {SMOKE_TIMEOUT_SECONDS}s)...")
    try:
        # SDK 0.103 start_conversation_and_wait does NOT accept a timeout
        # kwarg in all versions; pass only the documented positional args.
        msg = client.genie.start_conversation_and_wait(space_id, DEFAULT_SMOKE_QUESTION)
    except Exception as exc:  # noqa: BLE001
        print(
            f"  Genie is still warming up or no tables are bound yet: {exc}",
            file=sys.stderr,
        )
        return
    # Genie responses are composed of attachments; print the first text answer
    # we can find without over-interpreting the shape.
    printed = False
    for attr in ("content", "text", "message"):
        text = getattr(msg, attr, None)
        if text:
            print(f"  answer ({attr}): {text}")
            printed = True
            break
    if not printed and hasattr(msg, "as_dict"):
        d = msg.as_dict()
        print(f"  response keys: {sorted(d.keys())}")
        # Best-effort: pull the first attachment text if present
        atts = d.get("attachments") or []
        if atts and isinstance(atts, list):
            print(f"  first attachment: {json.dumps(atts[0], indent=2)[:800]}")


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

    plan = "CREATE" if existing is None else "UPDATE"
    print(f"  plan:          {plan}")

    tables_bound = False
    space_id = ""

    if plan == "CREATE":
        if not args.warehouse_id:
            print(
                "error: DATABRICKS_WAREHOUSE_ID (or --warehouse-id) is required to create a space.",
                file=sys.stderr,
            )
            print(f"hint:  open {_workspace_ui_url(host)} to create manually.", file=sys.stderr)
            return 5
        try:
            created, tables_bound = _create_with_fallback(
                client,
                spec,
                warehouse_id=args.warehouse_id,
                title=target_name,
            )
            space_id = getattr(created, "space_id", None) or ""
        except Exception as exc:  # noqa: BLE001
            print(f"error: create_space failed: {exc}", file=sys.stderr)
            return 6
    else:  # UPDATE
        space_id = getattr(existing, "space_id", "") or ""
        try:
            tables_bound = _update_with_fallback(
                client,
                spec,
                space_id=space_id,
                title=target_name,
                warehouse_id=args.warehouse_id or None,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"warning: update_space failed: {exc}", file=sys.stderr)
            print("         existing space kept; continuing with existing id.", file=sys.stderr)

    if not space_id:
        print("error: could not resolve space_id after provisioning.", file=sys.stderr)
        return 7

    # Verify round-trip: fetch the space and confirm curated fields landed.
    try:
        verified = _verify_round_trip(client, space_id)
        # 2026-05-04 hardening: assert the space's bound warehouse matches
        # what we asked for. Earlier the live space was bound to a workspace
        # default (da02d15a9490650b) the app SP didn't have CAN_USE on,
        # producing PERMISSION_DENIED on every Genie call. The provisioner
        # passes warehouse_id on update but the SDK does not always honour
        # warehouse changes silently — we now fail loudly on drift so the
        # operator notices instead of discovering it from a flapping breaker.
        bound_warehouse = verified.get("warehouse_id")
        if args.warehouse_id and bound_warehouse and bound_warehouse != args.warehouse_id:
            print(
                f"error: Genie space is bound to warehouse {bound_warehouse} but the "
                f"bundle / DATABRICKS_WAREHOUSE_ID expects {args.warehouse_id}. "
                f"The app's service principal needs CAN_USE on the bound warehouse "
                f"or every Genie call returns 403 PERMISSION_DENIED.\n"
                f"hint:  databricks genie update-space {space_id} "
                f"--warehouse-id {args.warehouse_id}",
                file=sys.stderr,
            )
            return 8
        parsed = verified.get("_parsed_serialized_space", {}) or {}
        questions = (parsed.get("config", {}) or {}).get("sample_questions", []) or []
        tables = (parsed.get("data_sources", {}) or {}).get("tables", []) or []
        instructions = (parsed.get("instructions", {}) or {}).get("text_instructions", []) or []
        print(
            f"  verified:      title={verified.get('title')!r} "
            f"warehouse={bound_warehouse} "
            f"questions={len(questions)} instructions={len(instructions)} "
            f"tables={len(tables)} (bound_in_payload={tables_bound})"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"warning: verification get_space failed: {exc}", file=sys.stderr)

    _write_space_id(space_id)
    print()
    print(f"Resolved space_id: {space_id}")
    print(f"Written to:        {SPACE_ID_FILE.relative_to(REPO_ROOT)}")
    print(f"Deep link:         {_workspace_ui_url(host, space_id)}")
    print()
    print("Paste into your shell (or .env.local, not committed):")
    print(f"  export BUNDLE_VAR_genie_space_id={space_id}")
    print(f"  export GENIE_SPACE_ID={space_id}")

    if not tables_bound:
        print()
        print(
            "note: trusted_assets in the YAML reference a catalog not yet "
            f"materialized ({spec.catalog}). Run `make bundle-deploy-dev` then "
            "`databricks bundle run refresh_silver -t dev` to create the "
            "gold tables, then re-run this tool to bind them to the space."
        )

    if args.smoke_test:
        _run_smoke_test(client, space_id)

    return 0


def main(argv: list[str] | None = None) -> int:
    return run(_parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
