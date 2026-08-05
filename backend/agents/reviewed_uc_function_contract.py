"""Runtime-verifiable contract for the Supervisor's reviewed UC functions."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

_ASCII_WHITESPACE = " \t\n\r\f\v"


def _strip_ascii_whitespace(value: str) -> str:
    return value.strip(_ASCII_WHITESPACE)


@dataclass(frozen=True)
class ReviewedFunctionSpec:
    leaf_name: str
    comment: str
    return_type: str
    deterministic: bool
    sql_data_access: str
    input_params: tuple[tuple[str, str], ...]
    body_sha256: str


def _outer_parentheses_enclose_expression(value: str) -> bool:
    if len(value) < 2 or value[0] != "(" or value[-1] != ")":
        return False
    depth = 0
    in_literal = False
    in_delimited_identifier = False
    index = 0
    while index < len(value):
        char = value[index]
        if char == "'" and not in_delimited_identifier:
            if in_literal and index + 1 < len(value) and value[index + 1] == "'":
                index += 2
                continue
            in_literal = not in_literal
        elif char == "`" and not in_literal:
            if (
                in_delimited_identifier
                and index + 1 < len(value)
                and value[index + 1] == "`"
            ):
                index += 2
                continue
            in_delimited_identifier = not in_delimited_identifier
        elif not in_literal and not in_delimited_identifier:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth < 0 or (depth == 0 and index != len(value) - 1):
                    return False
        index += 1
    return not in_literal and not in_delimited_identifier and depth == 0


def _canonicalize_select_outer_parentheses(value: str) -> str:
    """Collapse only provider-added wrappers around a scalar SELECT body."""

    original = value
    core = value
    wrapper_count = 0
    while _outer_parentheses_enclose_expression(core):
        core = _strip_ascii_whitespace(core[1:-1])
        wrapper_count += 1
    if wrapper_count and re.match(r"\ASELECT\b", core, flags=re.IGNORECASE):
        return f"({core})"
    return original


def _quoted_sql_token(
    value: str,
    index: int,
    *,
    delimiter: str,
    label: str,
) -> tuple[str, int]:
    output = [delimiter]
    index += 1
    while index < len(value):
        char = value[index]
        output.append(char)
        if char == delimiter:
            if index + 1 < len(value) and value[index + 1] == delimiter:
                output.append(delimiter)
                index += 2
                continue
            return "".join(output), index + 1
        index += 1
    raise RuntimeError(f"reviewed UC function body contains an unterminated {label}")


def _is_ascii_sql_word_char(char: str) -> bool:
    return char.isascii() and (char.isalnum() or char in {"_", "$"})


def _sql_body_tokens(value: str) -> list[tuple[str, str]]:
    """Tokenize enough SQL to preserve every semantic identifier boundary."""

    tokens: list[tuple[str, str]] = []
    index = 0
    while index < len(value):
        char = value[index]
        if not char.isascii():
            raise RuntimeError(
                "reviewed UC function body contains unsupported non-ASCII SQL"
            )
        if char.isspace():
            index += 1
            continue
        if char == "'":
            token, index = _quoted_sql_token(
                value,
                index,
                delimiter="'",
                label="literal",
            )
            tokens.append(("literal", token))
            continue
        if char == "`":
            token, index = _quoted_sql_token(
                value,
                index,
                delimiter="`",
                label="delimited identifier",
            )
            tokens.append(("identifier", token))
            continue
        if value.startswith("--", index) or value.startswith("/*", index):
            raise RuntimeError("reviewed UC function body comments are unsupported")
        if char == '"':
            raise RuntimeError(
                "reviewed UC function body double-quoted strings are unsupported"
            )
        if _is_ascii_sql_word_char(char):
            end = index + 1
            while end < len(value) and _is_ascii_sql_word_char(value[end]):
                end += 1
            tokens.append(("word", value[index:end].lower()))
            index = end
            continue
        tokens.append(("symbol", char))
        index += 1
    return tokens


def _canonical_sql_tokens(
    tokens: list[tuple[str, str]],
    *,
    catalog: str,
) -> str:
    catalog_token = catalog.lower()
    if not re.fullmatch(r"[a-z0-9_]+", catalog_token):
        raise RuntimeError("reviewed UC function catalog is not a governed identifier")
    canonical: list[str] = []
    for index, (kind, value) in enumerate(tokens):
        if (
            kind == "word"
            and value == catalog_token
            and index + 1 < len(tokens)
            and tokens[index + 1] == ("symbol", ".")
        ):
            kind = "catalog"
            value = ""
        code = {
            "catalog": "c",
            "identifier": "i",
            "literal": "l",
            "symbol": "s",
            "word": "w",
        }[kind]
        canonical.append(f"{code}{len(value)}:{value}")
    return "".join(canonical)


def canonical_sql_body(value: str, *, catalog: str = "mip") -> str:
    """Ignore formatting/case without collapsing SQL token boundaries."""

    body = _strip_ascii_whitespace(value)
    body = _strip_ascii_whitespace(body.removesuffix(";"))
    body = _strip_ascii_whitespace(
        re.sub(r"\ARETURN\b", "", body, count=1, flags=re.IGNORECASE)
    )
    body = _canonicalize_select_outer_parentheses(body)
    return _canonical_sql_tokens(_sql_body_tokens(body), catalog=catalog)


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
        sql_data_access="READS_SQL_DATA",
        input_params=(
            ("segment_codes", "ARRAY<STRING>"),
            ("segment_mode", "STRING"),
            ("states", "ARRAY<STRING>"),
        ),
        body_sha256="bd05e44a1e5d45f98a643fb909e36bf9b7f11ac977b982f4ebf5ad708ff6e450",
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
        sql_data_access="READS_SQL_DATA",
        input_params=(
            ("segment_codes", "ARRAY<STRING>"),
            ("segment_mode", "STRING"),
            ("states", "ARRAY<STRING>"),
        ),
        body_sha256="9522df24a285d352f9eb59e36c0d92307de01f7881da2a2f7425f275c102a200",
    ),
    ReviewedFunctionSpec(
        leaf_name="fn_lead_queue_url",
        comment=(
            "Reviewed Mortgage Growth Agent Lead Queue handoff tool. Produces a safe app route "
            "from reviewed filters; no outreach or state write."
        ),
        return_type="STRING",
        deterministic=True,
        sql_data_access="CONTAINS_SQL",
        input_params=(
            ("segment_codes", "ARRAY<STRING>"),
            ("segment_mode", "STRING"),
            ("states", "ARRAY<STRING>"),
        ),
        body_sha256="1f4257005a15da4e61e0963e6c1652c319b2128de9755aea9498cd1b02f6738b",
    ),
)


def _field(value: object, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _enum_text(value: object) -> str:
    return str(_field(value, "value") or value or "")


_SDK_RETURN_TYPE_CONTRACT = {
    # Unity Catalog exposes SQL BIGINT through the SDK's coarse
    # ColumnTypeName.LONG enum while preserving the exact SQL spelling in
    # FunctionInfo.full_data_type. Require both fields so an inconsistent or
    # incomplete metadata response cannot satisfy the reviewed contract.
    "BIGINT": ("LONG", "BIGINT"),
    "STRING": ("STRING", "STRING"),
}


def _normalized_sql_type(value: object) -> str:
    return _enum_text(value).upper()


def _expected_parameter_type_json(name: str, type_text: str) -> dict[str, Any]:
    if type_text == "ARRAY<STRING>":
        parameter_type: object = {
            "type": "array",
            "elementType": "string",
            "containsNull": True,
        }
    elif type_text == "STRING":
        parameter_type = "string"
    else:
        raise RuntimeError(f"unsupported reviewed UC function parameter type: {type_text}")
    return {
        "name": name,
        "type": parameter_type,
        "nullable": True,
        "metadata": {},
    }


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    output: dict[str, object] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("duplicate JSON key")
        output[key] = value
    return output


def _canonical_strict_json(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value, object_pairs_hook=_reject_duplicate_json_keys)
    except (json.JSONDecodeError, ValueError):
        return None
    return json.dumps(parsed, sort_keys=True, separators=(",", ":"))


def assert_reviewed_function(
    details: object,
    *,
    catalog: str,
    spec: ReviewedFunctionSpec,
    expected_owner: str,
    allow_legacy_segment_determinism: bool = False,
) -> None:
    """Fail closed unless live UC metadata matches the reviewed function exactly."""

    expected_name = f"{catalog}.gold.{spec.leaf_name}"
    if (
        not expected_owner
        or expected_owner != _strip_ascii_whitespace(expected_owner)
        or str(_field(details, "owner") or "") != expected_owner
    ):
        raise RuntimeError(f"reviewed UC function owner drifted: {expected_name}")
    if (
        str(_field(details, "full_name") or "") != expected_name
    ):
        raise RuntimeError(f"reviewed UC function identity drifted: {expected_name}")
    if str(_field(details, "comment") or "") != spec.comment:
        raise RuntimeError(f"reviewed UC function comment drifted: {expected_name}")
    identity_metadata = (
        (_field(details, "catalog_name"), catalog),
        (_field(details, "schema_name"), "gold"),
        (_field(details, "name"), spec.leaf_name),
        (_field(details, "specific_name"), spec.leaf_name),
    )
    if any(
        str(live or "") != expected
        for live, expected in identity_metadata
    ):
        raise RuntimeError(f"reviewed UC function identity drifted: {expected_name}")
    execution_metadata = (
        (_field(details, "parameter_style"), "S"),
        (_field(details, "routine_body"), "SQL"),
        (_field(details, "security_type"), "DEFINER"),
        (_field(details, "sql_data_access"), spec.sql_data_access),
    )
    if any(
        _normalized_sql_type(live) != expected
        for live, expected in execution_metadata
    ) or any(
        _field(details, field) is not None
        for field in (
            "external_language",
            "external_name",
            "is_null_call",
            "return_params",
            "sql_path",
        )
    ):
        raise RuntimeError(
            f"reviewed UC function execution metadata drifted: {expected_name}"
        )
    live_deterministic = _field(details, "is_deterministic")
    legacy_determinism_matches = (
        allow_legacy_segment_determinism
        and spec.leaf_name == "fn_segment_counts"
        and live_deterministic is True
    )
    if (
        type(allow_legacy_segment_determinism) is not bool
        or type(live_deterministic) is not bool
        or (
            live_deterministic is not spec.deterministic
            and not legacy_determinism_matches
        )
    ):
        raise RuntimeError(f"reviewed UC function determinism drifted: {expected_name}")
    expected_return_type = _SDK_RETURN_TYPE_CONTRACT.get(
        _normalized_sql_type(spec.return_type)
    )
    live_return_type = (
        _normalized_sql_type(_field(details, "data_type")),
        _normalized_sql_type(_field(details, "full_data_type")),
    )
    if expected_return_type is None or live_return_type != expected_return_type:
        raise RuntimeError(f"reviewed UC function return type drifted: {expected_name}")
    parameter_container = _field(details, "input_params")
    parameters = list(_field(parameter_container, "parameters") or [])
    if len(parameters) != len(spec.input_params):
        raise RuntimeError(f"reviewed UC function parameters drifted: {expected_name}")
    for expected_position, (parameter, expected_parameter) in enumerate(
        zip(parameters, spec.input_params, strict=True)
    ):
        expected_parameter_name, expected_parameter_type = expected_parameter
        live_position = _field(parameter, "position")
        if (
            type(live_position) is not int
            or _field(parameter, "parameter_default") is not None
            or _field(parameter, "comment") is not None
            or _field(parameter, "parameter_mode") is not None
            or _field(parameter, "type_interval_type") is not None
        ):
            raise RuntimeError(
                f"reviewed UC function parameters drifted: {expected_name}"
            )
        type_text = (
            str(_field(parameter, "type_text") or "").upper()
        )
        expected_type_name = (
            "ARRAY" if expected_parameter_type == "ARRAY<STRING>" else "STRING"
        )
        type_json = _canonical_strict_json(_field(parameter, "type_json"))
        expected_type_json = json.dumps(
            _expected_parameter_type_json(
                expected_parameter_name,
                expected_parameter_type,
            ),
            sort_keys=True,
            separators=(",", ":"),
        )
        if (
            str(_field(parameter, "name") or "") != expected_parameter_name
            or type_text != expected_parameter_type
            or live_position != expected_position
            or _normalized_sql_type(_field(parameter, "parameter_type")) != "PARAM"
            or _normalized_sql_type(_field(parameter, "type_name"))
            != expected_type_name
            or type(_field(parameter, "type_precision")) is not int
            or _field(parameter, "type_precision") != 0
            or type(_field(parameter, "type_scale")) is not int
            or _field(parameter, "type_scale") != 0
            or type_json != expected_type_json
        ):
            raise RuntimeError(
                f"reviewed UC function parameters drifted: {expected_name}"
            )
    definition = str(_field(details, "routine_definition") or "")
    if not definition or sql_body_sha256(definition, catalog=catalog) != spec.body_sha256:
        raise RuntimeError(f"reviewed UC function body drifted: {expected_name}")


def authenticated_reviewed_function_owner(
    workspace: object,
    *,
    catalog: str,
) -> str:
    """Resolve one function owner and prove it is the authenticated deployer."""

    functions = _field(workspace, "functions")
    get_function = _field(functions, "get")
    current_user = _field(workspace, "current_user")
    get_current_user = _field(current_user, "me")
    if not callable(get_function) or not callable(get_current_user):
        raise RuntimeError("reviewed UC function deployer identity is unavailable")
    identity = get_current_user()
    raw_aliases = [
        str(_field(identity, field) or "")
        for field in ("application_id", "user_name")
    ]
    if any(value and value != value.strip() for value in raw_aliases):
        raise RuntimeError("reviewed UC function deployer identity is not canonical")
    aliases = set(raw_aliases) - {""}
    owners = {
        str(
            _field(
                get_function(f"{catalog}.gold.{spec.leaf_name}"),
                "owner",
            )
            or ""
        )
        for spec in REVIEWED_FUNCTIONS
    }
    if (
        len(owners) != 1
        or any(not owner or owner != owner.strip() for owner in owners)
        or not aliases
        or not owners.issubset(aliases)
    ):
        raise RuntimeError(
            "reviewed UC functions are not owned by the authenticated deployer"
        )
    return owners.pop()


def assert_reviewed_function_set(
    workspace: object,
    *,
    catalog: str,
    expected_owner: str,
    allow_legacy_segment_determinism: bool = False,
) -> None:
    functions = _field(workspace, "functions")
    get_function = _field(functions, "get")
    if not callable(get_function):
        raise RuntimeError("Unity Catalog function metadata client is unavailable")
    for spec in REVIEWED_FUNCTIONS:
        name = f"{catalog}.gold.{spec.leaf_name}"
        assert_reviewed_function(
            get_function(name),
            catalog=catalog,
            spec=spec,
            expected_owner=expected_owner,
            allow_legacy_segment_determinism=allow_legacy_segment_determinism,
        )
