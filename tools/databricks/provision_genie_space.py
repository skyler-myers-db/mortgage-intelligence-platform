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
* Warehouse ID comes from ``--warehouse-id`` (scripts/deploy.sh passes the
  warehouse its bundle state binds) or ``DATABRICKS_WAREHOUSE_ID``.
* The governed space is resolved by a pinned id, never by its title alone
  (tools/databricks/genie_space_pin.py, attack G12, M1-PR3): the signed pin
  record or the App's signed rollback record. With no pin, no title match
  means a first create, which pins the new id; one title match is refused
  unless ``--ack-plan`` is the sha256 of its read-only pin plan
  (``--plan-pin`` prints it, with no mutation); more than one is refused. A
  write needs the deploy's proof-signing key pair
  (``MIP_AI_GATEWAY_PROOF_SIGNING_KEY`` / ``_VERIFY_KEY``), checked before
  any mutation. ``GENIE_SPACE_ID`` is never an input.
* Two operator-only modes (scripts/deploy.sh passes neither):
  ``--retire-pin`` retires the pin of a pinned space that is gone (disaster
  recovery; tools/databricks/genie_space_pin_retire.py), read-only with
  ``--plan-pin`` and otherwise only with ``--ack-plan`` equal to the retire
  plan's sha256. ``--no-pin`` updates an acked title match (or a pinned
  space) without writing a pin: the legacy live install's stand-alone spec
  update until it is adopted (plan M2-PR6). It never creates a space.
* On success, writes the resolved space id to ``genie/space_id.txt``
  (gitignored), the run-local hand-off to scripts/deploy.sh.
* Runs a live smoke-test conversation (``--smoke-test``, default on)
  against the new space so operators see that Genie actually
  answers before serving real traffic.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, replace
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
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.config import source_names  # noqa: E402
from backend.config.source_names import CotalitySource  # noqa: E402
from tools import render_sql  # noqa: E402
from tools.databricks import genie_space_pin  # noqa: E402
from tools.databricks.genie_space_writes import (  # noqa: E402
    _create_with_fallback,
    _update_with_fallback,
    _verify_round_trip,
)
from tools.databricks.workspace_auth import (  # noqa: E402
    APP_FACING_WORKSPACE_AUTH_ENV,
    deployment_workspace_client,
)

SPACE_YAML = REPO_ROOT / "genie" / "mortgage_lead_intelligence_space.yml"
SPACE_ID_FILE = REPO_ROOT / "genie" / "space_id.txt"


def _load_env_local() -> None:
    """Overlay .env.local onto the process env for SUBPROCESS CLI runs ONLY.

    Called exclusively from the ``__main__`` guard — never at import time
    and never from ``main(argv)``, which unit tests invoke in-process. An
    import-time ``load_dotenv`` mutated ``os.environ`` for the whole pytest
    process and poisoned the fail-closed settings contract test on any
    operator machine with a populated ``.env.local`` (2026-06-11 incident:
    MIP_ADMIN_EMAILS leaked); moving it merely into ``main()`` reproduced
    the same leak through the dry-run test. python-dotenv tolerates
    unquoted spaces and angle-bracket placeholders that bash sourcing
    chokes on.
    """
    try:
        from dotenv import dotenv_values
    except ImportError:  # pragma: no cover — dotenv is pinned in requirements.txt
        return
    env_local = REPO_ROOT / ".env.local"
    if env_local.exists():
        for key, value in dotenv_values(env_local).items():
            if value is None or key in os.environ:
                continue
            # Catalog routing is a deployment control, not a local default.
            # A stale .env.local MIP_DEFAULT_CATALOG can update the live Genie
            # space with zero bound tables; scripts/deploy.sh exports the
            # intended catalog before invoking this tool. Standalone operators
            # should use --catalog or an explicit exported env var. The
            # Cotality source names are deployment controls in the same way.
            if key in {
                "MIP_DEFAULT_CATALOG",
                "MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS",
                *source_names.ENV_NAMES,
            }:
                continue
            if key in APP_FACING_WORKSPACE_AUTH_ENV:
                continue
            if os.environ.get("MIP_DEPLOYER_DATABRICKS_PROFILE") and key in {
                "DATABRICKS_HOST",
                "DATABRICKS_TOKEN",
                "DATABRICKS_AUTH_TYPE",
            }:
                continue
            os.environ[key] = value

DEFAULT_SPACE_NAME = "Mortgage Lead Intelligence"
DEFAULT_APP_NAME = "mip-app"
DEFAULT_LAKEBASE_INSTANCE = "mip-app-state"
DEFAULT_PROFILE = "DEFAULT"
# Exit codes of the pin (M1-PR3): 10 refused before any mutation, 11 a space
# was created but its pin could not be written (the next run refuses it as an
# unpinned title match until it is acked).
EXIT_PIN_REFUSED = 10
EXIT_PIN_UNWRITTEN = 11
DEFAULT_SMOKE_QUESTION = "How many borrowers are currently in-the-money?"
SMOKE_TIMEOUT_SECONDS = 60
DEFAULT_TENANT_NAME = "Summit Mortgage"
DEFAULT_CATALOG = "mip"
_UC_SCHEMAS = ("gold", "semantics", "silver", "ref", "raw")


def _hex_id(*parts: str) -> str:
    """Return a lowercase 32-hex id derived from the given seed parts.

    The Genie backend requires each sample_question and text_instruction to
    carry a lowercase-hex id without hyphens. We derive it deterministically
    from the YAML content so re-provisioning is idempotent.
    """
    h = hashlib.md5("||".join(parts).encode("utf-8"))
    return h.hexdigest()


def _sort_snippet_groups(value: Any, *, path: str = "sql_snippets") -> Any:
    """Sort snippet arrays by id for Genie's export-proto validator."""

    if isinstance(value, dict):
        out = {k: _sort_snippet_groups(v, path=f"{path}.{k}") for k, v in value.items()}
        raw_id = out.get("id")
        if isinstance(raw_id, str) and not _is_hex32(raw_id):
            out["id"] = _hex_id(path, raw_id)
        return out
    if isinstance(value, list):
        sorted_items = sorted(
            (_sort_snippet_groups(v, path=path) for v in value),
            key=lambda item: str(item.get("id", "")) if isinstance(item, dict) else "",
        )
        return sorted_items
    return value


def _is_hex32(value: str) -> bool:
    return len(value) == 32 and all(ch in "0123456789abcdef" for ch in value)


def _tenant_name() -> str:
    return (os.environ.get("MIP_LENDER_NAME") or DEFAULT_TENANT_NAME).strip() or DEFAULT_TENANT_NAME


def _catalog_name() -> str:
    return (os.environ.get("MIP_DEFAULT_CATALOG") or DEFAULT_CATALOG).strip() or DEFAULT_CATALOG


def _cotality_source() -> CotalitySource:
    return source_names.from_env(os.environ)


def _render_space_templates(
    value: Any,
    *,
    tenant_name: str,
    catalog_name: str,
    cotality: CotalitySource = source_names.CANONICAL,
) -> Any:
    """Render tenant, install catalog and Cotality source into every string.

    The Cotality source goes through tools/render_sql.py's closed-shape rewrite
    last, so the install-catalog rules never run over the source names, and a
    non-canonical source refuses a string that still carries a canonical token.
    """
    if isinstance(value, str):
        rendered = value.replace("{tenant_name}", tenant_name).replace("{catalog}", catalog_name)
        if catalog_name != DEFAULT_CATALOG:
            schema_group = "|".join(re.escape(schema) for schema in _UC_SCHEMAS)
            rendered = re.sub(
                rf"(?<![A-Za-z0-9_]){re.escape(DEFAULT_CATALOG)}\.({schema_group})\b",
                lambda match: f"{catalog_name}.{match.group(1)}",
                rendered,
            )
            rendered = re.sub(
                rf"(?<![A-Za-z0-9_]){re.escape(DEFAULT_CATALOG)}(?![A-Za-z0-9_])",
                catalog_name,
                rendered,
            )
        return render_sql.render_cotality_source(rendered, cotality, where="Genie space spec")[0]
    if isinstance(value, list):
        return [
            _render_space_templates(
                item, tenant_name=tenant_name, catalog_name=catalog_name, cotality=cotality
            )
            for item in value
        ]
    if isinstance(value, dict):
        return {
            key: _render_space_templates(
                item, tenant_name=tenant_name, catalog_name=catalog_name, cotality=cotality
            )
            for key, item in value.items()
        }
    return value


def _column_config_v2(config: dict[str, Any]) -> dict[str, Any]:
    """Translate YAML column hints to Genie serialized_space v2 fields."""

    out = {
        k: v
        for k, v in config.items()
        if k not in {"get_example_values", "build_value_dictionary"}
    }
    if "get_example_values" in config:
        out["enable_format_assistance"] = bool(config["get_example_values"])
    if "build_value_dictionary" in config:
        out["enable_entity_matching"] = bool(config["build_value_dictionary"])
    return out


@dataclass(frozen=True)
class SpaceSpec:
    """In-memory representation of the curated Genie Space YAML."""

    name: str
    description: str
    catalog: str
    schema: str
    instructions: str
    trusted_assets: list[dict[str, Any]]
    sample_questions: list[str]
    example_question_sqls: list[dict[str, Any]]
    sql_snippets: dict[str, Any]

    @classmethod
    def load(cls, path: Path) -> SpaceSpec:
        raw = yaml.safe_load(path.read_text())
        if not isinstance(raw, dict):
            raise ValueError(f"{path} is not a YAML mapping")
        raw = _render_space_templates(
            raw,
            tenant_name=_tenant_name(),
            catalog_name=_catalog_name(),
            cotality=_cotality_source(),
        )
        return cls(
            name=str(raw.get("name", DEFAULT_SPACE_NAME)).strip(),
            description=str(raw.get("description", "")).strip(),
            catalog=str(raw.get("catalog", "mip")).strip(),
            schema=str(raw.get("schema", "gold")).strip(),
            instructions=str(raw.get("instructions", "")).strip(),
            trusted_assets=list(raw.get("trusted_assets") or []),
            sample_questions=list(raw.get("sample_questions") or []),
            example_question_sqls=list(raw.get("example_question_sqls") or []),
            sql_snippets=dict(raw.get("sql_snippets") or {}),
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
                column_configs = asset.get("column_configs")
                if isinstance(column_configs, list) and column_configs:
                    entry["column_configs"] = sorted(
                        [
                            _column_config_v2(cfg)
                            for cfg in column_configs
                            if isinstance(cfg, dict)
                        ],
                        key=lambda cfg: str(cfg.get("column_name", "")) if isinstance(cfg, dict) else "",
                    )
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
        instructions: dict[str, Any] = {}
        if text_instructions:
            instructions["text_instructions"] = text_instructions
        example_question_sqls: list[dict[str, Any]] = []
        for idx, item in enumerate(self.example_question_sqls):
            question = str(item.get("question", "")).strip()
            sql = item.get("sql")
            if not question or not sql:
                continue
            if isinstance(sql, str):
                sql_parts = [sql]
            else:
                sql_parts = [str(part) for part in sql if str(part).strip()]
            example_question_sqls.append(
                {
                    "id": _hex_id("example_question_sql", str(idx), question, "\n".join(sql_parts)),
                    "question": [question],
                    "sql": sql_parts,
                }
            )
        if example_question_sqls:
            example_question_sqls.sort(key=lambda e: str(e.get("id", "")))
            instructions["example_question_sqls"] = example_question_sqls
        if self.sql_snippets:
            instructions["sql_snippets"] = _sort_snippet_groups(self.sql_snippets)
        if instructions:
            payload["instructions"] = instructions

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
        "--catalog",
        default=None,
        help=(
            "Unity Catalog catalog for trusted assets. Defaults to explicitly "
            "exported MIP_DEFAULT_CATALOG, otherwise mip; .env.local catalog "
            "values are intentionally ignored."
        ),
    )
    parser.add_argument(
        "--spec",
        default=str(SPACE_YAML),
        help="Path to the space YAML (default: genie/mortgage_lead_intelligence_space.yml).",
    )
    parser.add_argument(
        "--app-name",
        default=os.environ.get("MIP_APP_NAME") or DEFAULT_APP_NAME,
        help="The App whose governed space this is (it names the pin and the rollback record).",
    )
    parser.add_argument(
        "--pin-scope",
        default=os.environ.get("MIP_APP_ROLLBACK_SECRET_SCOPE") or None,
        help="The App rollback secret scope holding the pin (default: derived from --app-name).",
    )
    parser.add_argument(
        "--lakebase-instance",
        default=os.environ.get("MIP_LAKEBASE_INSTANCE") or DEFAULT_LAKEBASE_INSTANCE,
        help="The App's Lakebase instance, which the signed rollback record names.",
    )
    parser.add_argument(
        "--plan-pin",
        action="store_true",
        help="Read-only: resolve the governed space by its pin and print the plan; no mutation.",
    )
    parser.add_argument(
        "--ack-plan",
        default="",
        help="The sha256 of the read-only pin plan that adopts one unpinned title match.",
    )
    parser.add_argument(
        "--retire-pin",
        action="store_true",
        help="Retire the pin of a governed space that is gone (with --plan-pin: read-only plan).",
    )
    parser.add_argument(
        "--no-pin",
        action="store_true",
        help="Update an acked title match or a pinned space without writing a pin; never creates.",
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

    if any(
        os.environ.get(name, "").strip()
        for name in (
            "MIP_DEPLOYER_DATABRICKS_HOST",
            "MIP_DEPLOYER_DATABRICKS_TOKEN",
            "MIP_DEPLOYER_DATABRICKS_PROFILE",
        )
    ):
        return deployment_workspace_client()
    kwargs: dict[str, Any] = {}
    if args.workspace_host:
        kwargs["host"] = args.workspace_host
    token = os.environ.get("DATABRICKS_TOKEN")
    if token:
        kwargs["token"] = token
    if not token and args.profile:
        kwargs["profile"] = args.profile
    return WorkspaceClient(**kwargs)


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


def _run_smoke_test(client: Any, space_id: str) -> bool:
    """Ask the space a deterministic warm-up question.

    The SDK's ``start_conversation_and_wait`` object can expose the original
    prompt as ``content`` even when no answer attachment was produced. Use the
    same stdlib client the FastAPI app uses and reject an exact question echo
    so this check cannot become a false-positive "Genie works" signal.
    """
    print()
    print(f"smoke-test: asking Genie {DEFAULT_SMOKE_QUESTION!r} (timeout {SMOKE_TIMEOUT_SECONDS}s)...")
    try:
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from backend.services.genie_client import GenieClient

        host = getattr(client.config, "host", None)
        auth_header = (client.config.authenticate() or {}).get("Authorization") or ""
        token = auth_header.removeprefix("Bearer ").strip()
        if not host or not token:
            print("  smoke-test failed: could not resolve host/token from SDK auth", file=sys.stderr)
            return False
        response = GenieClient(
            host=host,
            token=token,
            space_id=space_id,
            timeout_s=SMOKE_TIMEOUT_SECONDS,
        ).ask(DEFAULT_SMOKE_QUESTION)
    except Exception as exc:  # noqa: BLE001
        print(
            f"  smoke-test failed: {exc}",
            file=sys.stderr,
        )
        return False
    answer = (response.answer_text or "").strip()
    if not answer or answer.casefold() == DEFAULT_SMOKE_QUESTION.casefold():
        print("  smoke-test failed: Genie returned no answer beyond the prompt echo", file=sys.stderr)
        return False
    print(f"  answer: {answer[:800]}")
    if response.sql_query:
        print(f"  sql:    {response.sql_query[:800]}")
    return True


def _default_pin_scope(app_name: str) -> str:
    from tools.databricks.app_rollback_secret_scope import expected_app_rollback_scope

    return expected_app_rollback_scope(app_name)


def _print_refusal(exc: genie_space_pin.GeniePinRefusal) -> None:
    if exc.plan is not None:
        print("read-only pin plan (nothing was changed):")
        for line in genie_space_pin.describe_plan(exc.plan):
            print(line)
    print(f"error: {exc}", file=sys.stderr)


def _retire_pin(client: Any, args: argparse.Namespace, *, title: str, scope: str) -> int:
    from tools.databricks import genie_space_pin_retire

    target = {"app_name": args.app_name, "title": title, "scope": scope, "lakebase_instance": args.lakebase_instance}
    try:
        if args.plan_pin:
            plan = genie_space_pin_retire.retire_plan(client, **target)
            for line in genie_space_pin.describe_plan(plan):
                print(line)
            print("  read-only retire plan: nothing was changed")
            return 0
        genie_space_pin_retire.retire(client, ack_plan=args.ack_plan, **target)
    except genie_space_pin.GeniePinRefusal as exc:
        _print_refusal(exc)
        return EXIT_PIN_REFUSED
    except Exception as exc:  # noqa: BLE001 - the class only: an SDK message can carry an id
        print(f"error: the Genie pin could not be retired: {type(exc).__name__}", file=sys.stderr)
        return EXIT_PIN_REFUSED
    print("  retired:       the pin of the gone governed space; the next run creates and pins a new one")
    return 0


def run(args: argparse.Namespace) -> int:
    if args.catalog:
        os.environ["MIP_DEFAULT_CATALOG"] = args.catalog

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
    print(f"  catalog:       {spec.catalog}")
    print(f"  profile:       {args.profile}")
    print(f"  dry-run:       {args.dry_run}")

    if args.dry_run:
        print()
        print("Plan (dry-run, no SDK calls made):")
        print(f"  - Resolve workspace via profile={args.profile!r} (or env).")
        print("  - Resolve the governed space by its pin (the signed pin record or rollback record).")
        print(f"  - No pin and no space titled {target_name!r}: CREATE via genie.create_space() with")
        print("      warehouse_id = --warehouse-id (the bundle's warehouse)")
        print(f"      title        = {target_name!r}")
        print(f"      description  = {spec.description[:60]!r}...")
        print("    then pin the new id.")
        print("  - A pinned space: UPDATE title / description / serialized_space if drift.")
        print("  - An unpinned title match: refuse unless --ack-plan names its pin plan.")
        print(f"  - Write resolved space_id to {SPACE_ID_FILE.relative_to(REPO_ROOT)}")
        if args.retire_pin:
            print("  - --retire-pin: instead retire the pin of a pinned space that is gone (acked plan only).")
        return 0

    try:
        client = _build_client(args)
    except Exception as exc:  # noqa: BLE001 - surface CLI-friendly error
        print(f"error: could not build WorkspaceClient: {exc}", file=sys.stderr)
        return 3

    host = getattr(client.config, "host", None)
    print(f"  workspace:     {host}")

    pin_scope = args.pin_scope or _default_pin_scope(args.app_name)
    if args.retire_pin:
        if args.no_pin:
            print("error: --retire-pin and --no-pin are exclusive; nothing was changed.", file=sys.stderr)
            return EXIT_PIN_REFUSED
        return _retire_pin(client, args, title=target_name, scope=pin_scope)
    try:
        resolution = genie_space_pin.resolve(
            client,
            app_name=args.app_name,
            title=target_name,
            scope=pin_scope,
            lakebase_instance=args.lakebase_instance,
            ack_plan=args.ack_plan,
        )
    except genie_space_pin.GeniePinRefusal as exc:
        _print_refusal(exc)
        return EXIT_PIN_REFUSED
    except Exception as exc:  # noqa: BLE001 - the class only: an SDK message can carry an id
        print(f"error: the governed Genie space pin could not be read: {type(exc).__name__}", file=sys.stderr)
        return EXIT_PIN_REFUSED

    plan = {"use": "UPDATE", "adopt": "UPDATE", "create": "CREATE"}[resolution.action]
    print(f"  plan:          {plan} (governed space from {resolution.source})")
    if args.plan_pin:
        if resolution.plan is not None:
            for line in genie_space_pin.describe_plan({**resolution.plan, "sha256": resolution.plan_sha256}):
                print(line)
        print("  read-only pin plan: nothing was changed")
        return 0
    if args.no_pin:
        if plan == "CREATE":
            print(
                "error: --no-pin never creates a space (a created space is pinned at once); nothing was changed.",
                file=sys.stderr,
            )
            return EXIT_PIN_REFUSED
        resolution = replace(resolution, write_pin=None)
        print("  pin:           not written (--no-pin)")
    if resolution.write_pin is not None:
        # A pin write needs the deploy's signing key pair: prove it before any mutation.
        try:
            genie_space_pin.sign_pin({"probe": True})
        except Exception as exc:  # noqa: BLE001
            print(f"error: the Genie pin cannot be signed ({exc}); nothing was changed.", file=sys.stderr)
            return EXIT_PIN_REFUSED

    tables_bound = False
    space_id = ""

    if plan == "CREATE":
        if not args.warehouse_id:
            print(
                "error: --warehouse-id (the bundle's warehouse) is required to create a space.",
                file=sys.stderr,
            )
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
    else:  # UPDATE the pinned (or just acked) space
        space_id = resolution.space_id or ""
    if space_id and resolution.write_pin is not None:
        try:
            genie_space_pin.write_pin(
                client,
                scope=pin_scope,
                app_name=args.app_name,
                title=target_name,
                space_id=space_id,
                provenance=resolution.write_pin,
                plan_digest=resolution.plan_sha256,
                retired_space_ids=resolution.retired,
            )
        except Exception as exc:  # noqa: BLE001
            print(
                f"error: the governed Genie space could not be pinned ({type(exc).__name__}); the next run "
                "refuses it as an unpinned title match until its plan is acked.",
                file=sys.stderr,
            )
            return EXIT_PIN_UNWRITTEN
        print(f"  pinned:        governed space ({resolution.write_pin})")
    if plan == "UPDATE" and space_id:
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
    if args.no_pin:
        print("The id was not pinned (--no-pin): adopt the install (plan M2-PR6) before a governed deploy.")
    else:
        print("The id is pinned: scripts/deploy.sh and bundle_env resolve it by the pin, never by an input.")

    if not tables_bound:
        print()
        print(
            "note: trusted_assets in the YAML reference a catalog not yet "
            f"materialized ({spec.catalog}). Run `./scripts/deploy.sh -t client` "
            "to create and refresh the governed tables, then re-run this tool "
            "to bind them to the space."
        )

    if args.smoke_test and not _run_smoke_test(client, space_id):
        print(
            "error: Genie smoke-test failed. Re-run with --no-smoke-test only for a "
            "known manual recovery path.",
            file=sys.stderr,
        )
        return 9

    return 0


def main(argv: list[str] | None = None) -> int:
    return run(_parse_args(argv))


if __name__ == "__main__":
    _load_env_local()
    raise SystemExit(main())
