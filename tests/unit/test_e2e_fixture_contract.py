"""Every e2e fixture body validates against the real API response model.

2026-09-21 UI/UX audit, finding ``quality-09`` (step 3). The credential-free
fixture harness (frontend/tests/e2e/fixture) answers every API call from a
typed registry. TypeScript checks those payloads against the frontend's own
hand-written types, which can drift from the backend; this test checks them
against the backend itself, so a fixture UI cannot pass while the real wire
contract has moved.

``tools/export_e2e_fixtures.mjs`` (bare Node, no node_modules) exports every
body the harness serves: each defaultFixtures() handler, invoked with
contractSamples.ts's PARAM_SAMPLES / BODY_SAMPLES / QUERY_SAMPLES, plus every
exported ``contractSamples()`` (contractSamples.ts and any data/*.ts module).
Each sample is then resolved exactly the way Starlette dispatches it: the
path is version-normalized to ``/api/v1``, and the FIRST canonical APIRoute in
``backend.main.app.routes`` whose ``matches()`` is ``Match.FULL`` for the
method and path owns it (so ``/borrowers/search`` beats ``/borrowers/{id}``).
A 2xx body must validate against that route's model in JSON mode, which runs
the model validators (score-band canon, governed identifiers, name-shaped
text, vocabularies). A non-2xx sample must still resolve to a route but is not
validated against a model. A 2xx body may also carry no key its model does
not declare (the w3-api-contract carryover): the model drops such a key, so
the UI could be built on a field the real wire never sends. Nor may it omit a
key the real server sends (quality-09 item 2): FastAPI serializes every
declared field, so ``omitted_keys`` dumps the validated body the way the
route itself serializes it (its own response_model flags; a declared raw 202
in full) and reports what the served body lacks; the partial bodies found
when the check landed sit in the shrink-only KNOWN_OMISSIONS. Every body must
also stay synthetic: masked borrower ids, ``.example`` email domains and the
Summit Mortgage sample lender.

The exporter needs Node >= 22.18. Where Node is missing or older the module
SKIPS, unless ``MIP_REQUIRE_FIXTURE_CONTRACT=1`` (set in CI's backend job),
in which case it FAILS.
"""
from __future__ import annotations

import functools
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import APIRouter
from fastapi.routing import APIRoute
from pydantic import BaseModel, TypeAdapter, ValidationError
from starlette.routing import Match

from backend.main import API_VERSION, app

ROOT = Path(__file__).resolve().parents[2]
EXPORTER = ROOT / "tools" / "export_e2e_fixtures.mjs"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
REQUIRE_FLAG = "MIP_REQUIRE_FIXTURE_CONTRACT"
MIN_NODE = (22, 18)
CANONICAL_PREFIX = f"/api/{API_VERSION}/"

MASKED_BORROWER_ID = re.compile(r"^B-[0-9A-Z]{13}$")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
SAMPLE_LENDER = "Summit Mortgage"

# Samples whose drift is known and owned elsewhere, keyed by exported source.
# SHRINK-ONLY: an entry must still fail validation (test below), so a fix
# retires its entry in the same change. Add one only when fixing the drift
# would change text asserted by a spec another lane owns and no equally valid
# value keeps that text. Shape: {"finding", "owner", "recorded", "why"}.
KNOWN_DRIFT: dict[str, dict[str, str]] = {}

# 2xx samples that carry keys their model does not declare, keyed by exported
# source. SHRINK-ONLY like KNOWN_DRIFT: each entry must still reproduce
# exactly its keys (test below). Shape: {"finding", "owner", "recorded",
# "why", "keys"}, where "keys" is the extras' dotted paths, comma-joined.
KNOWN_EXTRAS: dict[str, dict[str, str]] = {}


def _per_item(count: int, keys: str, overrides: dict[int, str] | None = None) -> str:
    """Comma-joined ``[i].key`` paths for ``count`` list items omitting ``keys``
    (``overrides`` gives an item its own list)."""
    chosen = overrides or {}
    return ",".join(
        f"[{index}].{key}" for index in range(count) for key in chosen.get(index, keys).split(",")
    )


_LEAD_SUMMARY_OMITTED = (
    "approved_at,outreach_at,is_former_customer,is_competitor_lien,second_pos_amount,"
    "has_permit,listing_status_category,listing_status_description,listing_date,"
    "listing_status_date,listing_price,listing_days_on_market,listing_service,"
    "heloc_propensity_score,heloc_propensity_run_date,refi_propensity_score,"
    "refi_propensity_run_date,has_refi_propensity_trigger,current_lender_ref,"
    "last_touch_at,eligible_recontact_at,dnc,assigned_to_email,assigned_to_label,"
    "assigned_at,assignment_expires_at,assignment_status,assignment_id,"
    "latest_disposition_outcome,latest_disposition_at,latest_callback_at,aging_days"
)
_BORROWER_360_OMITTED = (
    "current_lien_balance_low,current_lien_balance_high,ltv_basis_is_unreliable,"
    "situs_cbsa_code,first_pos_loan_type,is_absentee,is_corporate_owner,"
    "has_first_party_relationship,first_party_relationship_depth,"
    "first_party_recent_interactions,first_party_recent_application,"
    "first_party_synthetic_demo"
)
_QUEUE_LAYOUT_ITEM_2 = (
    "approved_at,outreach_at,is_former_customer,is_competitor_lien,second_pos_amount,"
    "has_permit,listing_status_category,listing_status_description,listing_date,"
    "listing_status_date,listing_price,listing_days_on_market,listing_service,"
    "heloc_propensity_score,heloc_propensity_run_date,refi_propensity_score,"
    "refi_propensity_run_date,has_refi_propensity_trigger,current_lender_ref,"
    "last_touch_at,eligible_recontact_at,assigned_at,assignment_expires_at,assignment_id,"
    "latest_callback_at"
)


_OMISSION_WHY = {
    "leads": (
        "LeadSummary rows predate the assignment, listing, propensity and disposition "
        "columns; the real server sends each (null or its default)"
    ),
    "b360": (
        "the Borrower 360 body predates the LeadSummary columns and the lien-band, "
        "CBSA, owner-type and first-party fields the real server always sends"
    ),
    "genie": (
        "Genie answer and refusal builders leave optional response fields out; the real"
        " server serializes every one (null, [] or its default)"
    ),
    "session": (
        "session bodies leave capability and display fields to an implied default; the "
        "real server always sends them"
    ),
    "portfolio": (
        "portfolio bodies predate offers_available / household_summary; the real server"
        " always sends them"
    ),
    "lifecycle": (
        "lifecycle bodies leave the decision ids and timestamps out instead of sending "
        "null"
    ),
    "admin": (
        "admin, data-estate and asset-metadata bodies predate checked_at, "
        "synthetic_demo, catalog_explorer_url and redacted"
    ),
    "proof": (
        "proof body predates fair_lending_note, databricks_sql_url and margins"
    ),
    "outreach": (
        "outreach draft body predates campaign_treatment_fingerprint"
    ),
    "genie_jobs": (
        "job-status bodies leave typical_seconds and the finished answer's optional "
        "fields out; the real server sends each (null or [])"
    ),
}


def _omission(domain: str, keys: str) -> dict[str, str]:
    return {
        "finding": "quality-09 item 2",
        "owner": "w5-wire-types",
        "recorded": "2026-09-30",
        "why": _OMISSION_WHY[domain],
        "keys": keys,
    }


_REFUSED_SUBMIT_OMITTED = (
    "completion_jobs,response.summary,response.sections,response.message_id,"
    "response.elapsed_ms,response.sql_query,response.proof.sql_query,"
    "response.proof.data_freshness,response.proof.reasoning_trace,response.proof.message_id,"
    "response.proof.elapsed_ms,response.proof.generated_at,response.visualization,"
    "response.actions,response.metric_value,response.native_visualization,"
    "response.reasoning_trace,response.genie_status"
)
_REFUSED_TURN_OMITTED = (
    "summary,sections,message_id,elapsed_ms,sql_query,proof.sql_query,proof.data_freshness,"
    "proof.reasoning_trace,proof.message_id,proof.elapsed_ms,proof.generated_at,visualization,"
    "actions,metric_value,native_visualization,reasoning_trace,genie_status"
)

# 2xx samples that OMIT keys the real server always sends (quality-09 item 2),
# keyed by exported source. SHRINK-ONLY: populated once when the check landed
# (2026-09-30) from `node tools/export_e2e_fixtures.mjs --out <file>`; each
# entry must still reproduce exactly its keys (test below), so it is only
# ever narrowed or removed, never added or widened: a new partial body is
# completed in its fixture. Shape: {"finding", "owner", "recorded", "why",
# "keys"}, where "keys" is the omitted dotted paths, comma-joined. The
# domain's P3 PR (w5-wire-types, D-api-types-a4 part a4-ii) types its served
# bodies with the generated presence view and deletes its entries.
KNOWN_OMISSIONS: dict[str, dict[str, str]] = {
    "contractSamples.ts:CONTACTABLE_PORTFOLIO_PREVIEW": _omission("portfolio", "offers_available"),
    "contractSamples.ts:LIVE_SHAPED_HOME_PREVIEW": _omission("portfolio", "offers_available"),
    "contractSamples.ts:MAX_HOME_PREVIEW": _omission("portfolio", "offers_available"),
    "contractSamples.ts:QUEUE_LAYOUT_LEADS": _omission(
        "leads",
        _per_item(24, _LEAD_SUMMARY_OMITTED, {2: _QUEUE_LAYOUT_ITEM_2}),
    ),
    "contractSamples.ts:SIGNED_IN_APPROVER": _omission("session", "lender_name,rum_enabled"),
    "contractSamples.ts:decidedLifecycle(approved)": _omission("lifecycle", "outreach_at"),
    "contractSamples.ts:decidedLifecycle(rejected)": _omission("lifecycle", "outreach_at"),
    "contractSamples.ts:genieAnswerFixture()": _omission(
        "genie",
        (
            "summary,sections,visualization.series,native_visualization,reasoning_trace,"
            "refusal_reason,refusal_report_hash"
        ),
    ),
    "contractSamples.ts:genieDeepAnswerFixture()": _omission(
        "genie",
        (
            "sections[0].visualization.series,sections[0].visualization.reason,"
            "sections[0].narrative_withheld,sections[1].visualization.series,"
            "sections[1].visualization.reason,sections[1].narrative_withheld,visualization.series,"
            "native_visualization,reasoning_trace,refusal_reason,refusal_report_hash"
        ),
    ),
    "contractSamples.ts:portfolioCreated()": _omission("portfolio", "household_summary"),
    "contractSamples.ts:refusedSubmit(instruction_override)": _omission(
        "genie",
        _REFUSED_SUBMIT_OMITTED,
    ),
    "contractSamples.ts:refusedSubmit(out_of_scope)": _omission("genie", _REFUSED_SUBMIT_OMITTED),
    "contractSamples.ts:refusedSubmit(output_policy)": _omission("genie", _REFUSED_SUBMIT_OMITTED),
    "contractSamples.ts:refusedSubmit(outreach_instruction)": _omission(
        "genie",
        _REFUSED_SUBMIT_OMITTED,
    ),
    "contractSamples.ts:refusedSubmit(pii_request)": _omission("genie", _REFUSED_SUBMIT_OMITTED),
    "contractSamples.ts:refusedSubmit(protected_class)": _omission(
        "genie",
        _REFUSED_SUBMIT_OMITTED,
    ),
    "contractSamples.ts:refusedSubmit(scope_bypass)": _omission("genie", _REFUSED_SUBMIT_OMITTED),
    "contractSamples.ts:refusedSubmit(unknown)": _omission("genie", _REFUSED_SUBMIT_OMITTED),
    "contractSamples.ts:refusedSubmit(unreviewed_criterion)": _omission(
        "genie",
        _REFUSED_SUBMIT_OMITTED,
    ),
    "contractSamples.ts:refusedTurn(instruction_override)": _omission(
        "genie",
        _REFUSED_TURN_OMITTED,
    ),
    "contractSamples.ts:refusedTurn(out_of_scope)": _omission("genie", _REFUSED_TURN_OMITTED),
    "contractSamples.ts:refusedTurn(output_policy)": _omission("genie", _REFUSED_TURN_OMITTED),
    "contractSamples.ts:refusedTurn(outreach_instruction)": _omission(
        "genie",
        _REFUSED_TURN_OMITTED,
    ),
    "contractSamples.ts:refusedTurn(pii_request)": _omission("genie", _REFUSED_TURN_OMITTED),
    "contractSamples.ts:refusedTurn(protected_class)": _omission("genie", _REFUSED_TURN_OMITTED),
    "contractSamples.ts:refusedTurn(scope_bypass)": _omission("genie", _REFUSED_TURN_OMITTED),
    "contractSamples.ts:refusedTurn(unknown)": _omission("genie", _REFUSED_TURN_OMITTED),
    "contractSamples.ts:refusedTurn(unreviewed_criterion)": _omission(
        "genie",
        _REFUSED_TURN_OMITTED,
    ),
    "contractSamples.ts:registerGenieTurn submit": _omission("genie", "completion_jobs"),
    "data/genie.ts:genieSessionDetail(GENIE_LINK_HEX_ID)": _omission(
        "genie",
        (
            "turns[0].response.summary,turns[0].response.sections,turns[0].response.elapsed_ms,"
            "turns[0].response.question_hash,turns[0].response.sql_query,turns[0].response.row_count,"
            "turns[0].response.proof,turns[0].response.visualization,turns[0].response.actions,"
            "turns[0].response.metric_value,turns[0].response.table_rows,"
            "turns[0].response.native_visualization,turns[0].response.reasoning_trace,"
            "turns[0].response.refusal_reason,turns[0].response.refusal_report_hash"
        ),
    ),
    "data/genieJobs.ts:data/genieJobs.ts": _omission(
        "genie_jobs",
        (
            "typical_seconds,response.summary,response.sections,response.visualization.series,"
            "response.native_visualization,response.reasoning_trace,response.refusal_reason,"
            "response.refusal_report_hash"
        ),
    ),
    "data/genieReading.ts:data/genieReading.ts": _omission(
        "genie",
        (
            "summary,sections,native_visualization,reasoning_trace,refusal_reason,refusal_report_hash,"
            "sections[0].narrative_withheld,sections[1].narrative_withheld,"
            "sections[2].narrative_withheld,sections[3].narrative_withheld,"
            "sections[4].narrative_withheld,visualization.series"
        ),
    ),
    "data/queuePlace.ts:data/queuePlace.ts#LO_SESSION": _omission(
        "session",
        "actor_display_name,role_labels,lender_name,rum_enabled",
    ),
    "registry:GET /api/admin/assets/:assetKey/metadata": _omission(
        "admin",
        "columns[0].redacted,columns[1].redacted,columns[2].redacted,columns[3].redacted",
    ),
    "registry:GET /api/admin/sources": _omission(
        "admin",
        _per_item(7, "checked_at,synthetic_demo"),
    ),
    "registry:GET /api/borrowers/:id": _omission(
        "b360",
        _LEAD_SUMMARY_OMITTED + "," + _BORROWER_360_OMITTED,
    ),
    "registry:GET /api/borrowers/:id/lifecycle": _omission(
        "lifecycle",
        "approval_id,audit_event_id,approved_at,outreach_at",
    ),
    "registry:GET /api/borrowers/:id/proof": _omission(
        "proof",
        (
            "score_components[0].fair_lending_note,score_components[1].fair_lending_note,"
            "score_components[2].fair_lending_note,score_components[3].fair_lending_note,"
            "reproduce[0].databricks_sql_url,margins"
        ),
    ),
    "registry:GET /api/borrowers/search": _omission("leads", _per_item(8, _LEAD_SUMMARY_OMITTED)),
    "registry:GET /api/data-estate": _omission(
        "admin",
        (
            "lanes[0].assets[0].catalog_explorer_url,lanes[0].assets[0].synthetic_demo,"
            "lanes[0].assets[1].catalog_explorer_url,lanes[0].assets[1].synthetic_demo,"
            "lanes[1].assets[0].catalog_explorer_url,lanes[1].assets[0].synthetic_demo,"
            "lanes[2].assets[0].catalog_explorer_url,lanes[2].assets[0].synthetic_demo,"
            "lanes[2].assets[1].catalog_explorer_url,lanes[2].assets[1].synthetic_demo,"
            "lanes[3].assets[0].catalog_explorer_url"
        ),
    ),
    "registry:GET /api/leads": _omission("leads", _per_item(24, _LEAD_SUMMARY_OMITTED)),
    "registry:GET /api/session": _omission("session", "actor_display_name,role_labels"),
    "registry:POST /api/portfolio/preview": _omission("portfolio", "offers_available"),
}


class ContractError(LookupError):
    """A sample the contract cannot place on a route or a model."""


def _canonical_path(path: str) -> str:
    """``/api/x`` and ``/api/v2/x`` both dispatch as ``/api/<API_VERSION>/x``."""
    return re.sub(r"^/api(?:/v\d+)?(?=/|$)", f"/api/{API_VERSION}", path)


def _canonical_routes() -> list[APIRoute]:
    return [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith(CANONICAL_PREFIX)
    ]


def resolve_route(method: str, path: str, routes: list[APIRoute] | None = None) -> APIRoute:
    """The first canonical APIRoute that FULLY matches, in dispatch order."""
    canonical = _canonical_path(path)
    scope = {"type": "http", "method": method, "path": canonical, "root_path": ""}
    for route in routes if routes is not None else _canonical_routes():
        match, _child = route.matches(scope)
        if match == Match.FULL:
            return route
    raise ContractError(f"no API route answers {method} {canonical}")


def response_model_for(route: APIRoute, status: int) -> Any:
    """The model a 2xx ``status`` must satisfy on ``route``.

    A status declared in ``responses={status: {"model": ...}}`` (for example a
    202 job receipt) uses that model; otherwise the status must be the route's
    declared ``status_code`` (200 when unset) and the route's response_model.
    """
    declared = route.responses.get(status) or route.responses.get(str(status)) or {}
    if isinstance(declared, dict) and declared.get("model") is not None:
        return declared["model"]
    expected = route.status_code or 200
    if status != expected:
        raise ContractError(
            f"status {status} is not declared by {route.path} (declared status {expected})"
        )
    if route.response_model is None:
        raise ContractError(f"{route.path} declares no response model for {status}")
    return route.response_model


@functools.cache
def _adapter(model: Any) -> TypeAdapter[Any]:
    return TypeAdapter(model)


def synthetic_problems(node: Any, where: str, key: str | None = None) -> list[str]:
    """Fixture data stays synthetic: masked ids, .example emails, the sample lender."""
    problems: list[str] = []
    if isinstance(node, dict):
        for child_key, child in node.items():
            problems.extend(synthetic_problems(child, where, str(child_key)))
        return problems
    if isinstance(node, list):
        for child in node:
            problems.extend(synthetic_problems(child, where, key))
        return problems
    if not isinstance(node, str):
        return problems
    is_borrower_id = key is not None and (key.endswith("borrower_id") or key == "borrower_ids")
    if is_borrower_id and not MASKED_BORROWER_ID.match(node):
        problems.append(f"{where}: {key} {node!r} is not a masked B-[0-9A-Z]{{13}} id")
    if key == "lender_name" and node != SAMPLE_LENDER:
        problems.append(f"{where}: lender_name {node!r} is not the sample lender {SAMPLE_LENDER!r}")
    for email in EMAIL.findall(node):
        if not email.lower().endswith(".example"):
            problems.append(f"{where}: email {email!r} is not on a reserved .example domain")
    return problems


def _extra_paths(body: Any, by_alias: Any, by_name: Any, path: str) -> list[str]:
    """Keys in ``body`` that neither dump of the validated model carries.

    A dict key present in either dump is declared (or allowed: a
    ``dict[str, ...]`` field keeps every key, and so does ``extra="allow"``)
    and is walked into; lists are zipped by index.
    """
    if isinstance(body, dict):
        alias_map = by_alias if isinstance(by_alias, dict) else {}
        name_map = by_name if isinstance(by_name, dict) else {}
        extras: list[str] = []
        for key, child in body.items():
            where = f"{path}.{key}" if path else str(key)
            if key not in alias_map and key not in name_map:
                extras.append(where)
                continue
            extras.extend(_extra_paths(child, alias_map.get(key), name_map.get(key), where))
        return extras
    if isinstance(body, list):
        alias_list = by_alias if isinstance(by_alias, list) else []
        name_list = by_name if isinstance(by_name, list) else []
        return [
            extra
            for index, child in enumerate(body)
            for extra in _extra_paths(
                child,
                alias_list[index] if index < len(alias_list) else None,
                name_list[index] if index < len(name_list) else None,
                f"{path}[{index}]",
            )
        ]
    return []


def undeclared_keys(body: Any, model: Any) -> list[str]:
    """Dotted paths of the ``body`` keys ``model`` does not declare (validates first)."""
    adapter = _adapter(model)
    validated = adapter.validate_json(json.dumps(body))
    by_alias = adapter.dump_python(validated, mode="json", by_alias=True)
    by_name = adapter.dump_python(validated, mode="json", by_alias=False)
    return _extra_paths(body, by_alias, by_name, "")


def _served_by_route_model(route: APIRoute, status: int) -> bool:
    """True when ``status`` is serialized through the route's own response_model
    (``response_model_for`` did not take ``route.responses[status]['model']``)."""
    declared = route.responses.get(status) or route.responses.get(str(status)) or {}
    return not (isinstance(declared, dict) and declared.get("model") is not None)


def _missing_paths(body: Any, dumped: Any, path: str) -> list[str]:
    """Dotted paths the server's dump carries and ``body`` does not.

    Lists are zipped by index. A ``dict[str, X]`` field's dump has exactly
    the body's keys (it was validated from the body), so its keys are never
    reported, only walked.
    """
    if isinstance(dumped, dict) and isinstance(body, dict):
        missing: list[str] = []
        for key, child in dumped.items():
            where = f"{path}.{key}" if path else str(key)
            if key not in body:
                missing.append(where)
                continue
            missing.extend(_missing_paths(body[key], child, where))
        return missing
    if isinstance(dumped, list) and isinstance(body, list):
        return [
            item
            for index, (child, dumped_child) in enumerate(zip(body, dumped, strict=False))
            for item in _missing_paths(child, dumped_child, f"{path}[{index}]")
        ]
    return []


def omitted_keys(body: Any, model: Any, route: APIRoute, status: int) -> list[str]:
    """Dotted paths the real server would send for this route and status but ``body`` omits.

    The dump mirrors the route's own serialization (quality-09 item 2): a status
    served through the route's response_model uses the route's flags (by_alias,
    exclude_unset, exclude_defaults, exclude_none, include, exclude), so the
    /health exclude_unset route reports nothing a body left unset; a declared
    raw status (the genie 202) is dumped in full by alias, which
    test_api_types_generated.py (c) proves is what the server sends.
    ``exclude=True`` fields are never dumped and so never reported.
    """
    adapter = _adapter(model)
    validated = adapter.validate_json(json.dumps(body))
    if _served_by_route_model(route, status):
        dumped = adapter.dump_python(
            validated,
            mode="json",
            by_alias=route.response_model_by_alias,
            exclude_unset=route.response_model_exclude_unset,
            exclude_defaults=route.response_model_exclude_defaults,
            exclude_none=route.response_model_exclude_none,
            include=route.response_model_include,
            exclude=route.response_model_exclude,
        )
    else:
        dumped = adapter.dump_python(validated, mode="json", by_alias=True)
    return _missing_paths(body, dumped, "")


def contract_problems(sample: dict[str, Any], routes: list[APIRoute] | None = None) -> list[str]:
    """Why ``sample`` breaks the contract; empty when it holds (or is non-2xx)."""
    method, path, status = sample["method"], sample["path"], int(sample["status"])
    where = f"{sample['source']} [{method} {path}{'?' + sample['query'] if sample.get('query') else ''} -> {status}]"
    problems = synthetic_problems(sample["body"], where)
    try:
        route = resolve_route(method, path, routes)
    except ContractError as exc:
        return [*problems, f"{where}: {exc}"]
    if not 200 <= status < 300:
        return problems
    try:
        model = response_model_for(route, status)
    except ContractError as exc:
        return [*problems, f"{where}: {exc}"]
    try:
        extras = undeclared_keys(sample["body"], model)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
            for error in exc.errors()[:6]
        )
        problems.append(f"{where}: fails {getattr(model, '__name__', model)}: {details}")
        return problems
    known = KNOWN_EXTRAS.get(sample["source"], {}).get("keys", "").split(",")
    unknown = [extra for extra in extras if extra not in known]
    if unknown:
        problems.append(
            f"{where}: {getattr(model, '__name__', model)} does not declare {', '.join(unknown)}"
            " (the real response drops it)"
        )
    known_omitted = KNOWN_OMISSIONS.get(sample["source"], {}).get("keys", "").split(",")
    omitted = [
        item for item in omitted_keys(sample["body"], model, route, status) if item not in known_omitted
    ]
    if omitted:
        problems.append(
            f"{where}: {getattr(model, '__name__', model)} omits {', '.join(omitted)}"
            " (the real server always sends them)"
        )
    return problems


def _node_version(node: str) -> tuple[int, int] | None:
    completed = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=30, check=False)
    match = re.match(r"v(\d+)\.(\d+)", completed.stdout.strip())
    return (int(match.group(1)), int(match.group(2))) if match else None


def _node_or_skip() -> str:
    required = os.environ.get(REQUIRE_FLAG) == "1"
    node = shutil.which("node")
    version = _node_version(node) if node else None
    if node and version and version >= MIN_NODE:
        return node
    reason = (
        f"Node >= {MIN_NODE[0]}.{MIN_NODE[1]} is required to export the e2e fixtures "
        f"(found {'none' if not node else version})"
    )
    if required:
        pytest.fail(f"{reason}; {REQUIRE_FLAG}=1 makes this a failure, not a skip")
    pytest.skip(reason)


@pytest.fixture(scope="module")
def node() -> str:
    return _node_or_skip()


@pytest.fixture(scope="module")
def samples(node: str, tmp_path_factory: pytest.TempPathFactory) -> list[dict[str, Any]]:
    out = tmp_path_factory.mktemp("fixture-contract") / "samples.json"
    completed = subprocess.run(
        [node, str(EXPORTER), "--out", str(out)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, f"exporter failed:\n{completed.stderr}"
    exported = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(exported, list) and exported, "the exporter wrote no samples"
    return exported


def test_every_fixture_body_matches_the_backend_contract(samples: list[dict[str, Any]]) -> None:
    problems = [
        problem
        for sample in samples
        if sample["source"] not in KNOWN_DRIFT
        for problem in contract_problems(sample)
    ]
    assert problems == [], "fixture bodies drifted from the backend contract:\n" + "\n".join(problems)


def test_every_default_fixture_yields_a_validated_2xx_sample(samples: list[dict[str, Any]]) -> None:
    registered = {
        (sample["method"], sample["pattern"]) for sample in samples if sample["source"].startswith("registry:")
    }
    validated = {
        (sample["method"], sample["pattern"])
        for sample in samples
        if 200 <= int(sample["status"]) < 300 and not contract_problems(sample)
    }
    assert registered, "no defaultFixtures() entries were exported"
    assert sorted(registered - validated) == []


def test_known_drift_is_shrink_only(samples: list[dict[str, Any]]) -> None:
    by_source = {sample["source"]: sample for sample in samples}
    for source, entry in KNOWN_DRIFT.items():
        assert {"finding", "owner", "recorded", "why"} <= entry.keys(), source
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry["recorded"]), source
        assert source in by_source, f"KNOWN_DRIFT names {source!r}, which the exporter no longer emits"
        assert contract_problems(by_source[source]), f"KNOWN_DRIFT {source!r} no longer reproduces: remove it"


def _sample_omissions(sample: dict[str, Any]) -> list[str]:
    status = int(sample["status"])
    if not 200 <= status < 300:
        return []
    route = resolve_route(sample["method"], sample["path"])
    return omitted_keys(sample["body"], response_model_for(route, status), route, status)


def test_known_omissions_is_shrink_only(samples: list[dict[str, Any]]) -> None:
    by_source: dict[str, list[dict[str, Any]]] = {}
    for sample in samples:
        by_source.setdefault(sample["source"], []).append(sample)
    for source, entry in KNOWN_OMISSIONS.items():
        assert {"finding", "owner", "recorded", "why", "keys"} <= entry.keys(), source
        assert entry["finding"] == "quality-09 item 2", source
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry["recorded"]), source
        assert source in by_source, f"KNOWN_OMISSIONS names {source!r}, which the exporter no longer emits"
        reproduced = list(dict.fromkeys(path for sample in by_source[source] for path in _sample_omissions(sample)))
        assert reproduced == entry["keys"].split(","), (
            f"KNOWN_OMISSIONS {source!r} no longer reproduces exactly its keys: narrow or remove it"
        )


def test_known_extras_is_shrink_only(samples: list[dict[str, Any]]) -> None:
    by_source = {sample["source"]: sample for sample in samples}
    for source, entry in KNOWN_EXTRAS.items():
        assert {"finding", "owner", "recorded", "why", "keys"} <= entry.keys(), source
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry["recorded"]), source
        assert source in by_source, f"KNOWN_EXTRAS names {source!r}, which the exporter no longer emits"
        sample = by_source[source]
        model = response_model_for(resolve_route(sample["method"], sample["path"]), int(sample["status"]))
        assert undeclared_keys(sample["body"], model) == entry["keys"].split(","), (
            f"KNOWN_EXTRAS {source!r} no longer reproduces exactly {entry['keys']!r}: shrink or remove it"
        )


# --- Non-vacuity: the validator rejects each kind of break -------------------


def _valid_session_sample() -> dict[str, Any]:
    return {
        "source": "non-vacuity",
        "method": "GET",
        "pattern": "/api/genie/sessions",
        "path": "/api/genie/sessions",
        "query": "",
        "status": 200,
        "body": {
            "sessions": [
                {"conversation_id": "c-1", "title": "t", "last_activity_at": None, "turn_count": 1}
            ]
        },
    }


def test_the_validator_accepts_a_valid_sample() -> None:
    assert contract_problems(_valid_session_sample()) == []


def test_the_validator_rejects_a_missing_required_field() -> None:
    sample = _valid_session_sample()
    del sample["body"]["sessions"][0]["conversation_id"]
    assert any("conversation_id" in problem for problem in contract_problems(sample))


def test_the_validator_rejects_an_unknown_path() -> None:
    sample = {**_valid_session_sample(), "path": "/api/genie/no-such-endpoint"}
    assert contract_problems(sample) == [
        f"non-vacuity [GET /api/genie/no-such-endpoint -> 200]: no API route answers GET /api/{API_VERSION}/genie/no-such-endpoint"
    ]


def test_the_validator_rejects_a_wrong_method() -> None:
    sample = {**_valid_session_sample(), "method": "DELETE"}
    assert any("no API route answers DELETE" in problem for problem in contract_problems(sample))


def test_the_validator_rejects_an_undeclared_2xx_status() -> None:
    sample = {**_valid_session_sample(), "status": 201}
    assert any("status 201 is not declared" in problem for problem in contract_problems(sample))


def test_the_validator_rejects_a_top_level_undeclared_key() -> None:
    sample = _valid_session_sample()
    sample["body"]["app_env"] = "fixture"
    assert contract_problems(sample) == [
        "non-vacuity [GET /api/genie/sessions -> 200]: GenieSessionListResponse does not declare app_env"
        " (the real response drops it)"
    ]


def test_the_validator_rejects_an_undeclared_key_inside_a_list_item() -> None:
    sample = _valid_session_sample()
    sample["body"]["sessions"][0]["pinned"] = True
    assert any("does not declare sessions[0].pinned" in problem for problem in contract_problems(sample))


def test_a_dict_field_accepts_arbitrary_keys() -> None:
    # HealthResponse.dependencies is dict[str, str]: any dependency name is declared.
    model = response_model_for(resolve_route("GET", "/api/health"), 200)
    body = {"status": "ok", "mode": "live", "dependencies": {"warehouse": "up", "any_new_dependency": "up"}}
    assert undeclared_keys(body, model) == []
    assert undeclared_keys({**body, "app_env": "fixture"}, model) == ["app_env"]


def test_the_validator_rejects_an_unmasked_borrower_id() -> None:
    sample = {
        **_valid_session_sample(),
        "body": {"sessions": [], "borrower_id": "borrower-42"},
    }
    assert any("is not a masked" in problem for problem in contract_problems(sample))


def test_a_non_2xx_sample_skips_the_model_but_not_routing_or_the_synthetic_guard() -> None:
    # A body GenieSessionListResponse rejects (sessions must be a list).
    down = {**_valid_session_sample(), "status": 503, "body": {"sessions": "fixture: history is down"}}
    assert contract_problems({**down, "status": 200}), "control: the same body as a 2xx must fail the model"
    assert contract_problems(down) == []
    assert any(
        "no API route answers" in problem
        for problem in contract_problems({**down, "path": "/api/genie/no-such-endpoint"})
    )
    leaking = {**down, "body": {"sessions": "fixture", "borrower_id": "borrower-42"}}
    assert any("is not a masked" in problem for problem in contract_problems(leaking))


def test_the_synthetic_guard_rejects_real_emails_and_other_lenders() -> None:
    problems = synthetic_problems(
        {"owner": "Reach jane@realbank.com", "lender_name": "Another Bank", "ok": "a@summit.example"},
        "non-vacuity",
    )
    assert problems == [
        "non-vacuity: email 'jane@realbank.com' is not on a reserved .example domain",
        "non-vacuity: lender_name 'Another Bank' is not the sample lender 'Summit Mortgage'",
    ]


def test_dispatch_order_resolves_the_static_segment_first() -> None:
    assert resolve_route("GET", "/api/borrowers/search").path == f"/api/{API_VERSION}/borrowers/search"
    assert resolve_route("GET", "/api/borrowers/B-0000000000000").path == f"/api/{API_VERSION}/borrowers/{{borrower_id}}"


class _JobReceipt(BaseModel):
    job_id: str
    state: str = "queued"


class _Answer(BaseModel):
    answer: str


def _declared_202_route() -> APIRoute:
    router = APIRouter()

    @router.post(
        f"/api/{API_VERSION}/non-vacuity/jobs",
        response_model=_Answer,
        responses={202: {"model": _JobReceipt}},
    )
    def _jobs() -> _Answer:  # pragma: no cover - never served
        return _Answer(answer="")

    route = router.routes[0]
    assert isinstance(route, APIRoute)
    return route


def test_a_non_default_2xx_status_validates_against_its_declared_model() -> None:
    route = _declared_202_route()
    base = {"source": "non-vacuity", "method": "POST", "pattern": "/api/non-vacuity/jobs", "path": "/api/non-vacuity/jobs", "query": ""}

    assert contract_problems({**base, "status": 202, "body": {"job_id": "j-1", "state": "queued"}}, [route]) == []
    assert contract_problems({**base, "status": 200, "body": {"answer": "ok"}}, [route]) == []
    wrong_model = contract_problems({**base, "status": 202, "body": {"answer": "ok"}}, [route])
    assert any("fails _JobReceipt" in problem and "job_id" in problem for problem in wrong_model)
    undeclared = contract_problems({**base, "status": 203, "body": {"job_id": "j-1"}}, [route])
    assert any("status 203 is not declared" in problem for problem in undeclared)


def test_a_declared_202_body_missing_a_defaulted_key_is_rejected() -> None:
    """A declared raw status is dumped in full by alias: the genie 202 sends every field."""
    route = _declared_202_route()
    base = {"source": "non-vacuity", "method": "POST", "pattern": "/api/non-vacuity/jobs", "path": "/api/non-vacuity/jobs", "query": ""}
    assert contract_problems({**base, "status": 202, "body": {"job_id": "j-1"}}, [route]) == [
        "non-vacuity [POST /api/non-vacuity/jobs -> 202]: _JobReceipt omits state"
        " (the real server always sends them)"
    ]


# --- Non-vacuity: served 2xx bodies are complete (quality-09 item 2) ---------


def test_the_validator_rejects_a_top_level_omission() -> None:
    sample = {**_valid_session_sample(), "body": {}}
    assert contract_problems(sample) == [
        "non-vacuity [GET /api/genie/sessions -> 200]: GenieSessionListResponse omits sessions"
        " (the real server always sends them)"
    ]


def test_the_validator_rejects_an_omission_inside_a_list_item() -> None:
    sample = _valid_session_sample()
    del sample["body"]["sessions"][0]["title"]
    assert any("omits sessions[0].title" in problem for problem in contract_problems(sample))


def _synthetic_route(model: Any) -> APIRoute:
    router = APIRouter()

    @router.get(f"/api/{API_VERSION}/non-vacuity/model", response_model=model)
    def _served() -> None:  # pragma: no cover - never served
        return None

    route = router.routes[0]
    assert isinstance(route, APIRoute)
    return route


def test_an_exclude_unset_route_reports_nothing_a_body_left_unset() -> None:
    route = resolve_route("GET", "/api/health")
    assert route.response_model_exclude_unset, "non-vacuity: /health is serialized with exclude_unset"
    model = response_model_for(route, 200)
    body = {"status": "ok", "mode": "live"}
    assert omitted_keys(body, model, route, 200) == []
    unflagged = omitted_keys(body, model, _synthetic_route(model), 200)
    assert "dependencies" in unflagged, "control: the same body on an unflagged route omits keys"


class _Labels(BaseModel):
    labels: dict[str, str]


def test_a_dict_field_with_arbitrary_keys_omits_nothing() -> None:
    route = _synthetic_route(_Labels)
    assert omitted_keys({"labels": {"a": "1", "any-new-key": "2"}}, _Labels, route, 200) == []
    assert omitted_keys({"labels": {}}, _Labels, route, 200) == []


def test_an_excluded_field_is_never_reported(samples: list[dict[str, Any]]) -> None:
    from backend.schemas.lead import LeadSummary

    assert LeadSummary.model_fields["row_refreshed_at"].exclude is True, "non-vacuity: the field is exclude=True"
    leads = next(sample for sample in samples if sample["source"] == "registry:GET /api/leads")
    row = {key: value for key, value in leads["body"][0].items() if key != "row_refreshed_at"}
    route = resolve_route("GET", "/api/leads")
    omitted = omitted_keys([row], response_model_for(route, 200), route, 200)
    assert omitted, "non-vacuity: the served row omits other keys"
    assert not any(path.endswith("row_refreshed_at") for path in omitted)


def test_a_route_without_a_response_model_fails_closed() -> None:
    router = APIRouter()

    @router.get(f"/api/{API_VERSION}/non-vacuity/untyped", response_model=None)
    def _untyped() -> dict[str, str]:  # pragma: no cover - never served
        return {}

    route = router.routes[0]
    assert isinstance(route, APIRoute)
    sample = {"source": "non-vacuity", "method": "GET", "pattern": "/api/non-vacuity/untyped", "path": "/api/non-vacuity/untyped", "query": "", "status": 200, "body": {}}
    assert any("declares no response model" in problem for problem in contract_problems(sample, [route]))


def test_the_exporter_names_the_pattern_of_an_unknown_param(node: str) -> None:
    script = (
        f"import {{ substitutePattern }} from {json.dumps(EXPORTER.as_uri())};"
        "try { substitutePattern('/api/things/:nope', {}); process.exit(0); }"
        "catch (error) { console.error(error.message); process.exit(3); }"
    )
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60, check=False
    )
    assert completed.returncode == 3
    assert ":nope" in completed.stderr and "/api/things/:nope" in completed.stderr


FIXTURE_REL = Path("frontend", "tests", "e2e", "fixture")
# What the exporter loads, and the package.json that makes the .ts modules ESM.
EXPORTER_CLOSURE = (
    FIXTURE_REL / "registry.ts",
    FIXTURE_REL / "mockApi.ts",
    FIXTURE_REL / "contractSamples.ts",
    FIXTURE_REL / "data",
    Path("frontend", "package.json"),
)


def test_the_exporter_runs_on_the_fixture_modules_alone(
    node: str, samples: list[dict[str, Any]], tmp_path: Path
) -> None:
    """CI's backend job has no node_modules, and the exporter must not load
    frontend/src: run it on a copy of the modules it loads and nothing else.
    A package or src module that survives type stripping (for example an
    all-inline ``import { type X } from '@playwright/test'``) fails here with
    ERR_MODULE_NOT_FOUND even where a developer's node_modules would hide it."""
    isolated = tmp_path / "isolated"
    for relative in (Path("tools", EXPORTER.name), *EXPORTER_CLOSURE):
        source, target = ROOT / relative, isolated / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    reachable = [parent / "node_modules" for parent in (isolated, *isolated.parents) if (parent / "node_modules").exists()]
    assert reachable == [], f"the isolated copy can still resolve packages from {reachable}"
    assert not (isolated / "frontend" / "src").exists()

    out = tmp_path / "isolated.json"
    completed = subprocess.run(
        [node, str(isolated / "tools" / EXPORTER.name), "--out", str(out)],
        cwd=isolated,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, (
        "the exporter needs more than the fixture modules (a runtime import of a package or of "
        f"frontend/src survived type stripping):\n{completed.stderr}"
    )
    assert json.loads(out.read_text(encoding="utf-8")) == samples


def test_the_exporter_collects_data_module_contract_samples(samples: list[dict[str, Any]]) -> None:
    """Step 3 of the exporter (a data/*.ts module's own contractSamples()) is exercised by real data."""
    sources = {sample["source"] for sample in samples}
    assert "data/genie.ts:GENIE_HISTORY_SESSIONS" in sources


# --- CI pin -------------------------------------------------------------------


def test_ci_runs_this_contract_with_node_and_the_require_flag() -> None:
    jobs = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    steps = jobs["backend-tests"]["steps"]
    assert any(str(step.get("uses", "")).startswith("actions/setup-node@") for step in steps), (
        "backend-tests must install Node so the fixture exporter runs"
    )
    pytest_steps = [step for step in steps if "pytest" in str(step.get("run", ""))]
    assert pytest_steps, "backend-tests has no pytest step"
    assert any(step.get("env", {}).get(REQUIRE_FLAG) == "1" for step in pytest_steps), (
        f"backend-tests' pytest step must set {REQUIRE_FLAG}=1 so a missing Node fails, not skips"
    )
