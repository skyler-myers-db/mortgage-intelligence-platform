"""Live endpoint verifier for the deployed Mortgage Intelligence Platform app.

Probes each production API endpoint using an OAuth bearer token obtained via
`databricks auth token -p DEFAULT`, then reports status, latency, and a
lightweight payload sanity check.

Usage:
    python tools/verify_live.py
    python tools/verify_live.py --base-url https://mip-app-2543889327043640.aws.databricksapps.com

Idempotent: write probes use real borrower IDs discovered from `/api/leads`.
Unknown-borrower probes use synthetic B-TEST ids only as 404 negative checks,
so the verifier never creates phantom borrowers in Lakebase or gold mirrors.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Make the repo importable when this script is run directly.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.verify_live_geo_drill import (  # noqa: E402
    COUNTY_PROBE,
    EMPTY_ALLOWED_PROBES,
    STATE_PROBE,
    ZIP_PROBE,
    geo_drill_flags,
    pick_drill_state,
    pick_drill_zip,
)
from tools.verify_live_rate_sensitivity import rate_sensitivity_flags  # noqa: E402
from tools.verify_live_source_readiness import (  # noqa: E402
    DEFAULT_MAX_SOURCE_AGE_DAYS,
    _source_readiness_flags,
)

DEFAULT_BASE = "https://mip-app-2543889327043640.aws.databricksapps.com"


def _report_safe_sample(value: Any) -> Any:
    """Return a report-only copy with dynamic geography copy.

    The live API is allowed to return exact counts and scope labels, but
    validation artifacts are release-gate inputs. Keep numeric fields intact
    and avoid pinning current Cotality footprint wording in markdown.
    """
    if isinstance(value, dict):
        return {k: _report_safe_sample(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_report_safe_sample(item) for item in value]
    if isinstance(value, str):
        text = re.sub(r"\bAll\s+\d+\s+states\b", "All available states", value)
        text = re.sub(
            r"\b\d+\s+counties\s+across\s+\d+\s+states\b",
            "available counties across available states",
            text,
        )
        text = re.sub(
            r";\s*\d+\s+county\s+available\s+in\s+([A-Z]{2})\b",
            r"; available counties in \1",
            text,
        )
        return text
    return value


def get_oauth_token(profile: str = "DEFAULT") -> str:
    result = subprocess.run(
        ["databricks", "auth", "token", "-p", profile],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    return payload["access_token"]


@dataclass
class ProbeResult:
    name: str
    method: str
    path: str
    status: int | None = None
    latency_ms: float | None = None
    ok: bool = False
    notes: str = ""
    sample: Any = None
    raw_len: int = 0
    error: str | None = None
    top_keys: list[str] = field(default_factory=list)


def probe(
    base: str,
    token: str,
    name: str,
    method: str,
    path: str,
    body: dict | None = None,
    timeout: float = 30.0,
    extra_headers: dict[str, str] | None = None,
    expect_status: int | None = None,
) -> ProbeResult:
    url = base.rstrip("/") + path
    data = None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            latency = (time.perf_counter() - t0) * 1000
            status = resp.status
    except urllib.error.HTTPError as exc:
        latency = (time.perf_counter() - t0) * 1000
        raw = exc.read() if exc.fp else b""
        status = exc.code
        # If the caller explicitly expected this status (e.g. a 403 on
        # the RBAC negative-probe), a match is a PASS, not a failure.
        matched_expectation = expect_status is not None and status == expect_status
        return ProbeResult(
            name=name,
            method=method,
            path=path,
            status=status,
            latency_ms=latency,
            ok=matched_expectation,
            raw_len=len(raw),
            error=(
                None if matched_expectation
                else raw[:500].decode("utf-8", errors="replace")
            ),
        )
    except Exception as exc:  # noqa: BLE001
        latency = (time.perf_counter() - t0) * 1000
        return ProbeResult(
            name=name,
            method=method,
            path=path,
            status=None,
            latency_ms=latency,
            ok=False,
            error=str(exc)[:500],
        )

    text = raw.decode("utf-8", errors="replace")
    parsed: Any
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return ProbeResult(
            name=name,
            method=method,
            path=path,
            status=status,
            latency_ms=latency,
            ok=False,
            raw_len=len(raw),
            error=f"non-JSON body, first 200 chars: {text[:200]!r}",
        )

    top_keys: list[str] = []
    sample: Any = None
    if isinstance(parsed, dict):
        top_keys = sorted(parsed.keys())
        sample = parsed
    elif isinstance(parsed, list):
        top_keys = [f"[array len={len(parsed)}]"]
        sample = parsed if name == "admin.sources" else parsed[0] if parsed else []

    # When the caller declared an explicit expectation, honour it on the
    # 2xx path too — not just the HTTPError branch. Previously a probe
    # with expect_status=403 that actually returned 200 would silently
    # pass (the 2xx-default branch didn't consult expect_status), masking
    # a real RBAC regression.
    ok = (
        status == expect_status
        if expect_status is not None
        else 200 <= status < 300
    )
    return ProbeResult(
        name=name,
        method=method,
        path=path,
        status=status,
        latency_ms=latency,
        ok=ok,
        raw_len=len(raw),
        top_keys=top_keys,
        sample=sample,
    )


def get_json(base: str, token: str, path: str, timeout: float = 30.0) -> Any:
    req = urllib.request.Request(
        base.rstrip("/") + path,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 -- controlled URL
        return json.loads(resp.read())


def run_probes(base: str, token: str) -> list[ProbeResult]:
    unknown_uuid_approve = f"B-TEST-{uuid.uuid4().hex[:8].upper()}"
    unknown_uuid_reject = f"B-TEST-{uuid.uuid4().hex[:8].upper()}"

    results: list[ProbeResult] = []

    # 1. Health
    results.append(probe(base, token, "health", "GET", "/api/health"))
    results.append(probe(base, token, "config.options", "GET", "/api/config/options"))

    geography_label = "All"
    try:
        config_options = get_json(base, token, "/api/config/options")
        geographies = config_options.get("geographies") if isinstance(config_options, dict) else None
        if isinstance(geographies, list) and geographies:
            geography_label = str(geographies[0])
    except Exception:  # noqa: BLE001 -- verifier falls back to the broad accepted alias
        geography_label = "All"

    # 2. Portfolio preview — unfiltered vs filtered
    results.append(
        probe(base, token, "portfolio.unfiltered", "POST", "/api/portfolio/preview", body={"criteria": {}})
    )
    results.append(
        probe(
            base,
            token,
            "portfolio.all_states",
            "POST",
            "/api/portfolio/preview",
            body={"criteria": {"geography": geography_label}},
        )
    )
    results.append(
        probe(
            base,
            token,
            "portfolio.all_states.owner.25pct",
            "POST",
            "/api/portfolio/preview",
            body={
                "criteria": {
                    "geography": geography_label,
                    "occupancy": "Owner-occupied",
                    "min_equity_pct_label": "≥ 25%",
                }
            },
        )
    )

    # 3. Segments + leads
    results.append(probe(base, token, "segments", "GET", "/api/segments"))
    results.append(probe(base, token, "leads.all", "GET", "/api/leads"))
    results.append(probe(base, token, "leads.itm", "GET", "/api/leads?segment=itm"))

    # 4. Pick a real borrower id from /api/leads (raw fetch since the sample
    # recorded above is truncated). Using the same bearer token. If the fetch
    # fails we skip the borrower-dependent probes entirely — a failure there
    # says nothing about the app when the fake id doesn't exist.
    borrower_ids: list[str] = []
    try:
        leads_req = urllib.request.Request(
            base.rstrip("/") + "/api/leads",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(leads_req, timeout=30) as resp:  # noqa: S310 -- controlled URL
            body_bytes = resp.read()
        payload = json.loads(body_bytes)
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            borrower_ids = [
                str(row.get("borrower_id"))
                for row in payload
                if isinstance(row, dict) and row.get("borrower_id")
            ]
        elif isinstance(payload, dict):
            rows = payload.get("rows") or payload.get("leads") or payload.get("items") or []
            if rows:
                borrower_ids = [
                    str(row.get("borrower_id"))
                    for row in rows
                    if isinstance(row, dict) and row.get("borrower_id")
                ]
    except Exception:  # noqa: BLE001 -- diagnostic path; don't blow up the whole script
        borrower_ids = []

    borrower_ids = list(dict.fromkeys(borrower_ids))
    borrower_id = borrower_ids[0] if borrower_ids else None
    reject_borrower_id = borrower_ids[1] if len(borrower_ids) > 1 else borrower_id

    if not borrower_id:
        results.append(
            ProbeResult(
                name="borrower.pick",
                method="INFO",
                path="/api/leads",
                status=0,
                latency_ms=0.0,
                ok=False,
                top_keys=[],
                error="no real borrower_id available from /api/leads; borrower-dependent probes not proven",
            )
        )

    if borrower_id:
        results.append(probe(base, token, "borrower.detail", "GET", f"/api/borrowers/{borrower_id}"))
        results.append(probe(base, token, "borrower.evidence", "GET", f"/api/borrowers/{borrower_id}/evidence"))

        # 5. Offers & outreach for real borrower
        results.append(
            probe(base, token, "offers.recommend", "POST", "/api/offers/recommend", body={"borrower_id": borrower_id})
        )
        draft_probe = probe(
            base,
            token,
            "outreach.draft",
            "POST",
            "/api/outreach/draft",
            body={"borrower_id": borrower_id},
        )
        results.append(draft_probe)
    else:
        draft_probe = None

    # 6. Approval writes. Positive probes use real borrowers; negative probes
    # use synthetic IDs and must 404 without writing Lakebase rows.
    if borrower_id:
        results.append(approve_real_probe(base, token, borrower_id, draft_probe))
    if reject_borrower_id:
        results.append(
            probe(
                base,
                token,
                "outreach.reject.real",
                "POST",
                "/api/outreach/reject",
                body={
                    "borrower_id": reject_borrower_id,
                    "offer_code": "refi",
                    "rationale_code": "low_intent",
                    "request_id": str(uuid.uuid4()),
                },
            )
        )
    results.append(
        probe(
            base,
            token,
            "outreach.approve.unknown_404",
            "POST",
            "/api/outreach/approve",
            body={
                "borrower_id": unknown_uuid_approve,
                "offer_code": "refi",
                "draft_body": "Governed verification draft. To comply with mortgage marketing rules, this is a human-approved test body.",
                # A well-formed synthetic proof + review_mode (required since
                # W5c), so the request still reaches the unknown-borrower 404.
                "draft_generation_id": str(uuid.uuid4()),
                "draft_response_hash": "0" * 64,
                "draft_source_refreshed_at": "2026-01-01T00:00:00Z",
                "review_mode": "individual",
                "request_id": str(uuid.uuid4()),
            },
            expect_status=404,
        )
    )
    results.append(
        probe(
            base,
            token,
            "outreach.reject.unknown_404",
            "POST",
            "/api/outreach/reject",
            body={
                "borrower_id": unknown_uuid_reject,
                "offer_code": "refi",
                "rationale_code": "low_intent",
                "request_id": str(uuid.uuid4()),
            },
            expect_status=404,
        )
    )

    # 7. Audit events
    results.append(probe(base, token, "audit.events", "GET", "/api/audit/events?limit=10"))

    # 8. Genie
    results.append(
        probe(
            base,
            token,
            "genie.message",
            "POST",
            "/api/genie/message",
            body={"question": "How many borrowers are currently in-the-money?"},
            timeout=60.0,
        )
    )

    # 9. Admin + geo
    # Admin endpoints are RBAC-gated (X-Forwarded-Groups must include the
    # admin group). Databricks Apps injects this header in production based
    # on the caller's workspace groups. For the smoke probe we inject it
    # manually — a 403 here would mean either the gate is disabled (bad)
    # OR the header plumbing regressed (bad).
    admin_headers = {"X-Forwarded-Groups": "mip-admin"}
    results.append(
        probe(base, token, "admin.rules", "GET", "/api/admin/rules", extra_headers=admin_headers)
    )
    results.append(
        probe(base, token, "admin.sources", "GET", "/api/admin/sources", extra_headers=admin_headers)
    )
    # RBAC negative path is NOT exercised here. The Databricks Apps edge
    # strips and re-injects ``X-Forwarded-Email`` / ``X-Forwarded-Groups``
    # from the authenticated bearer identity, so a client running as an
    # allow-listed admin cannot forge a non-admin identity from outside
    # the edge. The 403 path is covered by
    # ``tests/unit/test_admin_rbac.py::test_admin_rejects_non_admin_group``
    # and related unit tests that inject headers directly into the
    # TestClient. Running an external negative probe would require
    # provisioning a second service principal with no admin membership.
    results.append(probe(base, token, "geo.state_rollups", "GET", "/api/geo/state-rollups"))
    # The Rate Lever grid; collect_red_flags checks its invariants.
    results.append(probe(base, token, "geo.rate_sensitivity", "GET", "/api/geo/rate-sensitivity"))
    # Audit explorer reads (admin-gated, audit-free).
    results.append(
        probe(base, token, "audit.facets", "GET", "/api/audit/facets", extra_headers=admin_headers)
    )
    results.append(
        probe(base, token, "audit.count", "GET", "/api/audit/count", extra_headers=admin_headers)
    )
    results.extend(_geo_drill_probes(base, token))

    return results


def _geo_drill_probes(base: str, token: str) -> list[ProbeResult]:
    """Drill the map the way the UI does: largest populated state -> its ZIPs.

    The county grain is retired (``tools/verify_live_geo_drill.py``), so the
    county endpoint is probed only as a shape contract and the Lead Queue deep
    link carries ``state`` + ``zip`` and no ``county``. The payload checks,
    the reconciliation included, run in ``collect_red_flags`` on exactly the
    payloads recorded here.
    """
    criteria = {
        "segment_codes": "itm,equity",
        "segment_mode": "all",
        "occupancy": "Owner-occupied",
        "lien_status": "Open 1st lien",
        "min_equity_pct_label": "≥ 25%",
    }
    state_path = "/api/geo/state-rollups?" + urllib.parse.urlencode(criteria)
    state_probe = probe(base, token, STATE_PROBE, "GET", state_path)
    results = [state_probe]

    if state_probe.ok:
        state_code, reason = pick_drill_state(state_probe.sample)
    else:
        state_code, reason = None, "filtered state rollups probe failed"
    if state_code is None:
        results.append(_drill_target_failure("geo.drill_target.state", state_path, str(reason)))
        return results

    geo_params = {"state": state_code, **criteria}
    zip_path = "/api/geo/zip-rollups?" + urllib.parse.urlencode(geo_params)
    zip_probe = probe(base, token, ZIP_PROBE, "GET", zip_path)
    results.append(zip_probe)
    county_path = "/api/geo/county-rollups?" + urllib.parse.urlencode(geo_params)
    results.append(probe(base, token, COUNTY_PROBE, "GET", county_path))

    zip_code = pick_drill_zip(zip_probe.sample, state_code) if zip_probe.ok else None
    if zip_code is None:
        results.append(
            _drill_target_failure(
                "geo.drill_target.zip",
                zip_path,
                f"no filtered ZIP rollup with addressable borrowers for {state_code}",
            )
        )
        return results

    results.append(
        probe(
            base,
            token,
            "leads.filtered_geo",
            "GET",
            "/api/leads?" + urllib.parse.urlencode({"state": state_code, "zip": zip_code, **criteria}),
        )
    )
    return results


def _drill_target_failure(name: str, path: str, error: str) -> ProbeResult:
    return ProbeResult(name=name, method="INFO", path=path, status=0, latency_ms=0.0, ok=False, error=error)


APPROVE_PATH = "/api/outreach/approve"
APPROVE_DRAFT_UNAVAILABLE = "draft unavailable: approve not attempted (no audited draft proof)"


def approve_body_from_draft(borrower_id: str, draft: Any) -> dict[str, Any] | None:
    """The approve.real body: the audited draft's exact copy and proof, or None.

    The approve contract (W5c) certifies the draft the approver saw: the copy
    (body, subject), the channel and campaign binding it was generated for,
    and its proof (generation id, response hash, source refresh), with
    review_mode 'individual'. Without a generation id and response hash there
    is no audited draft to certify, so there is no approve to send.
    """
    if not isinstance(draft, dict) or not draft.get("generation_id") or not draft.get("response_hash"):
        return None
    return {
        "borrower_id": borrower_id,
        "offer_code": draft.get("offer_code"),
        "channel": draft.get("channel"),
        "campaign_id": draft.get("campaign_id"),
        "variant_name": draft.get("variant_name"),
        "draft_body": draft.get("body"),
        "draft_subject": draft.get("subject"),
        "draft_generation_id": draft.get("generation_id"),
        "draft_response_hash": draft.get("response_hash"),
        "draft_source_refreshed_at": draft.get("source_refreshed_at"),
        "review_mode": "individual",
        "request_id": str(uuid.uuid4()),
    }


def approve_real_probe(base: str, token: str, borrower_id: str, draft_probe: ProbeResult | None) -> ProbeResult:
    """Approve the audited draft for a real borrower, only when the draft probe proved one.

    An audited approval WRITE on the target (one APPROVE row per run). It is
    attempted only after outreach.draft succeeded with its proof; otherwise it
    is a not-ok result and nothing is written.
    """
    draft = draft_probe.sample if draft_probe is not None and draft_probe.ok else None
    body = approve_body_from_draft(borrower_id, draft)
    if body is None:
        return ProbeResult(
            name="outreach.approve.real",
            method="POST",
            path=APPROVE_PATH,
            status=0,
            latency_ms=0.0,
            ok=False,
            error=APPROVE_DRAFT_UNAVAILABLE,
        )
    return probe(base, token, "outreach.approve.real", "POST", APPROVE_PATH, body=body)


def collect_red_flags(
    results: list[ProbeResult], *, max_source_age_days: int = DEFAULT_MAX_SOURCE_AGE_DAYS
) -> list[str]:
    flags: list[str] = []

    def find(n: str) -> ProbeResult | None:
        return next((r for r in results if r.name == n), None)

    unf = find("portfolio.unfiltered")
    narrowed = find("portfolio.all_states.owner.25pct")

    def total_of(r: ProbeResult | None) -> int | None:
        if r is None or not r.ok or not isinstance(r.sample, dict):
            return None
        for k in (
            "marketable_population",
            "total",
            "total_rows",
            "count",
            "row_count",
            "match_count",
        ):
            v = r.sample.get(k)
            if isinstance(v, int):
                return v
        return None

    t_unf = total_of(unf)
    t_narrowed = total_of(narrowed)
    if t_unf is not None and t_narrowed is not None and t_unf <= t_narrowed:
        flags.append(
            "portfolio filtered predicate did not narrow results: "
            f"unfiltered={t_unf} vs all_states.owner.25pct={t_narrowed}"
        )

    admin_sources = find("admin.sources")
    if admin_sources is not None and admin_sources.ok and isinstance(admin_sources.sample, list):
        flags.extend(_source_readiness_flags(admin_sources.sample, max_age_days=max_source_age_days))

    rate_sensitivity = find("geo.rate_sensitivity")
    if rate_sensitivity is not None and rate_sensitivity.ok:
        flags.extend(_rate_sensitivity_flags(rate_sensitivity.sample))

    flags.extend(geo_drill_flags(results))

    for r in results:
        if not r.ok:
            detail = r.error[:160] if r.error else "payload sanity check failed"
            flags.append(f"{r.name}: status={r.status} error={detail}")
        if r.ok and isinstance(r.sample, list) and len(r.sample) == 0:
            flags.append(f"{r.name}: returned empty array")
        if r.ok and any(key == "[array len=0]" for key in r.top_keys):
            flags.append(f"{r.name}: returned empty array")
        if r.ok and isinstance(r.sample, dict):
            for key in ("rows", "leads", "items", "rollups", "segments"):
                value = r.sample.get(key)
                # The retired county grain answers [] by design; its rows,
                # when any, are checked by geo_drill_flags.
                if key == "rollups" and r.name in EMPTY_ALLOWED_PROBES:
                    continue
                if isinstance(value, list) and len(value) == 0:
                    flags.append(f"{r.name}: `{key}` returned empty array")
    return flags


def _rate_sensitivity_flags(sample: Any) -> list[str]:
    """The Rate Lever grid's structural invariants (tools/verify_live_rate_sensitivity.py)."""
    return rate_sensitivity_flags(sample)


def render_markdown(
    results: list[ProbeResult],
    base: str,
    test_tag: str,
    *,
    profile: str = "DEFAULT",
    max_source_age_days: int = DEFAULT_MAX_SOURCE_AGE_DAYS,
) -> str:
    lines: list[str] = []
    lines.append("# E2E live verification — 2026-04-23")
    lines.append("")
    lines.append("> **Internal validation artifact. Not public release collateral.**")
    lines.append("")
    lines.append(f"Base URL: `{base}`  ")
    lines.append(f"Auth: `databricks auth token -p {profile}` (OAuth bearer of the profile's identity)  ")
    lines.append(f"Source freshness: `checked_at` within {max_source_age_days} d (`--max-source-age-days`)  ")
    lines.append(f"Synthetic test-id prefix: `{test_tag}`")
    lines.append("")
    lines.append("## Endpoint results")
    lines.append("")
    lines.append("| Endpoint | Method+Path | Status | Latency (ms) | Payload OK? | Notes |")
    lines.append("| --- | --- | ---: | ---: | :---: | --- |")
    for r in results:
        status = r.status if r.status is not None else "ERR"
        latency = f"{r.latency_ms:.0f}" if r.latency_ms is not None else ""
        ok = "yes" if r.ok else "NO"
        note = r.notes or ""
        if r.error:
            note = f"ERROR: {r.error[:160]}"
        elif r.top_keys:
            note = f"keys/len: {', '.join(r.top_keys[:8])}"
        lines.append(f"| {r.name} | `{r.method} {r.path}` | {status} | {latency} | {ok} | {note} |")
    lines.append("")
    lines.append("## Clean payload samples")
    lines.append("")
    for r in results:
        if r.ok and r.sample is not None:
            lines.append(f"### {r.name} — `{r.method} {r.path}`")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(_report_safe_sample(r.sample), indent=2, default=str))
            lines.append("```")
    lines.append("")
    lines.append("## Red flags")
    lines.append("")
    flags = collect_red_flags(results, max_source_age_days=max_source_age_days)

    if not flags:
        lines.append("(none)")
    else:
        for f in flags:
            lines.append(f"- {f}")

    lines.append("")
    lines.append("## Teardown")
    lines.append("")
    lines.append(
        "No synthetic approval/rejection rows are expected. `B-TEST-*` IDs are used only for unknown-borrower 404 probes."
    )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=DEFAULT_BASE)
    ap.add_argument("--profile", default="DEFAULT")
    ap.add_argument("--out", default="docs/validation/e2e-verification-2026-04-23.md")
    ap.add_argument(
        "--max-source-age-days",
        type=int,
        default=DEFAULT_MAX_SOURCE_AGE_DAYS,
        help="flag a source whose checked_at is older than this many days (default 3)",
    )
    args = ap.parse_args()
    if args.max_source_age_days < 1:
        ap.error("--max-source-age-days must be at least 1")

    try:
        token = get_oauth_token(args.profile)
    except Exception as exc:  # noqa: BLE001
        print(f"OAuth token fetch failed: {exc}", file=sys.stderr)
        return 2

    results = run_probes(args.base_url, token)
    report = render_markdown(
        results,
        args.base_url,
        "B-TEST-*",
        profile=args.profile,
        max_source_age_days=args.max_source_age_days,
    )
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(report)
    # also print JSON summary on stdout for the caller agent
    summary = [
        {
            "name": r.name,
            "method": r.method,
            "path": r.path,
            "status": r.status,
            "latency_ms": round(r.latency_ms, 1) if r.latency_ms else None,
            "ok": r.ok,
            "top_keys": r.top_keys[:8],
            "error": r.error,
        }
        for r in results
    ]
    print(json.dumps(summary, indent=2))
    flags = collect_red_flags(results, max_source_age_days=args.max_source_age_days)
    if flags:
        print("Live verification red flags:", file=sys.stderr)
        for flag in flags:
            print(f"- {flag}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
