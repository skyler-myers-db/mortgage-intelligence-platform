"""``existing_borrower_ids`` resolves a page of ids in one round-trip.

2026-08-07 platform audit F1: ``GET /api/v1/sales/aging`` answered "is this
borrower still in gold?" with one ``BorrowerRepository.get`` per candidate
row -- up to 250 sequential warehouse statements, measured at 25 s warm and
55 s cold while every other route stayed under 5 s. The batched existence
lookup replaces that loop; these tests pin the properties that make it cheap
and safe (one statement per chunk, bound parameters, malformed ids dropped).
"""
from __future__ import annotations

from typing import Any

from backend.services.repositories.databricks_repo import DatabricksBorrowerRepository


class _RecordingSqlClient:
    def __init__(self, known: set[str]) -> None:
        self.calls: list[tuple[str, dict[str, object] | None]] = []
        self._known = known

    def execute(
        self,
        statement: str,
        params: dict[str, object] | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append((statement, params))
        bound = {str(v) for v in (params or {}).values()}
        return [{"borrower_id": b} for b in sorted(bound & self._known)]


def _borrower_id(index: int) -> str:
    return f"B-{index:013d}"


def test_existence_check_is_one_statement_for_a_full_page() -> None:
    ids = [_borrower_id(i) for i in range(250)]
    live = set(ids[:200])
    client = _RecordingSqlClient(live)
    repo = DatabricksBorrowerRepository(client)  # type: ignore[arg-type]

    found = repo.existing_borrower_ids(ids)

    assert found == live
    assert len(client.calls) == 1


def test_existence_check_chunks_beyond_the_parameter_ceiling() -> None:
    ids = [_borrower_id(i) for i in range(600)]
    client = _RecordingSqlClient(set(ids))
    repo = DatabricksBorrowerRepository(client)  # type: ignore[arg-type]

    assert repo.existing_borrower_ids(ids) == set(ids)
    # 600 ids at a 250-id ceiling is 3 statements -- still O(1) per page, not
    # O(rows), which is the whole point of the fix.
    assert len(client.calls) == 3


def test_existence_check_binds_ids_as_parameters() -> None:
    client = _RecordingSqlClient({_borrower_id(1)})
    repo = DatabricksBorrowerRepository(client)  # type: ignore[arg-type]

    repo.existing_borrower_ids([_borrower_id(1), _borrower_id(2)])

    statement, params = client.calls[0]
    assert "mip.gold.borrower_dossier" in statement
    assert _borrower_id(1) not in statement
    assert set((params or {}).values()) == {_borrower_id(1), _borrower_id(2)}


def test_existence_check_drops_malformed_ids_without_querying_them() -> None:
    client = _RecordingSqlClient({_borrower_id(1)})
    repo = DatabricksBorrowerRepository(client)  # type: ignore[arg-type]

    found = repo.existing_borrower_ids(
        [_borrower_id(1), "' OR 1=1--", "", "not-a-borrower-id"]
    )

    assert found == {_borrower_id(1)}
    _statement, params = client.calls[0]
    assert list((params or {}).values()) == [_borrower_id(1)]


def test_existence_check_skips_the_warehouse_when_there_is_nothing_to_ask() -> None:
    client = _RecordingSqlClient(set())
    repo = DatabricksBorrowerRepository(client)  # type: ignore[arg-type]

    assert repo.existing_borrower_ids([]) == set()
    assert repo.existing_borrower_ids(["bogus"]) == set()
    assert client.calls == []
