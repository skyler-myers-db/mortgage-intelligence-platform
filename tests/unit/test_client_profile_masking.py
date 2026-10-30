"""tools/clients masks: every identifying leaf, and nothing a reader needs.

The class map decides: ids, hosts, emails and foreign object names are
masked; schema vocabulary (``users``, ``account users``, kind, type and
privilege names) and ISO dates stay readable, so refusal lines and exposure
decisions stay auditable in a public log (plan §4.1, attack G17).
"""

from __future__ import annotations

import io
import json

import pytest

from tests.fixtures.client_profiles import EXPOSURE, LENDER_IDENTITY, customer_profile, demo_profile
from tools.clients import profile as views
from tools.clients import schema
from tools.clients.environment import RawProfile, resolve
from tools.clients.errors import ProfileRefusal
from tools.clients.redact import ForeignNames, emit_mask
from tools.clients.schema import FIELD_CLASS, JSON_LEAF_CLASS, FieldClass, LeafClass
from tools.clients.shapes import https_host, is_iso_date, is_vocabulary


def _profile(values: dict[str, str]) -> schema.ClientProfile:
    return resolve(RawProfile(values=values))


def _github_mask(text: str, masks: tuple[str, ...]) -> str:
    """What the runner prints: every registered value replaced by ``***``."""

    for value in masks:
        text = text.replace(value, "***")
    return text


def _identifying_leaves(values: dict[str, str]) -> set[str]:
    """Every identifying leaf of a raw profile, read from the class map."""

    leaves: set[str] = set()
    for name, raw in values.items():
        if FIELD_CLASS[name] is not FieldClass.IDENTIFYING:
            continue
        if name in JSON_LEAF_CLASS:
            for path, leaf in views.iter_leaves(json.loads(raw)):
                if JSON_LEAF_CLASS[name][path] is LeafClass.IDENTIFYING:
                    leaves.add(leaf.removeprefix("inherited:"))
        elif name in {"DATABRICKS_HOST", "DATABRICKS_ACCOUNT_HOST"}:
            leaves.add(https_host(raw))
        else:
            leaves.update(item.strip() for item in raw.split(","))
    return {leaf for leaf in leaves if not is_vocabulary(leaf) and not is_iso_date(leaf)}


@pytest.mark.parametrize("build", [demo_profile, customer_profile])
def test_every_identifying_leaf_is_masked_and_nothing_else(build) -> None:
    values = build()
    masks = views.masks(_profile(values))
    assert set(masks) == _identifying_leaves(values)
    assert len(masks) == len(set(masks))
    # Longest first, so a value that contains another is replaced whole.
    assert [len(mask) for mask in masks] == sorted((len(mask) for mask in masks), reverse=True)


def test_every_json_leaf_path_has_a_class() -> None:
    samples = {
        "MIP_IDENTITY_IDS_JSON": json.loads(demo_profile()["MIP_IDENTITY_IDS_JSON"]),
        "MIP_LENDER_IDENTITY_JSON": LENDER_IDENTITY,
        "MIP_ACCEPTED_EXPOSURE_JSON": EXPOSURE,
    }
    for name, sample in samples.items():
        paths = {path for path, _ in views.iter_leaves(sample)}
        assert paths == set(JSON_LEAF_CLASS[name]), name


def test_vocabulary_dates_public_and_secret_values_are_never_masked() -> None:
    masks = set(views.masks(_profile(customer_profile())))
    readable = {
        "users", "account users", "admins", "CAN_VIEW", "BROWSE", "USE_CATALOG", "GENIE_SPACE",
        "CATALOG", "workspace_permission", "uc_grant", "OPEN", "ISOLATED", "app", "runtime",
        "read", "direct", "2026-12-01", "2026-12-01T00:00:00Z", "2026-10-01",
    }  # fmt: skip
    assert not masks & readable
    public_or_secret = {
        value
        for name, value in customer_profile().items()
        if FIELD_CLASS[name] is not FieldClass.IDENTIFYING
    }
    assert not masks & public_or_secret


def test_an_exposure_grantee_is_masked_unless_it_is_vocabulary() -> None:
    masks = views.masks(_profile(demo_profile()))
    assert "other-team-readers" in masks
    assert "account users" not in masks
    # An inherited source masks its path, never the vocabulary prefix.
    assert "metastore_parent_alpha" in masks
    assert not any(mask.startswith("inherited:") for mask in masks)


def test_an_unclassified_json_leaf_fails_closed_into_the_masks(monkeypatch: pytest.MonkeyPatch) -> None:
    exposure = dict(JSON_LEAF_CLASS["MIP_ACCEPTED_EXPOSURE_JSON"])
    del exposure["[].securable.name"]
    monkeypatch.setitem(views.JSON_LEAF_CLASS, "MIP_ACCEPTED_EXPOSURE_JSON", exposure)
    assert "Another Team Space" in views.masks(_profile(demo_profile()))


def test_a_masked_log_keeps_no_identifier_and_every_word_a_reader_needs() -> None:
    values = customer_profile()
    profile = _profile(values)
    log = "\n".join(
        [
            *(f"{name}={value}" for name, value in views.deploy_controls(profile).items()),
            "exposure: account users holds CAN_VIEW on GENIE_SPACE until 2026-12-01 (direct, read)",
        ]
    )
    masked = _github_mask(log, views.masks(profile))
    for leaf in _identifying_leaves(values):
        assert leaf not in masked, leaf
    assert "exposure: account users holds CAN_VIEW on GENIE_SPACE until 2026-12-01 (direct, read)" in masked
    assert "MIP_APP_NAME=mip-app" in masked


@pytest.mark.parametrize(
    "changes",
    [
        {"MIP_UC_METASTORE_OWNER": "account users"},
        {"MIP_REVIEW_MODE": "independent"},
        {"MIP_APP_ENV": "local"},
        {"MIP_ADMIN_IDENTITIES": "mip-deployer"},
        {"MIP_DEFAULT_CATALOG": "lender_catalog"},
        {"DATABRICKS_HOST": "<workspace-host>"},
    ],
)
def test_a_refusal_line_stays_readable_under_the_masks(changes: dict[str, str]) -> None:
    masks = views.masks(_profile(customer_profile()))
    with pytest.raises(ProfileRefusal) as caught:
        _profile(customer_profile(**changes))
    for line in caught.value.lines:
        assert _github_mask(line, masks) == line


def _generic_profile() -> schema.ClientProfile:
    """Identifying values a real estate holds that name no client.

    A shared ``main`` catalog, an ``inherited:samples`` source, a two-letter
    group, a group named ``admin`` and the ``/Workspace/Shared`` root; the
    three-letter grantee ``qzx`` is the shortest value that stays masked.
    """

    shared = {
        **EXPOSURE[1],
        "securable": {"type": "CATALOG", "id": "main", "name": "main"},
        "grantee": "it",
        "source": "inherited:samples",
    }
    short = {**EXPOSURE[1], "grantee": "qzx"}
    return _profile(
        customer_profile(
            MIP_ACCEPTED_EXPOSURE_JSON=json.dumps([EXPOSURE[0], shared, short]),
            MIP_ADMIN_GROUP_NAME="admin",
            MIP_GENIE_MANAGER_PRINCIPALS="approver,genie-managers-alpha",
            MIP_DASHBOARD_PARENT_PATH="/Workspace/Shared",
        )
    )


def test_generic_values_are_never_registered_and_specific_ones_still_are() -> None:
    masks = set(views.masks(_generic_profile()))
    assert not masks & {"main", "samples", "it", "admin", "approver", "/Workspace/Shared"}
    assert {"qzx", "Another Team Space", "genie-managers-alpha", "space-alpha-0001"} <= masks


@pytest.mark.parametrize(
    "line",
    [
        "MIP_REMAINING: the domain of origin/main is maintained",
        "deploy: wrote the dashboard under /Workspace/Shared/mip; it is readable",
    ],
)
def test_unrelated_log_text_survives_generic_identifying_values(line: str) -> None:
    assert _github_mask(line, views.masks(_generic_profile())) == line


@pytest.mark.parametrize(
    "changes",
    [
        {"MIP_UC_METASTORE_OWNER": "account users"},
        {"MIP_REVIEW_MODE": "independent"},
        {"MIP_ADMIN_IDENTITIES": "mip-deployer"},
        {"MIP_APPROVER_IDENTITIES": "mip-validator"},
        {"MIP_DEFAULT_CATALOG": "lender_catalog"},
    ],
)
def test_a_refusal_line_stays_readable_under_generic_masks(changes: dict[str, str]) -> None:
    masks = views.masks(_generic_profile())
    with pytest.raises(ProfileRefusal) as caught:
        _profile(customer_profile(**changes))
    for line in caught.value.lines:
        assert _github_mask(line, masks) == line


def test_emit_mask_registers_each_line_on_stderr_only_in_actions() -> None:
    stream = io.StringIO()
    assert emit_mask("value-alpha\nvalue-beta", stream=stream, environ={"GITHUB_ACTIONS": "true"}) == (
        "value-alpha\nvalue-beta"
    )
    assert stream.getvalue() == "::add-mask::value-alpha\n::add-mask::value-beta\n"
    quiet = io.StringIO()
    emit_mask("value-alpha", stream=quiet, environ={})
    assert quiet.getvalue() == ""


def test_foreign_names_get_stable_handles_only_when_redaction_is_on() -> None:
    names = ForeignNames(enabled=True)
    assert [names.handle(name) for name in ("main", "samples", "main")] == ["foreign#1", "foreign#2", "foreign#1"]
    assert names.mapping() == {"foreign#1": "main", "foreign#2": "samples"}
    assert ForeignNames(enabled=False).handle("main") == "main"


def test_foreign_redaction_reads_its_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIP_REDACT_FOREIGN_NAMES", "1")
    assert ForeignNames().handle("samples") == "foreign#1"
    monkeypatch.setenv("MIP_REDACT_FOREIGN_NAMES", "0")
    assert ForeignNames().handle("samples") == "samples"
