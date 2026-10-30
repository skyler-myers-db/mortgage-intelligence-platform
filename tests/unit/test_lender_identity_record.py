"""backend/schemas/lender_identity_record.py: the digest-pinned lender record.

The invariant: a customer lender identity is never a bare runtime string. It
is accepted only as a record that parses under the reviewed shapes and whose
canonical sha256 equals its pin, the deploying maintainer's exact-digest
acknowledgement. With neither pinned name set, the resolver is exactly the
code-registry path.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from backend.schemas import lender_identity_record as record_module
from backend.schemas.lender_identity import (
    effective_public_tenant_id,
    validate_public_lender_identity,
)
from backend.schemas.lender_identity_record import (
    LENDER_IDENTITY_JSON_ENV,
    LENDER_IDENTITY_SHA256_ENV,
    LenderIdentityRefused,
    as_configured,
    canonical_lender_identity_json,
    lender_identity_digest,
    parse_lender_identity_record,
    present_env,
    reviewed_lender_identity_from_env,
    tenant_lender_dictionary,
)
from tests.fixtures.client_profiles import LENDER_IDENTITY
from tools.clients import shapes

RECORD: dict[str, Any] = dict(LENDER_IDENTITY)
MARK = {
    "sha256": "ab" * 32,
    "media_type": "image/png",
    "width": 64,
    "height": 32,
    "authorization_ref": "BRAND-AUTH/2026-0042",
    "reviewed_on": "2026-09-30",
}


def _text(record: dict[str, Any] | None = None) -> str:
    return json.dumps(RECORD if record is None else record)


def _pinned(record: dict[str, Any] | None = None, **legacy: str) -> dict[str, str]:
    text = _text(record)
    return {
        LENDER_IDENTITY_JSON_ENV: text,
        LENDER_IDENTITY_SHA256_ENV: lender_identity_digest(text),
        **legacy,
    }


def _refusal(env: dict[str, str]) -> str:
    with pytest.raises(LenderIdentityRefused) as caught:
        reviewed_lender_identity_from_env(env)
    return str(caught.value)


# --- the pinned path ------------------------------------------------------------


def test_a_pinned_record_resolves_to_its_identity_and_digest() -> None:
    identity = reviewed_lender_identity_from_env(_pinned())

    assert (identity.lender_name, identity.nmls_id, identity.tenant_id) == (
        RECORD["legal_name"],
        RECORD["nmls_id"],
        RECORD["tenant_id"],
    )
    assert identity.tenant_aliases == ("FICTIONAL HARBOR SERVICING",)
    assert identity.pinned and identity.mark is None
    canonical = json.dumps(RECORD, sort_keys=True, separators=(",", ":"))
    assert identity.canonical_json == canonical
    assert identity.digest == hashlib.sha256(canonical.encode()).hexdigest()


def test_the_digest_ignores_layout_but_not_content() -> None:
    pretty = json.dumps(dict(reversed(list(RECORD.items()))), indent=4)
    padded = json.dumps({**RECORD, "legal_name": f"  {RECORD['legal_name']} "})

    assert lender_identity_digest(pretty) == lender_identity_digest(_text())
    assert lender_identity_digest(padded) == lender_identity_digest(_text())
    assert lender_identity_digest(_text({**RECORD, "nmls_id": "424243"})) != lender_identity_digest(_text())


def test_the_canonical_form_is_the_client_profile_view_of_the_same_field() -> None:
    from tools.clients.json_fields import LenderIdentity
    from tools.clients.profile import _env_text

    profile_text = _env_text(LENDER_IDENTITY_JSON_ENV, LenderIdentity.model_validate(RECORD))
    assert profile_text == parse_lender_identity_record(_text()).canonical_json


@pytest.mark.parametrize(
    ("key", "tampered"),
    [
        ("legal_name", "Fictional Harbour Lending"),
        ("nmls_id", "424243"),
        ("tenant_id", "fictional_harbour"),
        ("servicer_aliases", ["Fictional Harbor Servicing", "Other Servicing"]),
        ("reviewed_by", "someone@lender.example"),
        ("reviewed_at", "2026-10-02"),
    ],
)
def test_a_tampered_record_refuses_against_the_reviewed_pin(key: str, tampered: object) -> None:
    env = _pinned()
    env[LENDER_IDENTITY_JSON_ENV] = _text({**RECORD, key: tampered})

    assert "does not equal MIP_LENDER_IDENTITY_SHA256" in _refusal(env)


def test_a_one_character_tamper_of_the_raw_text_refuses() -> None:
    env = _pinned()
    env[LENDER_IDENTITY_JSON_ENV] = env[LENDER_IDENTITY_JSON_ENV].replace("Harbor Lending", "Harbos Lending")

    assert "does not equal" in _refusal(env)


@pytest.mark.parametrize(
    ("env", "rule"),
    [
        ({LENDER_IDENTITY_JSON_ENV: _text()}, "is required to pin"),
        ({LENDER_IDENTITY_JSON_ENV: _text(), LENDER_IDENTITY_SHA256_ENV: "  "}, "is required to pin"),
        ({LENDER_IDENTITY_SHA256_ENV: "0" * 64}, "is unset"),
        ({LENDER_IDENTITY_JSON_ENV: _text(), LENDER_IDENTITY_SHA256_ENV: "AB" * 32}, "64 lowercase hex"),
        ({LENDER_IDENTITY_JSON_ENV: _text(), LENDER_IDENTITY_SHA256_ENV: "0" * 64}, "does not equal"),
    ],
)
def test_an_unpinned_or_mispinned_record_refuses(env: dict[str, str], rule: str) -> None:
    # The deferred owner signature's stand-in is the exact digest pin.
    assert rule in _refusal(env)


@pytest.mark.parametrize(
    ("legacy", "accepted"),
    [
        ({"MIP_LENDER_NAME": "Fictional  Harbor Lending"}, True),
        ({"MIP_LENDER_NMLS_ID": " 424242 ", "MIP_TENANT_ID": "fictional_harbor"}, True),
        ({"MIP_LENDER_NAME": "Summit Mortgage"}, False),
        ({"MIP_LENDER_NMLS_ID": "123456"}, False),
        ({"MIP_TENANT_ID": "summit"}, False),
    ],
)
def test_a_legacy_name_set_beside_the_record_must_equal_it(legacy: dict[str, str], accepted: bool) -> None:
    env = _pinned(**legacy)
    if accepted:
        assert reviewed_lender_identity_from_env(env).lender_name == RECORD["legal_name"]
    else:
        assert "must equal the pinned MIP_LENDER_IDENTITY_JSON record" in _refusal(env)


_SHAPE_CASES: list[tuple[str, object, str]] = [
    ("version", 2, "version must be 1"),
    ("version", True, "version must be 1"),
    ("version", 1.0, "version must be 1"),
    ("legal_name", "Romani", "public lender organization name"),
    ("legal_name", 7, "legal_name must be a string"),
    ("nmls_id", "000123", "4-12 digit nonzero NMLS"),
    # Review of #298: a Unicode digit (Arabic-Indic, fullwidth) is not an NMLS
    # digit, though Python's \d matches it; it would render into disclosures.
    ("nmls_id", "1٢٣٤٥٦", "4-12 digit nonzero NMLS"),
    ("nmls_id", "42424２", "4-12 digit nonzero NMLS"),
    ("tenant_id", "Fictional-Harbor", "tenant_id must be a lowercase identifier"),
    ("servicer_aliases", [], "must list 1-32 servicer names"),
    ("servicer_aliases", "Fictional Harbor Servicing", "must list 1-32 servicer names"),
    ("servicer_aliases", ["Fictional Harbor", "FICTIONAL  harbor"], "must not repeat a servicer name"),
    ("servicer_aliases", [f"Servicer {n}" for n in range(33)], "must list 1-32 servicer names"),
    ("servicer_aliases", ["Fictional; DROP"], "servicer names of at most 100 characters"),
    ("servicer_aliases", [7], "must hold strings"),
    ("reviewed_by", "nobody-at-all", "the reviewer's email address"),
    ("reviewed_at", "yesterday", "an ISO-8601 date"),
]


@pytest.mark.parametrize(("key", "value", "rule"), _SHAPE_CASES)
def test_each_record_shape_rule_refuses_without_naming_the_value(key: str, value: object, rule: str) -> None:
    with pytest.raises(LenderIdentityRefused) as caught:
        parse_lender_identity_record(_text({**RECORD, key: value}))

    message = str(caught.value)
    assert message.startswith("MIP_LENDER_IDENTITY_JSON: ") and rule in message
    if isinstance(value, str) and len(value) > 3:
        assert value not in message


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("[]", "must be one JSON object"),
        ("{not json", "must be one JSON object"),
        ('{"version": NaN}', "NaN or Infinity"),
        ('{"version": 1, "version": 1}', "repeats a key"),
        (json.dumps({**RECORD, "role": "admin"}), "must hold exactly"),
        (json.dumps({key: value for key, value in RECORD.items() if key != "reviewed_by"}), "must hold exactly"),
        (json.dumps({**RECORD, "reviewed_by": "x" * 20000}), "at most 16384 characters"),
    ],
)
def test_a_malformed_record_refuses(text: str, rule: str) -> None:
    with pytest.raises(LenderIdentityRefused, match=rule):
        parse_lender_identity_record(text)


@pytest.mark.parametrize(
    "summit_part",
    [
        {"legal_name": "SUMMIT MORTGAGE"},
        {"nmls_id": "123456"},
        {"tenant_id": "summit"},
    ],
)
def test_no_part_of_the_summit_demo_identity_is_ever_a_pinned_record(summit_part: dict[str, str]) -> None:
    with pytest.raises(LenderIdentityRefused, match="Summit Mortgage demo identity"):
        parse_lender_identity_record(_text({**RECORD, **summit_part}))


def test_a_record_that_breaks_the_sms_disclosure_budget_refuses() -> None:
    long_name = "The Really Extremely Long Corporate National Home Mortgage"
    with pytest.raises(LenderIdentityRefused, match="160-character SMS disclosure budget"):
        parse_lender_identity_record(_text({**RECORD, "legal_name": long_name, "nmls_id": "123456789012"}))


def test_a_record_mark_parses_under_the_lender_mark_rules() -> None:
    identity = reviewed_lender_identity_from_env(_pinned({**RECORD, "mark": MARK}))

    assert identity.mark is not None
    assert (identity.mark.sha256, identity.mark.width, identity.mark.height) == (MARK["sha256"], 64, 32)
    assert identity.mark.nmls_id == RECORD["nmls_id"]
    assert identity.mark.reviewed_on == date(2026, 9, 30)


@pytest.mark.parametrize(
    ("change", "rule"),
    [
        ({"sha256": "AB" * 32}, "sha256 is not 64 lowercase hex"),
        ({"media_type": "image/gif"}, "media_type is not PNG or WebP"),
        ({"authorization_ref": "https://brand.example/ok"}, "authorization_ref is not a plain reference"),
        ({"reviewed_on": (date.today() + timedelta(days=1)).isoformat()}, "reviewed_on is not a past"),
        ({"reviewed_on": "soon"}, "ISO-8601 calendar date"),
        ({"height": 20}, "not a reviewable mark size"),
        ({"width": 65, "height": 32.0}, "whole numbers"),
        ({"extra": 1}, "must hold exactly sha256"),
    ],
)
def test_a_malformed_record_mark_refuses(change: dict[str, object], rule: str) -> None:
    with pytest.raises(LenderIdentityRefused, match=rule):
        parse_lender_identity_record(_text({**RECORD, "mark": {**MARK, **change}}))


def test_a_null_mark_is_refused_rather_than_read_as_absent() -> None:
    with pytest.raises(LenderIdentityRefused, match="mark must be an object"):
        parse_lender_identity_record(_text({**RECORD, "mark": None}))


def test_the_tenant_dictionary_maps_each_alias_to_the_legal_name() -> None:
    identity = reviewed_lender_identity_from_env(
        _pinned({**RECORD, "servicer_aliases": ["Fictional Harbor Servicing", "Fictional  Harbor MTG"]})
    )

    assert tenant_lender_dictionary(identity) == {
        "FICTIONAL HARBOR SERVICING": RECORD["legal_name"],
        "FICTIONAL HARBOR MTG": RECORD["legal_name"],
    }


def test_the_record_shapes_are_the_client_profile_shapes() -> None:
    assert record_module._ALIAS_RE.pattern == shapes.TITLE.pattern
    assert record_module._EMAIL_RE.pattern == shapes.EMAIL.pattern
    assert record_module._ISO_DATE_RE.pattern == shapes.ISO_DATE.pattern


# --- the legacy path ----------------------------------------------------------------


_LEGACY_SAMPLES: list[dict[str, str]] = [
    {},
    {"MIP_LENDER_NAME": "Summit Mortgage"},
    {"MIP_LENDER_NAME": "  Summit   Mortgage "},
    {"MIP_LENDER_NAME": "Summit Mortgage", "MIP_LENDER_NMLS_ID": "123456", "MIP_TENANT_ID": "summit"},
    {"MIP_LENDER_NMLS_ID": "123456"},
    {"MIP_TENANT_ID": ""},
    {"MIP_TENANT_ID": "custom_ns"},
    {LENDER_IDENTITY_JSON_ENV: "", LENDER_IDENTITY_SHA256_ENV: "   "},
]


@pytest.mark.parametrize("env", _LEGACY_SAMPLES)
def test_with_no_pinned_record_the_resolver_is_the_code_registry_path(env: dict[str, str]) -> None:
    identity = reviewed_lender_identity_from_env(env)

    lender, nmls_id = validate_public_lender_identity(env.get("MIP_LENDER_NAME", "Summit Mortgage"), env.get("MIP_LENDER_NMLS_ID"))
    tenant = effective_public_tenant_id(env.get("MIP_TENANT_ID"), lender_name=lender)
    assert (identity.lender_name, identity.nmls_id, identity.tenant_id) == (lender, nmls_id, tenant)
    assert not identity.pinned and identity.canonical_json is None
    assert identity.tenant_aliases == () and identity.mark is None


@pytest.mark.parametrize(
    ("env", "rule"),
    [
        ({"MIP_LENDER_NAME": ""}, "public lender organization name"),
        ({"MIP_LENDER_NAME": "Acme Mortgage", "MIP_LENDER_NMLS_ID": "7654321"}, "independently reviewed source-controlled"),
        ({"MIP_LENDER_NAME": "Acme Mortgage"}, "required when mip_lender_name"),
    ],
)
def test_the_legacy_path_keeps_its_own_refusals(env: dict[str, str], rule: str) -> None:
    with pytest.raises(ValueError, match=rule) as caught:
        reviewed_lender_identity_from_env(env)
    assert not isinstance(caught.value, LenderIdentityRefused)


def test_present_env_drops_blank_values_for_blank_means_unset_callers() -> None:
    values = {"MIP_LENDER_NAME": "", "MIP_LENDER_NMLS_ID": " 123456 ", "OTHER": "x", LENDER_IDENTITY_JSON_ENV: "  "}

    assert present_env(values) == {"MIP_LENDER_NMLS_ID": " 123456 "}


def test_as_configured_keeps_legacy_values_as_given_and_drops_blank_pins() -> None:
    values = {
        "MIP_LENDER_NAME": "   ",
        "MIP_LENDER_NMLS_ID": "",
        "MIP_TENANT_ID": None,
        "OTHER": "x",
        LENDER_IDENTITY_JSON_ENV: "  ",
        LENDER_IDENTITY_SHA256_ENV: "ab",
    }

    assert as_configured(values) == {"MIP_LENDER_NAME": "   ", LENDER_IDENTITY_SHA256_ENV: "ab"}
    with pytest.raises(ValueError, match="mip_lender_name must be"):
        reviewed_lender_identity_from_env(as_configured({"MIP_LENDER_NAME": "   "}))


def test_canonical_json_sorts_keys_without_whitespace() -> None:
    assert canonical_lender_identity_json({"b": [1, 2], "a": {"d": 1, "c": 2}}) == '{"a":{"c":2,"d":1},"b":[1,2]}'


def test_no_doc_claims_an_approval_the_pin_does_not_carry() -> None:
    """Review of #298: the pin is self-certifying, so every doc says what it is.

    It travels in the record's own configuration channel. Until a workflow
    carries it through a GitHub Environment approval (none does: the scan
    below has a positive control), no doc may claim that gate, and each one
    calls the pin an acknowledgement and not an independent approval.
    """

    root = Path(__file__).resolve().parents[2]
    workflows = "\n".join(path.read_text(encoding="utf-8") for path in sorted((root / ".github").rglob("*.yml")))
    assert "MIP_LENDER_NAME" in workflows, "positive control: the workflows carry the legacy lender name"
    deployment = (root / "docs" / "deployment.md").read_text(encoding="utf-8")
    section = deployment[deployment.index("## Customer lender identity") :]
    docs = {
        "README.md": (root / "README.md").read_text(encoding="utf-8"),
        "docs/deployment.md": section[: section.index("\n## ", 1)],
        "lender_identity_record.py": record_module.__doc__ or "",
    }
    for name, text in docs.items():
        flat = " ".join(text.split())
        assert "exact-digest acknowledgement" in flat and "not an independent approval" in flat, name
        if "MIP_LENDER_IDENTITY_SHA256" not in workflows:
            assert "GitHub Environment approval" not in flat.replace("through a GitHub Environment approval yet", ""), name
