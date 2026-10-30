"""The borrower E2E audit reads the raw share the install's silver lift reads.

``tools/e2e_borrower_audit_fetch.fetch_raw_inputs`` once named the canonical
share in its SQL, so for an install whose share is mounted under other names
it audited gold against a share the install does not read. It now takes the
source (``source_names.from_env`` in the CLI) with no default.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from backend.config.source_names import CANONICAL, CotalitySource
from tools import e2e_borrower_audit
from tools.e2e_borrower_audit_fetch import fetch_raw_inputs
from tools.e2e_borrower_audit_report import render_report

ACME = CotalitySource(catalog="acme_src", schema="feed", table_prefix="acme_")


class _Recorder:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute_one(self, statement: str, params: dict[str, Any]) -> dict[str, Any]:
        assert params == {"clip": "123"}
        self.statements.append(statement)
        return {}


def _raw_share_statements(cotality: CotalitySource) -> list[str]:
    client = _Recorder()
    fetch_raw_inputs(client, "123", cotality=cotality)  # type: ignore[arg-type]
    return [statement for statement in client.statements if ".silver." not in statement]


def test_the_raw_inputs_read_the_configured_share() -> None:
    statements = _raw_share_statements(ACME)

    assert len(statements) == 2
    assert "FROM acme_src.feed.acme_voluntary_lien_status_marketing_v2 " in statements[0]
    assert "FROM acme_src.feed.acme_property_domain_v3 " in statements[1]
    for name in (CANONICAL.catalog, CANONICAL.schema, CANONICAL.table_prefix):
        assert not [statement for statement in statements if name in statement], name


def test_the_canonical_share_is_read_by_its_canonical_names() -> None:
    statements = _raw_share_statements(CANONICAL)

    assert "FROM cotality_mortgage_data.corelogic.entrada_eval_voluntary_lien_status_marketing_v2 " in (
        statements[0]
    )
    assert "FROM cotality_mortgage_data.corelogic.entrada_eval_property_domain_v3 " in statements[1]


def test_the_report_names_the_tables_the_raw_rows_came_from() -> None:
    acme = render_report([], seed=1, sample_size=0, coverage_states=[], cotality=ACME)
    canonical = render_report([], seed=1, sample_size=0, coverage_states=[], cotality=CANONICAL)

    assert "`acme_voluntary_lien_status_marketing_v2` +" in acme and "`acme_property_domain_v3`" in acme
    assert CANONICAL.table_prefix not in acme
    assert "`entrada_eval_voluntary_lien_status_marketing_v2` +" in canonical
    assert "`entrada_eval_property_domain_v3`) and the latest FRED" in canonical


def test_the_source_has_no_default() -> None:
    assert inspect.signature(fetch_raw_inputs).parameters["cotality"].default is inspect.Parameter.empty
    for function in (e2e_borrower_audit.run_audit, render_report):
        assert inspect.signature(function).parameters["cotality"].default is inspect.Parameter.empty


def test_the_cli_refuses_an_invalid_source_before_building_a_client(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_client() -> None:
        raise AssertionError("no client is built for a refused source")

    monkeypatch.setattr(e2e_borrower_audit, "build_client", no_client)
    monkeypatch.setattr(e2e_borrower_audit.sys, "argv", ["e2e_borrower_audit.py"])
    monkeypatch.setenv("MIP_COTALITY_SOURCE_CATALOG", "<cotality-source-catalog>")

    assert e2e_borrower_audit.main() == 2
    assert "MIP_COTALITY_SOURCE_CATALOG is a template value" in capsys.readouterr().err
