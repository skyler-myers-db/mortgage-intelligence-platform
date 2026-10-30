"""The client profile pins a customer's lender record by MIP_LENDER_IDENTITY_SHA256.

The profile is where a reviewer sets the pin, so it refuses what the App and
the deploy would refuse later: a customer record without its pin, a pin that
is not the record's canonical sha256 (a tampered record), a pin on a demo,
and any record the backend's own rules refuse. Its views hand the deploy the
canonical record and the pin, and those verify in the backend resolver.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.schemas.lender_identity_record import (
    LENDER_IDENTITY_JSON_ENV,
    LENDER_IDENTITY_SHA256_ENV,
    lender_identity_digest,
    reviewed_lender_identity_from_env,
)
from tests.fixtures.client_profiles import (
    LENDER_IDENTITY,
    LENDER_IDENTITY_SHA256,
    LENDER_MARK,
    customer_profile,
    demo_profile,
)
from tools.clients import profile as views
from tools.clients.environment import RawProfile, resolve
from tools.clients.errors import ProfileRefusal


def _refusals(values: dict[str, str]) -> list[str]:
    with pytest.raises(ProfileRefusal) as caught:
        resolve(RawProfile(values=values))
    return list(caught.value.lines)


def _record(*, pin: str | None = None, **changes: Any) -> dict[str, str]:
    """A changed record and its pin (or a stand-in pin for a record no digest exists for)."""

    text = json.dumps({**LENDER_IDENTITY, **changes})
    return {LENDER_IDENTITY_JSON_ENV: text, LENDER_IDENTITY_SHA256_ENV: pin or lender_identity_digest(text)}


def test_a_pinned_customer_profile_resolves_and_its_views_verify_in_the_backend() -> None:
    profile = resolve(RawProfile(values=customer_profile()))
    env = views.app_payload_env(profile)

    assert env[LENDER_IDENTITY_SHA256_ENV] == LENDER_IDENTITY_SHA256
    identity = reviewed_lender_identity_from_env(env)
    assert identity.digest == LENDER_IDENTITY_SHA256
    assert identity.lender_name == env["MIP_LENDER_NAME"] == LENDER_IDENTITY["legal_name"]
    variables = views.bundle_vars(profile)
    assert variables["lender_identity_sha256"] == LENDER_IDENTITY_SHA256
    assert lender_identity_digest(variables["lender_identity_json"]) == LENDER_IDENTITY_SHA256


def test_the_pin_is_public_and_the_record_is_masked() -> None:
    profile = resolve(RawProfile(values=customer_profile()))

    masks = set(views.masks(profile))
    assert LENDER_IDENTITY_SHA256 not in masks
    assert LENDER_IDENTITY["legal_name"] in masks


def test_a_customer_record_without_its_pin_is_refused() -> None:
    lines = _refusals(customer_profile(MIP_LENDER_IDENTITY_SHA256=None))
    assert "MIP_LENDER_IDENTITY_SHA256: is required to pin MIP_LENDER_IDENTITY_JSON" in lines


def test_a_tampered_record_or_a_stale_pin_is_refused() -> None:
    tampered = json.dumps({**LENDER_IDENTITY, "nmls_id": "424243"})
    for values in (
        customer_profile(MIP_LENDER_IDENTITY_JSON=tampered),
        customer_profile(MIP_LENDER_IDENTITY_SHA256="0" * 64),
    ):
        assert _refusals(values) == [
            "MIP_LENDER_IDENTITY_SHA256: must equal the sha256 of the canonical MIP_LENDER_IDENTITY_JSON record"
        ]


def test_the_pin_is_taken_over_the_canonical_record_not_its_layout() -> None:
    pretty = json.dumps(dict(reversed(list(LENDER_IDENTITY.items()))), indent=2)
    profile = resolve(RawProfile(values=customer_profile(MIP_LENDER_IDENTITY_JSON=pretty)))
    assert profile.lender_identity_sha256 == LENDER_IDENTITY_SHA256


def test_a_demo_profile_refuses_a_pin() -> None:
    lines = _refusals(demo_profile(MIP_LENDER_IDENTITY_SHA256=LENDER_IDENTITY_SHA256))
    assert lines == ["MIP_LENDER_IDENTITY_SHA256: is customer-only; it pins MIP_LENDER_IDENTITY_JSON"]


@pytest.mark.parametrize("pin", ["AB" * 32, "abc", "<lender-identity-sha256>x"])
def test_a_malformed_pin_is_refused(pin: str) -> None:
    lines = _refusals(customer_profile(MIP_LENDER_IDENTITY_SHA256=pin))
    assert any(line.startswith("MIP_LENDER_IDENTITY_SHA256") for line in lines), lines


@pytest.mark.parametrize(
    ("changes", "rule"),
    [
        ({"servicer_aliases": []}, "must list 1-32 servicer names"),
        ({"nmls_id": "1٢٣٤٥٦"}, "4-12 digit nonzero NMLS"),
        ({"servicer_aliases": ["Fictional Harbor", "FICTIONAL HARBOR"]}, "must not repeat a servicer name"),
        ({"mark": {**LENDER_MARK, "height": 20}}, "not a reviewable mark size"),
        ({"mark": {**LENDER_MARK, "authorization_ref": "https://brand.example/ok"}}, "authorization_ref"),
        ({"mark": {**LENDER_MARK, "width": "64"}}, "valid integer"),
    ],
)
def test_the_backend_record_rules_hold_in_the_profile(changes: dict[str, Any], rule: str) -> None:
    lines = _refusals(customer_profile(**_record(pin="0" * 64, **changes)))
    assert any(line.startswith("MIP_LENDER_IDENTITY_JSON") and rule in line for line in lines), lines


def test_a_record_with_a_mark_resolves_and_verifies() -> None:
    values = customer_profile(**_record(mark=LENDER_MARK))
    profile = resolve(RawProfile(values=values))

    identity = reviewed_lender_identity_from_env(views.app_payload_env(profile))
    assert identity.mark is not None and identity.mark.sha256 == LENDER_MARK["sha256"]
