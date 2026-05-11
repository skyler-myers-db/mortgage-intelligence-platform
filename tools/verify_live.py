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
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

DEFAULT_BASE = "https://mip-app-2543889327043640.aws.databricksapps.com"


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
        # pick a useful sample
        sample = {k: parsed[k] for k in list(parsed.keys())[:6]}
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
        results.append(
            probe(base, token, "outreach.draft", "POST", "/api/outreach/draft", body={"borrower_id": borrower_id})
        )

    # 6. Approval writes. Positive probes use real borrowers; negative probes
    # use synthetic IDs and must 404 without writing Lakebase rows.
    if borrower_id:
        results.append(
            probe(
                base,
                token,
                "outreach.approve.real",
                "POST",
                "/api/outreach/approve",
                body={
                    "borrower_id": borrower_id,
                    "offer_code": "refi",
                    "request_id": f"verify-live-approve-{uuid.uuid4()}",
                },
            )
        )
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
                    "request_id": f"verify-live-reject-{uuid.uuid4()}",
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
                "request_id": f"verify-live-unknown-approve-{uuid.uuid4()}",
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
                "request_id": f"verify-live-unknown-reject-{uuid.uuid4()}",
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
            body={"question": "Which zips have the most in-the-money refi candidates?"},
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
    # from the authenticated bearer identity, so a client running as
    # skyler (an allow-listed admin) cannot forge a non-admin identity
    # from outside the edge. The 403 path is covered by
    # ``tests/unit/test_admin_rbac.py::test_admin_rejects_non_admin_group``
    # and related unit tests that inject headers directly into the
    # TestClient. Running an external negative probe would require
    # provisioning a second service principal with no admin membership.
    results.append(probe(base, token, "geo.state_rollups", "GET", "/api/geo/state-rollups"))
    results.extend(_geo_drill_probes(base, token))

    return results


def _geo_drill_probes(base: str, token: str) -> list[ProbeResult]:
    criteria = {
        "segment_codes": "itm,equity",
        "segment_mode": "all",
        "occupancy": "Owner-occupied",
        "lien_status": "Open 1st lien",
        "min_equity_pct_label": "≥ 25%",
    }
    state_path = "/api/geo/state-rollups?" + urllib.parse.urlencode(criteria)
    results = [probe(base, token, "geo.state_rollups.filtered", "GET", state_path)]

    state_code: str | None = None
    try:
        state_payload = get_json(base, token, state_path)
        rollups = state_payload.get("rollups") if isinstance(state_payload, dict) else None
        if isinstance(rollups, list):
            for row in rollups:
                if isinstance(row, dict) and int(row.get("addressable") or 0) > 0:
                    state_code = str(row.get("state") or "").upper()
                    break
    except Exception as exc:  # noqa: BLE001
        results.append(
            ProbeResult(
                name="geo.drill_target.state",
                method="INFO",
                path=state_path,
                status=0,
                latency_ms=0.0,
                ok=False,
                error=f"could not discover filtered state target: {exc}",
            )
        )
        return results

    if not state_code:
        results.append(
            ProbeResult(
                name="geo.drill_target.state",
                method="INFO",
                path=state_path,
                status=0,
                latency_ms=0.0,
                ok=False,
                error="no filtered state rollup with addressable borrowers",
            )
        )
        return results

    county_params = {"state": state_code, **criteria}
    county_path = "/api/geo/county-rollups?" + urllib.parse.urlencode(county_params)
    results.append(probe(base, token, "geo.county_rollups.filtered", "GET", county_path))

    county_fips: str | None = None
    try:
        county_payload = get_json(base, token, county_path)
        rollups = county_payload.get("rollups") if isinstance(county_payload, dict) else None
        if isinstance(rollups, list):
            for row in rollups:
                if isinstance(row, dict) and int(row.get("addressable_borrowers") or 0) > 0:
                    county_fips = str(row.get("fips_5") or "")
                    break
    except Exception as exc:  # noqa: BLE001
        results.append(
            ProbeResult(
                name="geo.drill_target.county",
                method="INFO",
                path=county_path,
                status=0,
                latency_ms=0.0,
                ok=False,
                error=f"could not discover filtered county target: {exc}",
            )
        )
        return results

    if not county_fips:
        results.append(
            ProbeResult(
                name="geo.drill_target.county",
                method="INFO",
                path=county_path,
                status=0,
                latency_ms=0.0,
                ok=False,
                error=f"no filtered county rollup with addressable borrowers for {state_code}",
            )
        )
        return results

    zip_params = {"county_fips": county_fips, **criteria}
    zip_path = "/api/geo/zip-rollups?" + urllib.parse.urlencode(zip_params)
    results.append(probe(base, token, "geo.zip_rollups.filtered", "GET", zip_path))
    results.append(
        probe(
            base,
            token,
            "leads.filtered_geo",
            "GET",
            "/api/leads?"
            + urllib.parse.urlencode(
                {
                    "state": state_code,
                    "county": county_fips,
                    **criteria,
                }
            ),
        )
    )
    return results


def collect_red_flags(results: list[ProbeResult]) -> list[str]:
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
        flags.extend(_source_readiness_flags(admin_sources.sample))

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
                if isinstance(value, list) and len(value) == 0:
                    flags.append(f"{r.name}: `{key}` returned empty array")
    return flags


def _source_readiness_flags(rows: list[Any]) -> list[str]:
    flags: list[str] = []
    by_name = {
        str(row.get("name")): row
        for row in rows
        if isinstance(row, dict) and row.get("name") is not None
    }
    core_sources = {
        "Cotality Public Records",
        "Voluntary Lien",
        "MMA Mortgage Analytics",
        "CLIP",
        "Owner Link",
        "AVM",
        "FRED Market Rates",
        "UC Gold Borrower 360",
        "UC Gold Lead Scores",
        "UC Gold Lead Population",
        "UC Gold Segment Population",
        "UC Gold Borrower Dossier",
    }
    first_party_sources = {
        "First-party LOS / Applications",
        "First-party Servicing Portfolio",
        "First-party CRM / Campaigns",
        "First-party Customer Interactions",
        "First-party Product Balances",
    }
    for source_name in sorted(core_sources):
        row = by_name.get(source_name)
        if row is None:
            flags.append(f"admin.sources: missing core source `{source_name}`")
            continue
        if row.get("status") != "live":
            flags.append(f"admin.sources: {source_name} status={row.get('status')} expected live")
        if int(row.get("rows") or 0) <= 0:
            flags.append(f"admin.sources: {source_name} has no row count proof")
        if not row.get("last_updated"):
            flags.append(f"admin.sources: {source_name} missing last_updated")
        if not row.get("checked_at"):
            flags.append(f"admin.sources: {source_name} missing checked_at")
        elif _is_stale_checked_at(row.get("checked_at")):
            flags.append(f"admin.sources: {source_name} checked_at is stale")
    for source_name in sorted(first_party_sources):
        row = by_name.get(source_name)
        if row is None:
            flags.append(f"admin.sources: missing first-party source `{source_name}`")
            continue
        if row.get("status") not in {"live", "demo_synthetic"}:
            flags.append(
                f"admin.sources: {source_name} status={row.get('status')} expected live/demo_synthetic"
            )
        if bool(row.get("synthetic_demo")) and row.get("status") != "demo_synthetic":
            flags.append(f"admin.sources: {source_name} synthetic_demo not disclosed")
        if int(row.get("rows") or 0) <= 0:
            flags.append(f"admin.sources: {source_name} has no row count proof")
        if not row.get("last_updated"):
            flags.append(f"admin.sources: {source_name} missing last_updated")
        if not row.get("checked_at"):
            flags.append(f"admin.sources: {source_name} missing checked_at")
        elif _is_stale_checked_at(row.get("checked_at")):
            flags.append(f"admin.sources: {source_name} checked_at is stale")
    for source_name in ("MLS", "Building Permits"):
        row = by_name.get(source_name)
        if row is None:
            flags.append(f"admin.sources: missing pending source `{source_name}`")
        elif row.get("status") == "live":
            flags.append(f"admin.sources: {source_name} cannot be live until the feed is loaded")
    return flags


def _is_stale_checked_at(value: Any, *, max_age: timedelta = timedelta(days=3)) -> bool:
    if not isinstance(value, str) or not value.strip():
        return True
    raw = value.strip()
    candidates = [
        raw,
        raw.replace(" ", "T"),
        raw.replace("Z", "+00:00"),
        raw.replace(" ", "T").replace("Z", "+00:00"),
    ]
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return datetime.now(UTC) - parsed.astimezone(UTC) > max_age
    return True


def render_markdown(results: list[ProbeResult], base: str, test_tag: str) -> str:
    lines: list[str] = []
    lines.append("# E2E live verification — 2026-04-23")
    lines.append("")
    lines.append(f"Base URL: `{base}`  ")
    lines.append("Auth: `databricks auth token -p DEFAULT` (OAuth bearer, skyler@entrada.ai)  ")
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
            lines.append(json.dumps(r.sample, indent=2, default=str)[:1500])
            lines.append("```")
    lines.append("")
    lines.append("## Red flags")
    lines.append("")
    flags = collect_red_flags(results)

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
    args = ap.parse_args()

    try:
        token = get_oauth_token(args.profile)
    except Exception as exc:  # noqa: BLE001
        print(f"OAuth token fetch failed: {exc}", file=sys.stderr)
        return 2

    results = run_probes(args.base_url, token)
    report = render_markdown(results, args.base_url, "B-TEST-*")
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
    flags = collect_red_flags(results)
    if flags:
        print("Live verification red flags:", file=sys.stderr)
        for flag in flags:
            print(f"- {flag}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
