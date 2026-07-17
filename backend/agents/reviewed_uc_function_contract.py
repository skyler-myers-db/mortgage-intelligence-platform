"""Runtime-verifiable contract for the Supervisor's reviewed UC functions."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReviewedFunctionSpec:
    leaf_name: str
    comment: str
    return_type: str
    deterministic: bool
    input_params: tuple[tuple[str, str], ...]
    body_sha256: str


def canonical_sql_body(value: str, *, catalog: str = "mip") -> str:
    """Ignore SQL formatting/case outside literals while preserving semantics."""

    body = value.strip().removesuffix(";").strip()
    body = re.sub(r"\ARETURN\b", "", body, count=1, flags=re.IGNORECASE).strip()
    output: list[str] = []
    in_literal = False
    index = 0
    while index < len(body):
        char = body[index]
        if char == "'":
            output.append(char)
            if in_literal and index + 1 < len(body) and body[index + 1] == "'":
                output.append("'")
                index += 2
                continue
            in_literal = not in_literal
        elif in_literal:
            output.append(char)
        elif char == "`":
            pass
        elif not char.isspace():
            output.append(char.casefold())
        index += 1
    if in_literal:
        raise RuntimeError("reviewed UC function body contains an unterminated literal")
    canonical = "".join(output)
    return canonical.replace(f"{catalog.casefold()}.", "mip.")


def sql_body_sha256(value: str, *, catalog: str = "mip") -> str:
    return hashlib.sha256(canonical_sql_body(value, catalog=catalog).encode("utf-8")).hexdigest()


REVIEWED_FUNCTIONS: tuple[ReviewedFunctionSpec, ...] = (
    ReviewedFunctionSpec(
        leaf_name="fn_build_cohort",
        comment=(
            "Reviewed Mortgage Growth Agent broad cohort tool. Counts DISTINCT clip from "
            "gold.borrower_360 using any/all segment semantics and optional state scope. "
            "Read-only."
        ),
        return_type="BIGINT",
        deterministic=True,
        input_params=(
            ("segment_codes", "ARRAY<STRING>"),
            ("segment_mode", "STRING"),
            ("states", "ARRAY<STRING>"),
        ),
        body_sha256="23ccf575302ef32f58d9e66c989ef2528f47fb40f45f27de627ff48ac12e7eb2",
    ),
    ReviewedFunctionSpec(
        leaf_name="fn_segment_counts",
        comment=(
            "Reviewed Mortgage Growth Agent actionability tool. Counts DISTINCT clip from "
            "gold.borrower_360 after the full contact-eligibility gate (marketing eligibility, "
            "opt-in consent, suppression, do-not-contact, frequency cap). Read-only."
        ),
        return_type="BIGINT",
        deterministic=False,
        input_params=(
            ("segment_codes", "ARRAY<STRING>"),
            ("segment_mode", "STRING"),
            ("states", "ARRAY<STRING>"),
        ),
        body_sha256="fa71ee688a738fbddbc15df8226866df0ed177706bec1c2cf8c452b114f7ca8f",
    ),
    ReviewedFunctionSpec(
        leaf_name="fn_lead_queue_url",
        comment=(
            "Reviewed Mortgage Growth Agent Lead Queue handoff tool. Produces a safe app route "
            "from reviewed filters; no outreach or state write."
        ),
        return_type="STRING",
        deterministic=True,
        input_params=(
            ("segment_codes", "ARRAY<STRING>"),
            ("segment_mode", "STRING"),
            ("states", "ARRAY<STRING>"),
        ),
        body_sha256="7d6aedda06ac8cf543144c79f7723061f663f958deb38992511d3d0f5a372ce1",
    ),
)


def _field(value: object, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _enum_text(value: object) -> str:
    return str(_field(value, "value") or value or "").strip()


def assert_reviewed_function(
    details: object,
    *,
    catalog: str,
    spec: ReviewedFunctionSpec,
) -> None:
    """Fail closed unless live UC metadata matches the reviewed function exactly."""

    expected_name = f"{catalog}.gold.{spec.leaf_name}"
    if str(_field(details, "full_name") or "").strip() != expected_name:
        raise RuntimeError(f"reviewed UC function identity drifted: {expected_name}")
    if str(_field(details, "comment") or "").strip() != spec.comment:
        raise RuntimeError(f"reviewed UC function comment drifted: {expected_name}")
    if bool(_field(details, "is_deterministic")) is not spec.deterministic:
        raise RuntimeError(f"reviewed UC function determinism drifted: {expected_name}")
    return_type = _enum_text(_field(details, "data_type")).upper()
    if return_type != spec.return_type:
        raise RuntimeError(f"reviewed UC function return type drifted: {expected_name}")
    parameter_container = _field(details, "input_params")
    parameters = list(_field(parameter_container, "parameters") or [])
    normalized_parameters = tuple(
        (
            str(_field(parameter, "name") or "").strip(),
            str(_field(parameter, "type_text") or _enum_text(_field(parameter, "type_name")) or "")
            .replace(" ", "")
            .upper(),
            int(_field(parameter, "position") or 0),
        )
        for parameter in parameters
    )
    expected_parameters = tuple(
        (name, type_text, position) for position, (name, type_text) in enumerate(spec.input_params)
    )
    if normalized_parameters != expected_parameters:
        raise RuntimeError(f"reviewed UC function parameters drifted: {expected_name}")
    definition = str(_field(details, "routine_definition") or "")
    if not definition or sql_body_sha256(definition, catalog=catalog) != spec.body_sha256:
        raise RuntimeError(f"reviewed UC function body drifted: {expected_name}")


def assert_reviewed_function_set(workspace: object, *, catalog: str) -> None:
    functions = _field(workspace, "functions")
    get_function = _field(functions, "get")
    if not callable(get_function):
        raise RuntimeError("Unity Catalog function metadata client is unavailable")
    for spec in REVIEWED_FUNCTIONS:
        name = f"{catalog}.gold.{spec.leaf_name}"
        assert_reviewed_function(get_function(name), catalog=catalog, spec=spec)
