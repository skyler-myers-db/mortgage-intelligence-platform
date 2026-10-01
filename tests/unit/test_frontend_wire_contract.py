"""Frontend wire contract (audit 2026-09-21 quality-04 items 1-2 / stack-v1).

Decision D-api-types-a3 P1, static and without Node:

(1) every schema-named hand type (types.ts, types/*.ts, lib/apiTypes.ts,
    lib/apiClients/*.ts) has its pair in a wireContract.<domain>.check.ts
    file, which tsc checks through ``include: ["src"]``;
(2) every frontend network call site resolves to exactly one ApiOperations
    key and its type arguments are bound to that operation's ok / body:
    transport calls anywhere under frontend/src, raw fetch / sendBeacon sites
    (each with a ``// wire:`` annotation), their ``.json() as H`` casts and the
    boot module's BOOT_READS;
(3) every ``@ts-expect-error`` in the check files is a dated, owner-tagged
    wire-drift entry, and the check files hold nothing else that could hide
    a mismatch.

The parser lives in tests/unit/frontend_wire_contract.py; its own rules are
pinned by tests/unit/test_frontend_wire_contract_parser.py.
"""

from __future__ import annotations

import re
from functools import cache

from tests.unit import frontend_wire_contract as wire
from tests.unit import frontend_wire_source as source

# Shrink-only: an entry whose site now parses and binds (or no longer exists)
# fails as stale. The key is '<file>:<enclosing function or property>:<ordinal>',
# never a line number. The integrator may add WireFits pairs, dated drift
# entries, `// wire:` lines or type-only extractions, never an entry here.
_ADMIN_PASSTHROUGH = "generic <T> passthrough: the caller names T; P3 admin domain, w5-wire-types"
BINDING_EXEMPT: dict[str, str] = {
    "frontend/src/lib/apiClients/admin.ts:adminRules:1": _ADMIN_PASSTHROUGH,
    "frontend/src/lib/apiClients/admin.ts:adminSources:1": _ADMIN_PASSTHROUGH,
    "frontend/src/lib/apiClients/admin.ts:adminOperations:1": _ADMIN_PASSTHROUGH,
    "frontend/src/lib/apiClients/admin.ts:adminCapabilities:1": _ADMIN_PASSTHROUGH,
    "frontend/src/lib/apiClients/admin.ts:adminRunOperation:1": (
        f"{_ADMIN_PASSTHROUGH}; typeof payload body: request pair via Parameters<> in wireContract.admin.check.ts"
    ),
    "frontend/src/lib/bootPrime.ts:usableJson:1": "T is inferred from the fallback transport call, which is itself bound",
}

# The W5c / W5d lanes a wire-drift entry may name as its P2 owner (closed set).
DRIFT_OWNER_LANES = frozenset(
    {
        "w5-lead-queue-paging",
        "w5-dossier-decisions",
        "w5-refusal-capture-sales",
        "w5-genie-stop-context",
        "w5-shell-nav-followups",
        "w5-field-vitals",
        "w5-zcta-watchlist",
        "w5-evidence-drawer",
        "w5-lq-requests-binding",
        "w5-print-glossary-sales-manager",
        "w5-palette-offer-sales-loop",
        "w5-wire-types",
        "w5-test-harness-deps-report",
        "w5-css-cascade",
        "w5-lexicon-copy",
        "w5-compiler-lint",
    }
)
# A drift touching one of these names the record's fail-closed rule.
FAIL_CLOSED_FIELDS = (
    "approval_status",
    "can_approve",
    "can_access_admin",
    "audit_event_id",
    "approved_at",
    "decision",
    "approver",
    "event_type",
)
DRIFT_DIRECTIVE = re.compile(r"^\s*// @ts-expect-error wire-drift quality-04 (\d{4}-\d{2}-\d{2}) ([\w-]+): (.+)$")
ADMIN_EGRESS = "Parameters<typeof adminApi['adminRunOperation']>[0]"
ADMIN_EGRESS_BODY = "ApiBody<'POST /api/v1/admin/operations/run'>"


@cache
def _state() -> tuple[wire.Project, dict[str, wire.Operation], wire.CheckFiles, tuple[wire.Site, ...]]:
    project = wire.Project()
    operations = wire.parse_operations(wire.API_GEN.read_text(encoding="utf-8"))
    checks = wire.parse_check_files(project)
    sites = wire.find_sites(project, operations, wire.scope_files())
    wire.bind_sites(project, sites, operations, checks.pairs)
    return project, operations, checks, tuple(sites)


def _sites() -> list[wire.Site]:
    return list(_state()[3])


def test_operation_keys_come_from_the_generated_api_operations() -> None:
    _, operations, _, _ = _state()

    assert "GET /api/v1/health" in operations
    assert "POST /api/v1/telemetry/rum" in operations
    assert operations["GET /api/v1/leads"].ok == (("LeadSummary", True),)
    assert operations["POST /api/v1/outreach/approve"].body == ("OutreachApproveRequest",)
    assert all(op.ok for op in operations.values()), "every operation names a 2xx schema"


def test_every_schema_named_hand_type_has_its_pair() -> None:
    project, _, checks, _ = _state()
    responses, requests = wire.schema_keys()

    named = wire.schema_named(project, responses, requests)
    sides = {side for side, _, _ in named}
    print(f"schema-named hand types: {len(named)} ({sorted(sides)})")

    assert {"response", "request"} <= sides
    assert checks.unresolved == []
    assert wire.missing_coverage(named, checks.pairs) == []


def test_every_network_call_site_is_bound_or_exempt() -> None:
    ledger = wire.account(_sites(), BINDING_EXEMPT)
    kinds = sorted({site.kind for site in _sites()})
    print(
        f"wire sites: {len(_sites())} ({', '.join(kinds)}); bound {len(ledger.bound)}, "
        f"exempt {len(ledger.exempt)}, raw/boot annotated "
        f"{sum(1 for s in ledger.bound if s.kind in {'raw', 'boot'})}"
    )

    assert [f"{s.key} (line {s.line}): {'; '.join(s.errors)}" for s in ledger.failing] == []
    assert ledger.stale == [], "BINDING_EXEMPT is shrink-only: remove the stale entries"
    assert len(ledger.bound) + len(ledger.exempt) == len(_sites())


def test_site_keys_are_unique_and_never_line_numbers() -> None:
    keys = [site.key for site in _sites()]

    assert len(keys) == len(set(keys))
    for key in BINDING_EXEMPT:
        file, name, ordinal = key.rsplit(":", 2)
        assert file.startswith("frontend/src/") and ordinal.isdigit() and not name.isdigit(), key


def test_exemptions_are_transport_or_cast_sites_only() -> None:
    by_key = {site.key: site for site in _sites()}

    for key in BINDING_EXEMPT:
        assert by_key[key].kind in {"transport", "cast"}, f"{key}: raw and boot sites are annotated, never exempted"


def _site(file: str, name: str, kind: str) -> list[wire.Site]:
    return [s for s in _sites() if s.file == f"frontend/src/{file}" and s.name == name and s.kind == kind]


def test_the_named_raw_and_boot_sites_are_bound() -> None:
    expected = {
        ("components/FootprintProvider.tsx", "defaultFetchFootprint", "raw"): "GET /api/v1/config/footprint",
        ("components/FootprintProvider.tsx", "defaultFetchFootprint", "cast"): "GET /api/v1/config/footprint",
        ("components/mortgage/DegradedBanner.tsx", "defaultFetchHealth", "raw"): "GET /api/v1/health",
        ("components/mortgage/DegradedBanner.tsx", "defaultFetchHealth", "cast"): "GET /api/v1/health",
        ("lib/apiFailure.ts", "probeSession", "raw"): "GET /api/v1/health",
        ("lib/queueVersion.ts", "fetchQueueVersion", "transport"): "GET /api/v1/workspace/queue-version",
        ("boot/primeBoot.ts", "BOOT_READS.session", "boot"): "GET /api/v1/session",
        ("boot/primeBoot.ts", "BOOT_READS.options", "boot"): "GET /api/v1/config/options",
        ("boot/primeBoot.ts", "BOOT_READS.footprint", "boot"): "GET /api/v1/config/footprint",
        ("boot/primeBoot.ts", "BOOT_READS.health", "boot"): "GET /api/v1/health",
    }
    for (file, name, kind), operation in expected.items():
        found = _site(file, name, kind)
        assert len(found) == 1, (file, name, kind, [s.key for s in _sites() if s.file.endswith(file)])
        assert found[0].bound and found[0].operation == operation, (found[0].key, found[0].errors)

    rum = _site("lib/rum.ts", "flushRum", "raw")
    assert sorted(s.helper for s in rum) == ["fetch", "navigator.sendBeacon"]
    assert all(s.bound and s.operation == "POST /api/v1/telemetry/rum" for s in rum)


def test_the_two_health_payload_copies_never_alias() -> None:
    _, _, checks, _ = _state()
    health = {
        pair.hand[0] for pair in checks.pairs if pair.hand[1] == "HealthPayload" and pair.key == "HealthResponse"
    }

    assert health == {"frontend/src/lib/apiTypes.ts", "frontend/src/components/mortgage/DegradedBanner.tsx"}


def test_the_admin_run_operation_body_has_its_egress_pair() -> None:
    text = (wire.SRC / "types" / "wireContract.admin.check.ts").read_text(encoding="utf-8")

    assert f"Expect<WireFits<{ADMIN_EGRESS}, {ADMIN_EGRESS_BODY}>>" in text
    assert f"Expect<DeepNoPhantomKeys<{ADMIN_EGRESS}, {ADMIN_EGRESS_BODY}>>" in text


def test_the_rum_event_has_its_request_pair() -> None:
    _, _, checks, _ = _state()

    assert any(
        pair.side == "request" and pair.hand == ("frontend/src/lib/rum.ts", "RumEvent") and pair.key == "RumEvent"
        for pair in checks.pairs
    )


def _check_file_lines() -> list[tuple[str, int, str, str]]:
    """(file, line number, raw line, comment-free line) over every check file, helper included."""

    rows: list[tuple[str, int, str, str]] = []
    for path in [wire.CHECK_HELPER, *wire.check_files()]:
        text = path.read_text(encoding="utf-8")
        code = source.code_only(text, wire.classify(text))
        for number, (raw, clean) in enumerate(zip(text.splitlines(), code.splitlines(), strict=True), start=1):
            rows.append((path.name, number, raw, clean))
    return rows


def test_check_files_hold_only_dated_owner_tagged_drift_directives() -> None:
    rows = _check_file_lines()
    entries = 0
    for index, (name, number, raw, _) in enumerate(rows):
        if not re.search(r"@ts-|eslint-disable|oxlint-disable", raw):
            continue
        m = DRIFT_DIRECTIVE.match(raw)
        assert m, f"{name}:{number}: only `@ts-expect-error wire-drift quality-04 <date> <lane>: <what>` is allowed"
        assert m.group(2) in DRIFT_OWNER_LANES, f"{name}:{number}: unknown owner lane {m.group(2)}"
        following = rows[index + 1][2].strip()
        assert following.startswith("Expect<"), f"{name}:{number}: a drift entry sits directly above one pair element"
        for field in FAIL_CLOSED_FIELDS:
            if re.search(rf"\b{field}\b", m.group(3)):
                assert f"fail-closed field {field}" in m.group(3), f"{name}:{number}: say 'fail-closed field {field}'"
        entries += 1
    print(f"wire-drift entries: {entries}")


def test_check_files_hold_no_any_and_no_assertion() -> None:
    for name, number, _, clean in _check_file_lines():
        if clean.lstrip().startswith("import type "):
            continue
        assert not re.search(r"\bany\b", clean), f"{name}:{number}: no `any` in a check file"
        assert not re.search(r"\bas\b", clean), f"{name}:{number}: no `as` assertion in a check file"


def test_check_files_import_types_only_and_only_siblings_import_the_helper() -> None:
    for name, number, _, clean in _check_file_lines():
        if re.match(r"\s*import\b", clean):
            assert clean.lstrip().startswith("import type {"), f"{name}:{number}: `import type` only"
    importers = sorted(
        wire.rel_path(path)
        for path in wire.SRC.rglob("*")
        if path.suffix in wire.EXTENSIONS
        and "node_modules" not in path.parts
        and re.search(r"from\s*['\"][./]*(?:types/)?wireContract\.check['\"]", path.read_text(encoding="utf-8"))
    )

    assert importers == sorted(wire.rel_path(path) for path in wire.check_files())


def test_every_domain_check_file_exports_one_contract_under_500_lines() -> None:
    files = wire.check_files()
    domains = {path.name.split(".")[1] for path in files}

    assert domains == {"leads", "portfolio", "genie", "growthAgent", "analytics", "admin", "sales", "activation"}
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert len(text.splitlines()) <= 500, path.name
        assert len(re.findall(r"(?m)^export type WireContract\w+ = \[$", text)) == 1, path.name
        assert re.findall(r"(?m)^export ", text) == ["export "], f"{path.name}: one export"
