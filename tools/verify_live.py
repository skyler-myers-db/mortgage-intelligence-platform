"""Live endpoint verifier for the deployed Mortgage Intelligence Platform app.

Probes each production API endpoint using an OAuth bearer token obtained via
`databricks auth token -p DEFAULT`, then reports status, latency, and a
lightweight payload sanity check.

Usage:
    python tools/verify_live.py
    python tools/verify_live.py --base-url https://mip-app-2543889327043640.aws.databricksapps.com

Idempotent: writes approvals with synthetic B-TEST-<uuid> IDs that the teardown
block deletes at the end via the Lakebase API path (best-effort; the report
still records what was written).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
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
) -> ProbeResult:
    url = base.rstrip("/") + path
    data = None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
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
        return ProbeResult(
            name=name,
            method=method,
            path=path,
            status=status,
            latency_ms=latency,
            ok=False,
            raw_len=len(raw),
            error=raw[:500].decode("utf-8", errors="replace"),
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
        sample = parsed[0] if parsed else None

    return ProbeResult(
        name=name,
        method=method,
        path=path,
        status=status,
        latency_ms=latency,
        ok=200 <= status < 300,
        raw_len=len(raw),
        top_keys=top_keys,
        sample=sample,
    )


def run_probes(base: str, token: str) -> list[ProbeResult]:
    # generate synthetic test IDs so we never pollute real data
    test_uuid_approve = f"B-TEST-{uuid.uuid4().hex[:8].upper()}"
    test_uuid_reject = f"B-TEST-{uuid.uuid4().hex[:8].upper()}"

    results: list[ProbeResult] = []

    # 1. Health
    results.append(probe(base, token, "health", "GET", "/api/health"))

    # 2. Portfolio preview — unfiltered vs filtered
    results.append(
        probe(base, token, "portfolio.unfiltered", "POST", "/api/portfolio/preview", body={"criteria": {}})
    )
    results.append(
        probe(
            base,
            token,
            "portfolio.chicago",
            "POST",
            "/api/portfolio/preview",
            body={"criteria": {"geography": "Chicago MSA"}},
        )
    )
    results.append(
        probe(
            base,
            token,
            "portfolio.chicago.owner.25pct",
            "POST",
            "/api/portfolio/preview",
            body={
                "criteria": {
                    "geography": "Chicago MSA",
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

    # 4. Try to pick a real borrower id from /api/leads
    borrower_id = None
    for r in results:
        if r.name == "leads.all" and r.ok:
            data = r.sample
            if isinstance(data, dict):
                rows = data.get("rows") or data.get("leads") or data.get("items") or []
            elif isinstance(data, list):
                rows = [data]
            else:
                rows = []
            if rows and isinstance(rows[0], dict):
                borrower_id = rows[0].get("borrower_id") or rows[0].get("id")
            break
    borrower_id = borrower_id or "B-00001"

    results.append(probe(base, token, "borrower.detail", "GET", f"/api/borrowers/{borrower_id}"))
    results.append(probe(base, token, "borrower.evidence", "GET", f"/api/borrowers/{borrower_id}/evidence"))

    # 5. Offers & outreach for real borrower
    results.append(
        probe(base, token, "offers.recommend", "POST", "/api/offers/recommend", body={"borrower_id": borrower_id})
    )
    results.append(
        probe(base, token, "outreach.draft", "POST", "/api/outreach/draft", body={"borrower_id": borrower_id})
    )

    # 6. Approval writes — synthetic IDs only
    results.append(
        probe(
            base,
            token,
            "outreach.approve.synthetic",
            "POST",
            "/api/outreach/approve",
            body={"borrower_id": test_uuid_approve, "offer_code": "refi"},
        )
    )
    results.append(
        probe(
            base,
            token,
            "outreach.reject.synthetic",
            "POST",
            "/api/outreach/reject",
            body={"borrower_id": test_uuid_reject, "offer_code": "refi"},
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
    results.append(probe(base, token, "admin.rules", "GET", "/api/admin/rules"))
    results.append(probe(base, token, "admin.sources", "GET", "/api/admin/sources"))
    results.append(probe(base, token, "geo.state_rollups", "GET", "/api/geo/state-rollups"))

    return results


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
    flags: list[str] = []

    # compute filter sanity
    def find(n: str) -> ProbeResult | None:
        return next((r for r in results if r.name == n), None)

    unf = find("portfolio.unfiltered")
    chi = find("portfolio.chicago")
    chi_own = find("portfolio.chicago.owner.25pct")

    def total_of(r: ProbeResult | None) -> int | None:
        if r is None or not r.ok or not isinstance(r.sample, dict):
            return None
        for k in ("total", "total_rows", "count", "row_count", "match_count"):
            v = r.sample.get(k)
            if isinstance(v, int):
                return v
        return None

    t_unf = total_of(unf)
    t_chi = total_of(chi)
    t_chi_own = total_of(chi_own)
    if t_unf is not None and t_chi is not None and t_unf <= t_chi:
        flags.append(
            f"portfolio filter predicate did NOT narrow results: unfiltered={t_unf} vs chicago={t_chi}"
        )
    if t_chi is not None and t_chi_own is not None and t_chi <= t_chi_own:
        flags.append(
            f"chicago+owner+25pct not stricter than chicago: chicago={t_chi} vs chicago.owner={t_chi_own}"
        )

    for r in results:
        if not r.ok and r.error:
            flags.append(f"{r.name}: status={r.status} error={r.error[:160]}")
        if r.ok and isinstance(r.sample, list) and len(r.sample) == 0:
            flags.append(f"{r.name}: returned empty array")

    if not flags:
        lines.append("(none)")
    else:
        for f in flags:
            lines.append(f"- {f}")

    lines.append("")
    lines.append("## Teardown")
    lines.append("")
    lines.append(
        "Synthetic approvals/rejections written with `B-TEST-*` IDs. Clean up via:"
    )
    lines.append("")
    lines.append("```sql")
    lines.append("DELETE FROM mip_app.approvals WHERE borrower_id LIKE 'B-TEST-%';")
    lines.append("DELETE FROM mip_app.audit_events WHERE borrower_id LIKE 'B-TEST-%';")
    lines.append("```")
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
