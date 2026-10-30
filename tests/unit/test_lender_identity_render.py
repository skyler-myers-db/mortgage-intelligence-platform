"""Tenant aliases render into the lender dictionary seed and the lender_ref fallback.

The demo (Summit Mortgage, no pinned record) render is byte-identical: the
same tree with or without the code-registry identity, and the seed renders to
its own source text at catalog ``mip``, as it did before (sha256 equal to the
source on origin/main a9493e21). A digest-pinned record replaces the demo
tenant rows with its aliases, and the in-process fallback agrees with it.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest

from backend.schemas.lender_identity_record import (
    LENDER_IDENTITY_JSON_ENV,
    LENDER_IDENTITY_SHA256_ENV,
    ReviewedLenderIdentity,
    lender_identity_digest,
    reviewed_lender_identity_from_env,
)
from backend.services import lender_ref
from tests.fixtures.client_profiles import LENDER_IDENTITY
from tools import render_sql

ROOT = Path(__file__).resolve().parents[2]
SQL = ROOT / "sql"
SEED = SQL / "ref" / "lender_dictionary_seed.sql"
_ROW = re.compile(r"^    \('(?P<raw>(?:[^']|'')+)',\s+'(?P<display>(?:[^']|'')+)',\s+'(?P<type>[a-z_]+)',\s+(?P<flag>TRUE|FALSE)\),?$", re.M)

RECORD: dict[str, Any] = {
    **LENDER_IDENTITY,
    "legal_name": "O'Hara Home Loans",
    "tenant_id": "ohara",
    "servicer_aliases": ["O'Hara Servicing", "Wells Fargo Bk NA", "SUMMIT MTG"],
}


def _pinned(record: dict[str, Any] = RECORD) -> ReviewedLenderIdentity:
    text = json.dumps(record)
    return reviewed_lender_identity_from_env(
        {LENDER_IDENTITY_JSON_ENV: text, LENDER_IDENTITY_SHA256_ENV: lender_identity_digest(text)}
    )


def _rows(text: str) -> dict[str, tuple[str, str]]:
    rows = [match for match in _ROW.finditer(text)]
    found = {match["raw"].replace("''", "'"): (match["display"].replace("''", "'"), match["flag"]) for match in rows}
    assert len(found) == len(rows), "a raw_key repeats: the MERGE would see two source rows"
    return found


def _tree(dest: Path) -> dict[str, str]:
    return {
        str(path.relative_to(dest)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(dest.rglob("*.sql"))
    }


# --- the demo render ------------------------------------------------------------------


def test_the_demo_render_is_byte_identical(tmp_path: Path) -> None:
    code_registry = reviewed_lender_identity_from_env({})
    trees = []
    for name, lender in (("none", None), ("code_registry", code_registry)):
        dest = tmp_path / name
        render_sql.render(catalog="mip", demo_first_party_enabled=True, source_root=SQL, dest_root=dest, lender=lender)
        trees.append(_tree(dest))
        assert (dest / "ref" / "lender_dictionary_seed.sql").read_bytes() == SEED.read_bytes()
    assert trees[0] == trees[1]


def test_the_cli_renders_the_demo_seed_unchanged_without_a_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in (LENDER_IDENTITY_JSON_ENV, LENDER_IDENTITY_SHA256_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MIP_LENDER_NAME", "Not A Reviewed Mortgage")  # never read on this path, as before

    assert render_sql.main(["--catalog", "mip", "--source", str(SQL), "--dest", str(tmp_path)]) == 0
    assert (tmp_path / "ref" / "lender_dictionary_seed.sql").read_bytes() == SEED.read_bytes()


# --- a pinned record's render -----------------------------------------------------------


def test_a_pinned_record_replaces_the_demo_tenant_rows_with_its_aliases() -> None:
    before = _rows(SEED.read_text(encoding="utf-8"))
    after = _rows(render_sql.render_tenant_lender_rows(SEED.read_text(encoding="utf-8"), _pinned()))

    tenant = {raw: row for raw, row in after.items() if row[1] == "FALSE"}
    assert tenant == {
        "O'HARA SERVICING": ("O'Hara Home Loans", "FALSE"),
        "WELLS FARGO BK NA": ("O'Hara Home Loans", "FALSE"),
        "SUMMIT MTG": ("O'Hara Home Loans", "FALSE"),
    }
    assert all(display != "Summit Mortgage" for display, _ in after.values())
    competitors = {raw: row for raw, row in before.items() if row[1] == "TRUE" and raw != "WELLS FARGO BK NA"}
    assert {raw: row for raw, row in after.items() if row[1] == "TRUE"} == competitors


def test_the_pinned_render_still_rewrites_the_catalog_and_keeps_the_merge(tmp_path: Path) -> None:
    render_sql.render(
        catalog="acme_uc", demo_first_party_enabled=False, source_root=SQL, dest_root=tmp_path, lender=_pinned()
    )
    rendered = (tmp_path / "ref" / "lender_dictionary_seed.sql").read_text(encoding="utf-8")

    assert "MERGE INTO acme_uc.ref.lender_dictionary AS t" in rendered
    assert "('O''HARA SERVICING', 'O''Hara Home Loans', 'other', FALSE),\n" in rendered
    assert "('SUMMIT MTG', 'O''Hara Home Loans', 'other', FALSE)\n) AS s" in rendered
    assert rendered.count("WHEN NOT MATCHED THEN INSERT") == 1


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("'Summit Mortgage',              'non_bank_fintech',   FALSE),\n    ('SUMMIT MORTGAGE CORP'",
         "'Summit Mortgage Inc',          'non_bank_fintech',   FALSE),\n    ('SUMMIT MORTGAGE CORP'"),
        ("    ('PENNYMAC LOAN SVCS LLC',", "    -- a comment inside the block\n    ('PENNYMAC LOAN SVCS LLC',"),
        ("FALSE),\n    ('SUMMIT MORTGAGE',", "TRUE),\n    ('SUMMIT MORTGAGE',"),
    ],
)
def test_a_seed_outside_the_reviewed_shape_refuses_before_any_write(
    before: str, after: str, tmp_path: Path
) -> None:
    source = tmp_path / "sql"
    (source / "ref").mkdir(parents=True)
    text = SEED.read_text(encoding="utf-8")
    assert text.count(before) == 1
    (source / "ref" / "lender_dictionary_seed.sql").write_text(text.replace(before, after), encoding="utf-8")
    (source / "a_first.sql").write_text("SELECT 1 FROM mip.gold.x;\n", encoding="utf-8")
    dest = tmp_path / "rendered"

    with pytest.raises(render_sql.TenantRowsUnmatched, match="reviewed shape"):
        render_sql.render(catalog="mip", demo_first_party_enabled=False, source_root=source, dest_root=dest, lender=_pinned())
    assert not dest.exists() or not any(dest.rglob("*.sql"))


def test_the_cli_renders_a_verified_record_and_refuses_a_tampered_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = json.dumps(RECORD)
    monkeypatch.setenv(LENDER_IDENTITY_JSON_ENV, text)
    monkeypatch.setenv(LENDER_IDENTITY_SHA256_ENV, lender_identity_digest(text))
    for name in ("MIP_LENDER_NAME", "MIP_LENDER_NMLS_ID", "MIP_TENANT_ID"):
        monkeypatch.delenv(name, raising=False)
    argv = ["--catalog", "mip", "--source", str(SQL), "--dest", str(tmp_path)]

    assert render_sql.main(argv) == 0
    assert "SUMMIT MORTGAGE CORP" not in (tmp_path / "ref" / "lender_dictionary_seed.sql").read_text(encoding="utf-8")
    assert "tenant_rows=pinned" in capsys.readouterr().out

    monkeypatch.setenv(LENDER_IDENTITY_JSON_ENV, text.replace("Hara", "Harra"))
    assert render_sql.main(argv) == 2
    err = capsys.readouterr().err
    assert "render_sql: refused: MIP_LENDER_IDENTITY_JSON" in err and "Hara" not in err


# --- the lender_ref fallback ------------------------------------------------------------


def test_the_demo_fallback_is_the_demo_map_itself() -> None:
    assert lender_ref.tenant_lender_ref_map(reviewed_lender_identity_from_env({})) is lender_ref._LENDER_REF_MAP
    assert lender_ref.LenderRefResolver()._fallback is lender_ref._LENDER_REF_MAP


def test_a_pinned_fallback_agrees_with_the_rendered_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    identity = _pinned()
    fallback = lender_ref.tenant_lender_ref_map(identity)
    rendered = _rows(render_sql.render_tenant_lender_rows(SEED.read_text(encoding="utf-8"), identity))

    assert "Summit Mortgage" not in fallback.values()
    assert fallback["WELLS FARGO BK NA"] == fallback["SUMMIT MTG"] == "O'Hara Home Loans"
    assert {raw: display for raw, display in fallback.items()} == {
        raw: rendered[raw][0] for raw in fallback
    }

    from backend.config.settings import settings

    monkeypatch.setattr(settings, "_reviewed_lender_identity", identity)
    resolver = lender_ref.LenderRefResolver()
    assert resolver._fallback == fallback
