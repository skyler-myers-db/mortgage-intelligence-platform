"""The Salesforce keep rule and the App payload's bind rule read "unset" the same way.

provision_runtime_secrets keeps an existing ``salesforce-*`` key when its input
is unset, and app_deploy_payload binds a ``salesforce-*`` key into the runtime
only when its input is set. Both read the same shell / ``.env.local`` inputs. If
the two disagree about one input, a deploy keeps the stored credential of an
earlier deploy AND binds it, so the connector runs on a key nobody supplied.
That happened for a placeholder secret input (``changeme``, ``<client-secret>``):
the provisioner read it as unset, the payload as non-empty (post-W5 L3 verifier
item L3-G1, 2026-10-27).

Every case drives both modules from one environment, with the four non-secret
connector inputs set (the only case where anything binds) and all three
``salesforce-*`` keys either already stored or absent.
"""

from __future__ import annotations

import itertools
from types import SimpleNamespace

import pytest

from backend.config.runtime_secret_policy import DEFAULT_RUNTIME_SECRET_PLACEHOLDERS
from tools.databricks import app_deploy_payload as payload_subject
from tools.databricks import provision_runtime_secrets as provision_subject

NON_SECRET_INPUTS = {
    "SALESFORCE_INSTANCE_URL": "https://summit.example.my.salesforce.com",
    "SALESFORCE_CLIENT_ID": "connected-app-id",
    "SALESFORCE_USERNAME": "mip-integration@example.com",
    "SALESFORCE_EXTERNAL_ID_FIELD": "MIP_Activation_Id__c",
}
SECRET_INPUTS = ("SALESFORCE_CLIENT_SECRET", "SALESFORCE_PASSWORD", "SALESFORCE_SECURITY_TOKEN")
REAL_VALUES = {name: f"synthetic-{name.lower()}-Q7v2Lk9x" for name in SECRET_INPUTS}
SALESFORCE_KEYS = tuple(provision_subject.ENV_TO_KEY[name] for name in SECRET_INPUTS)
PLACEHOLDER_SPELLINGS = (
    *sorted(provision_subject.PLACEHOLDERS | DEFAULT_RUNTIME_SECRET_PLACEHOLDERS),
    "<client-secret>",
    "<password>",
    "token>",
    "  ChangeMe  ",
)
STATES = ("empty", "placeholder", "real")


class _Secrets:
    """Read-only Secrets API: the scope exists and lists ``keys``; nothing is read."""

    def __init__(self, keys: tuple[str, ...]) -> None:
        self._keys = keys

    def list_scopes(self):
        return [SimpleNamespace(name=provision_subject.DEFAULT_SCOPE)]

    def list_secrets(self, *, scope: str):
        return [SimpleNamespace(key=key) for key in self._keys]

    def get_secret(self, *, scope: str, key: str):  # pragma: no cover - a failure path
        raise AssertionError(f"the plan read {key}; no stored value is compared here")


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(provision_subject, "ENV_LOCAL", tmp_path / ".env.local.absent")
    monkeypatch.setattr(payload_subject, "ENV_LOCAL", tmp_path / ".env.local.absent")
    for name in (
        *provision_subject.ENV_TO_KEY,
        provision_subject.PREVIOUS_KID_ENV,
        *payload_subject.NON_SECRET_OPERATOR_VARS,
        *payload_subject.SECRET_RESOURCE_BINDINGS,
        "MIP_PRESENTER_MODE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", "synthetic-mask-" + "m" * 32)
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_CURRENT", "synthetic-current-" + "c" * 32)
    for name, value in NON_SECRET_INPUTS.items():
        monkeypatch.setenv(name, value)


def _apply(monkeypatch, states: dict[str, str], placeholder: str) -> None:
    for name, state in states.items():
        if state == "empty":
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, placeholder if state == "placeholder" else REAL_VALUES[name])


def _decisions(stored: tuple[str, ...]) -> tuple[set[str], dict[str, str], set[str]]:
    """(kept keys, key -> written value, bound keys) for the current environment."""

    plan = provision_subject.check_runtime_secrets(
        client=SimpleNamespace(secrets=_Secrets(stored))
    )
    env = payload_subject.build_payload(source_code_path="/Workspace/app/files", target="prod")
    bound = {
        provision_subject.ENV_TO_KEY[item["name"]]
        for item in env["env_vars"]  # type: ignore[union-attr]
        if item["name"] in SECRET_INPUTS and item.get("value_from")
    }
    return set(plan.kept), dict(plan.writes), bound


@pytest.mark.parametrize("placeholder", PLACEHOLDER_SPELLINGS)
@pytest.mark.parametrize("stored", (SALESFORCE_KEYS, ()), ids=("keys-stored", "keys-absent"))
def test_a_kept_salesforce_key_is_never_bound(
    monkeypatch, placeholder: str, stored: tuple[str, ...]
) -> None:
    for combo in itertools.product(STATES, repeat=len(SECRET_INPUTS)):
        states = dict(zip(SECRET_INPUTS, combo, strict=True))
        _apply(monkeypatch, states, placeholder)

        kept, written, bound = _decisions(stored)

        assert not kept & bound, f"kept AND bound for {states}"
        for name, key in zip(SECRET_INPUTS, SALESFORCE_KEYS, strict=True):
            if key in bound:
                # A bound key always holds the value this deploy supplied.
                assert written.get(key) == REAL_VALUES[name], (states, key)
            if states[name] != "real":
                assert key not in bound, (states, key)
                assert (key in kept) is (key in stored), (states, key)


def test_real_secret_inputs_still_bind_the_keys_they_write(monkeypatch) -> None:
    _apply(monkeypatch, dict.fromkeys(SECRET_INPUTS, "real"), "")

    kept, written, bound = _decisions(SALESFORCE_KEYS)

    assert bound == set(SALESFORCE_KEYS) and not kept & set(SALESFORCE_KEYS)
    assert {key: written[key] for key in SALESFORCE_KEYS} == {
        key: REAL_VALUES[name] for name, key in zip(SECRET_INPUTS, SALESFORCE_KEYS, strict=True)
    }
