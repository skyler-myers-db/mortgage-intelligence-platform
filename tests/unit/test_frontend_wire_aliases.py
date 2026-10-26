"""Planted cases for the P3 alias rules (tests/unit/frontend_wire_aliases.py; W5e w5-wire-types B4).

A same-named generated alias (``export type X = ApiResponse<'X'>``) is its own
coverage for that side only; an alias naming another key, or any other alias
form, keeps needing its pairs; an exported indexed-alias vocabulary needs its
``string extends X`` widening guard in a check file.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit import frontend_wire_aliases as aliases
from tests.unit import frontend_wire_contract as wire

CLIENT = """\
import type { ApiRequest, ApiResponse } from '../api.gen';
export type LeadSummary = ApiResponse<'LeadSummary'>;
export type Borrower360 = ApiResponse<'LeadSummary'>;
export type OutreachRejectRequest = ApiRequest<'OutreachRejectRequest'>;
export type SessionResponse = ApiRequest<'SessionResponse'>;
export type HandWritten = ApiResponse<'HandWritten'> & { extra: string };
export type OfferCode = NonNullable<ApiRequest<'OutreachApproveRequest'>['offer_code']>;
export type RejectCode = ApiRequest<'OutreachRejectRequest'>['rationale_code'];
"""


def _project(tmp_path: Path, check: str) -> tuple[wire.Project, list[Path]]:
    (tmp_path / "lib" / "apiClients").mkdir(parents=True)
    (tmp_path / "types").mkdir()
    (tmp_path / "lib" / "apiClients" / "planted.ts").write_text(CLIENT, encoding="utf-8")
    check_file = tmp_path / "types" / "wireContract.planted.check.ts"
    check_file.write_text(check, encoding="utf-8")
    return wire.Project(tmp_path), [check_file]


def test_only_a_same_named_alias_counts_as_coverage_and_only_for_its_side(tmp_path: Path) -> None:
    project, _ = _project(tmp_path, "export type WireContractPlanted = [];\n")
    covered = aliases.alias_coverage(project)
    rel = wire.rel_path(tmp_path / "lib" / "apiClients" / "planted.ts")

    assert covered == {
        ("response", rel, "LeadSummary"),
        ("request", rel, "OutreachRejectRequest"),
        # The request view of SessionResponse: it covers that side, never the response side.
        ("request", rel, "SessionResponse"),
    }
    named = [
        ("response", rel, "LeadSummary"),
        ("response", rel, "Borrower360"),
        ("request", rel, "OutreachRejectRequest"),
        ("response", rel, "SessionResponse"),
        ("response", rel, "HandWritten"),
    ]
    missing = wire.missing_coverage(named, [], covered)
    # An alias naming another key, the wrong side and an intersection keep their pairs.
    assert missing == [
        f"response pair {rel}:Borrower360 vs ApiResponse<'Borrower360'>",
        f"response pair {rel}:SessionResponse vs ApiResponse<'SessionResponse'>",
        f"response pair {rel}:HandWritten vs ApiResponse<'HandWritten'>",
    ]


def test_an_indexed_vocabulary_needs_its_widening_guard(tmp_path: Path) -> None:
    project, checks = _project(
        tmp_path,
        "export type WireContractPlanted = [\n  Expect<string extends OfferCode ? false : true>,\n];\n",
    )
    rel = wire.rel_path(tmp_path / "lib" / "apiClients" / "planted.ts")

    assert sorted(aliases.indexed_vocabularies(project)) == [(rel, "OfferCode"), (rel, "RejectCode")]
    assert aliases.unguarded_vocabularies(project, checks) == [f"{rel}:RejectCode"]


def test_a_guard_line_removed_fails(tmp_path: Path) -> None:
    project, checks = _project(tmp_path, "export type WireContractPlanted = [];\n")
    rel = wire.rel_path(tmp_path / "lib" / "apiClients" / "planted.ts")

    assert aliases.unguarded_vocabularies(project, checks) == [f"{rel}:OfferCode", f"{rel}:RejectCode"]
