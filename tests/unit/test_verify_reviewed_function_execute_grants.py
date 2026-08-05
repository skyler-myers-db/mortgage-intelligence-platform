from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.databricks import verify_reviewed_function_execute_grants as verifier


class _Grants:
    def __init__(self, privilege: object, *, assignment_principal: str | None = None) -> None:
        self.privilege = privilege
        self.assignment_principal = assignment_principal
        self.calls: list[tuple[str, str, str, int, str | None]] = []

    def get_effective(
        self,
        securable_type: str,
        full_name: str,
        *,
        principal: str,
        max_results: int,
        page_token: str | None,
    ) -> object:
        self.calls.append(
            (securable_type, full_name, principal, max_results, page_token)
        )
        return SimpleNamespace(
            privilege_assignments=[
                SimpleNamespace(
                    principal=self.assignment_principal or principal,
                    privileges=[self.privilege],
                )
            ],
            next_page_token=None,
        )


def _privilege(
    name: str = "EXECUTE",
    *,
    inherited_type: str | None = None,
    inherited_name: str | None = None,
) -> object:
    return SimpleNamespace(
        privilege=name,
        inherited_from_type=inherited_type,
        inherited_from_name=inherited_name,
    )


def test_verifies_six_exact_direct_effective_execute_grants() -> None:
    grants = _Grants(_privilege())

    verifier.verify_reviewed_function_execute_grants(
        SimpleNamespace(grants=grants),
        catalog="mip_pr105_staging",
        principals=("app-client", "runtime-client"),
    )

    assert len(grants.calls) == 6
    assert {call[0] for call in grants.calls} == {"function"}
    assert {call[2] for call in grants.calls} == {"app-client", "runtime-client"}
    assert {call[1].rsplit(".", 1)[-1] for call in grants.calls} == {
        "fn_build_cohort",
        "fn_segment_counts",
        "fn_lead_queue_url",
    }
    assert {call[3:] for call in grants.calls} == {(1000, None)}


@pytest.mark.parametrize(
    "privilege",
    (
        _privilege("SELECT"),
        _privilege(inherited_type="SCHEMA", inherited_name="mip.gold"),
    ),
)
def test_rejects_missing_or_inherited_execute(privilege: object) -> None:
    with pytest.raises(RuntimeError, match="exact effective EXECUTE postflight failed"):
        verifier.verify_reviewed_function_execute_grants(
            SimpleNamespace(grants=_Grants(privilege)),
            catalog="mip",
            principals=("app-client", "runtime-client"),
        )


def test_rejects_execute_attributed_to_another_principal() -> None:
    with pytest.raises(RuntimeError, match="unexpected principal"):
        verifier.verify_reviewed_function_execute_grants(
            SimpleNamespace(
                grants=_Grants(_privilege(), assignment_principal="unexpected-client")
            ),
            catalog="mip",
            principals=("app-client", "runtime-client"),
        )


class _PagedGrants:
    def __init__(self, *, repeat_token: bool = False) -> None:
        self.repeat_token = repeat_token
        self.tokens: list[str | None] = []

    def get_effective(
        self,
        _securable_type: str,
        _full_name: str,
        *,
        principal: str,
        max_results: int,
        page_token: str | None,
    ) -> object:
        assert max_results == 1000
        self.tokens.append(page_token)
        if page_token is None:
            privileges = [_privilege()]
            next_token = "page-2"
        else:
            privileges = [] if self.repeat_token else [_privilege("SELECT")]
            next_token = "page-2" if self.repeat_token else None
        return SimpleNamespace(
            privilege_assignments=[
                SimpleNamespace(principal=principal, privileges=privileges)
            ],
            next_page_token=next_token,
        )


def test_consumes_later_pages_and_rejects_extra_privilege() -> None:
    grants = _PagedGrants()

    with pytest.raises(RuntimeError, match="exact effective EXECUTE postflight failed"):
        verifier.verify_reviewed_function_execute_grants(
            SimpleNamespace(grants=grants),
            catalog="mip",
            principals=("app-client", "runtime-client"),
        )

    assert grants.tokens == [None, "page-2"]


def test_rejects_repeated_effective_grant_page_token() -> None:
    grants = _PagedGrants(repeat_token=True)

    with pytest.raises(RuntimeError, match="repeated a page token"):
        verifier.verify_reviewed_function_execute_grants(
            SimpleNamespace(grants=grants),
            catalog="mip",
            principals=("app-client", "runtime-client"),
        )

    assert grants.tokens == [None, "page-2"]


@pytest.mark.parametrize(
    ("catalog", "principals"),
    (
        ("mip.bad", ("app-client", "runtime-client")),
        ("mip", ("", "runtime-client")),
        ("mip", ("same-client", "same-client")),
    ),
)
def test_rejects_invalid_target_controls(
    catalog: str,
    principals: tuple[str, str],
) -> None:
    with pytest.raises(ValueError):
        verifier.verify_reviewed_function_execute_grants(
            SimpleNamespace(grants=_Grants(_privilege())),
            catalog=catalog,
            principals=principals,
        )
