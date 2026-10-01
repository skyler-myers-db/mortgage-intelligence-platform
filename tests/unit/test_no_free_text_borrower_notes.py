"""No free-text borrower notes or @mentions (D-shell-deviations-g1; flow-08).

The 2026-09-30 ruling (docs/prototype-deviations.md, borrower-notes-mentions)
keeps free text about consumers out of the product: it creates fair-lending /
UDAAP evidence that the existing screens cannot catch by paraphrase, the
lender CRM is the narrative system of record, and @mentions need a staff
directory and a delivery channel that duplicate structured routing.

Three pins, all read from committed artifacts:

(a) no API path in tests/fixtures/openapi_baseline.json has a segment that
    names notes or mentions;
(b) lakebase/schema.sql creates no table whose name says notes or mentions;
(c) every request-body schema, resolved recursively through $ref / anyOf /
    oneOf / allOf / items / additionalProperties, has no property named like a
    note, mention, comment or memo outside ALLOWLIST. The allowlist records the
    OWNING schema (not the request root) and only shrinks: a pair that
    disappears fails until it is removed here (w5-refusal-capture-sales
    retires DispositionRequest.notes).

Stdlib only.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
OPENAPI = REPO / "tests" / "fixtures" / "openapi_baseline.json"
LAKEBASE_SCHEMA = REPO / "lakebase" / "schema.sql"

PATH_SEGMENT_RE = re.compile(r"notes?|mentions?", re.IGNORECASE)
TABLE_NAME_RE = re.compile(r"notes|mentions?", re.IGNORECASE)
PROPERTY_RE = re.compile(r"(^|_)(notes?|mentions?|comments?|memo)$")
CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z0-9_.\"]+)", re.IGNORECASE
)

# (owning schema, property). Exactly these three on 0a30fca2.
ALLOWLIST = frozenset(
    {
        ("DispositionRequest", "notes"),
        ("GenieFeedbackRequest", "comment"),
        ("PostedComposedPlan", "risk_notes"),
    }
)


def _openapi() -> dict[str, Any]:
    return json.loads(OPENAPI.read_text(encoding="utf-8"))


def path_segment_hits(paths: dict[str, Any]) -> list[str]:
    return sorted(
        path
        for path in paths
        if any(PATH_SEGMENT_RE.search(segment) for segment in path.split("/") if segment)
    )


def table_name_hits(sql: str) -> list[str]:
    return sorted(
        name
        for name in (m.group(1).strip('"') for m in CREATE_TABLE_RE.finditer(sql))
        if TABLE_NAME_RE.search(name.rsplit(".", 1)[-1])
    )


def _walk(
    schema: Any, owner: str, components: dict[str, Any], seen: set[str]
) -> Iterator[tuple[str, str]]:
    if not isinstance(schema, dict):
        return
    ref = schema.get("$ref")
    if isinstance(ref, str):
        name = ref.rsplit("/", 1)[-1]
        if name not in seen:
            seen.add(name)
            yield from _walk(components[name], name, components, seen)
        return
    for key in ("anyOf", "oneOf", "allOf"):
        for child in schema.get(key, []):
            yield from _walk(child, owner, components, seen)
    for key in ("items", "additionalProperties"):
        yield from _walk(schema.get(key), owner, components, seen)
    for prop, child in (schema.get("properties") or {}).items():
        if PROPERTY_RE.search(prop):
            yield owner, prop
        yield from _walk(child, owner, components, seen)


def request_body_note_properties(spec: dict[str, Any]) -> set[tuple[str, str]]:
    components = spec.get("components", {}).get("schemas", {})
    hits: set[tuple[str, str]] = set()
    for path, operations in spec.get("paths", {}).items():
        for method, operation in operations.items():
            if not isinstance(operation, dict):
                continue
            body = operation.get("requestBody") or {}
            for media in (body.get("content") or {}).values():
                root = f"<{method.upper()} {path}>"
                hits.update(_walk(media.get("schema"), root, components, set()))
    return hits


def test_no_api_path_names_notes_or_mentions() -> None:
    assert path_segment_hits(_openapi()["paths"]) == []


def test_no_lakebase_table_names_notes_or_mentions() -> None:
    assert table_name_hits(LAKEBASE_SCHEMA.read_text(encoding="utf-8")) == []


def test_request_bodies_carry_no_new_free_text_note_property() -> None:
    found = request_body_note_properties(_openapi())
    new = sorted(found - ALLOWLIST)
    stale = sorted(ALLOWLIST - found)
    assert new == [], (
        f"new free-text note/mention/comment request properties {new}: borrower notes and "
        "@mentions are not adopted (docs/prototype-deviations.md, borrower-notes-mentions)"
    )
    assert stale == [], f"allowlisted pairs {stale} are gone: shrink ALLOWLIST"


def test_the_pins_detect_what_they_guard() -> None:
    assert path_segment_hits({"/api/v1/borrowers/{borrower_id}/notes": {}}) == [
        "/api/v1/borrowers/{borrower_id}/notes"
    ]
    assert path_segment_hits({"/api/v1/mentions": {}, "/api/v1/leads": {}}) == ["/api/v1/mentions"]
    sql = "CREATE TABLE IF NOT EXISTS mip_app.borrower_notes (id int);\nCREATE TABLE x.approvals ();"
    assert table_name_hits(sql) == ["mip_app.borrower_notes"]
    spec = {
        "components": {
            "schemas": {
                "Wrapper": {"properties": {"inner": {"anyOf": [{"$ref": "#/components/schemas/Inner"}]}}},
                "Inner": {"properties": {"memo": {"type": "string"}, "mention_ids": {}}},
            }
        },
        "paths": {
            "/x": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Wrapper"}
                            }
                        }
                    }
                }
            }
        },
    }
    assert request_body_note_properties(spec) == {("Inner", "memo")}
