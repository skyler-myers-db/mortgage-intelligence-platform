"""The previous Genie action key: the pre-stop plan refuses whatever the App payload refuses.

``scripts/deploy.sh`` runs ``provision_runtime_secrets --check`` read-only at
the end of step 0, while the App still serves, and builds the App payload
(``app_deploy_payload``) in step 5, after step 0a has stopped or quarantined
the App and the bundle apply has run. Both read the same shell / ``.env.local``
inputs. A previous-key configuration the payload refuses must already refuse
in the plan, or the deploy fails with the App down. The case that slipped: a
``MIP_GENIE_ACTION_SECRET_PREVIOUS`` value without its ``_KID`` while
``genie-action-previous`` already exists, which the presence-only plan keeps
and so never validated.

The secret inputs reach both modules through the shell or ``.env.local``; the
dotenv cases point both modules' ``ENV_LOCAL`` at a temporary file this test
writes, never at a real one. The fake Secrets API answers no value read.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.databricks import app_deploy_payload as payload_subject
from tools.databricks import provision_runtime_secrets as provision_subject

PREVIOUS_ENV = provision_subject.PREVIOUS_ENV
KID_ENV = provision_subject.PREVIOUS_KID_ENV
PREVIOUS_VALUES = {
    "unset": None,
    "placeholder": "REDACTED",
    "real": "synthetic-previous-parity-" + "p" * 32,
}
KID_VALUES = {"unset": None, "set": "v1"}
ALL_KEYS = tuple(provision_subject.ENV_TO_KEY.values())
STORED = {
    "previous-stored": ALL_KEYS,
    "previous-absent": tuple(key for key in ALL_KEYS if key != provision_subject.PREVIOUS_KEY),
}
CHANNELS = ("shell", "dotenv")


class _Secrets:
    """Read-only Secrets API: the scope exists and lists ``keys``; a value read fails the test."""

    def __init__(self, keys: tuple[str, ...]) -> None:
        self._keys = keys

    def list_scopes(self):
        return [SimpleNamespace(name=provision_subject.DEFAULT_SCOPE)]

    def list_secrets(self, *, scope: str):
        return [SimpleNamespace(key=key) for key in self._keys]

    def get_secret(self, *, scope: str, key: str):  # pragma: no cover - a failure path
        raise AssertionError(f"the plan read {key}; the scope is presence-only")


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(provision_subject, "ENV_LOCAL", tmp_path / ".env.local.absent")
    monkeypatch.setattr(payload_subject, "ENV_LOCAL", tmp_path / ".env.local.absent")
    for name in (
        *provision_subject.ENV_TO_KEY,
        KID_ENV,
        *payload_subject.NON_SECRET_OPERATOR_VARS,
        *payload_subject.SECRET_RESOURCE_BINDINGS,
        "MIP_PRESENTER_MODE",
    ):
        monkeypatch.delenv(name, raising=False)


def _apply(monkeypatch, tmp_path: Path, values: dict[str, str | None], channel: str) -> None:
    for name in values:
        monkeypatch.delenv(name, raising=False)
    configured = {name: value for name, value in values.items() if value is not None}
    if channel == "shell":
        for name, value in configured.items():
            monkeypatch.setenv(name, value)
        return
    env_local = tmp_path / "parity.env.local"
    env_local.write_text("".join(f"{name}='{value}'\n" for name, value in configured.items()), encoding="utf-8")
    monkeypatch.setattr(provision_subject, "ENV_LOCAL", env_local)
    monkeypatch.setattr(payload_subject, "ENV_LOCAL", env_local)


def _plan_refusal(stored: tuple[str, ...]) -> str | None:
    try:
        provision_subject.check_runtime_secrets(client=SimpleNamespace(secrets=_Secrets(stored)))
    except ValueError as exc:
        return str(exc)
    return None


def _payload_refusal() -> str | None:
    try:
        payload_subject.build_payload(source_code_path="/Workspace/app/files", target="prod")
    except ValueError as exc:
        return str(exc)
    return None


@pytest.mark.parametrize("channel", CHANNELS)
@pytest.mark.parametrize("stored", tuple(STORED), ids=tuple(STORED))
def test_the_plan_refuses_every_previous_key_configuration_the_payload_refuses(
    monkeypatch, tmp_path, stored: str, channel: str
) -> None:
    for previous, kid in itertools.product(PREVIOUS_VALUES, KID_VALUES):
        _apply(monkeypatch, tmp_path, {PREVIOUS_ENV: PREVIOUS_VALUES[previous], KID_ENV: KID_VALUES[kid]}, channel)

        plan_refusal, payload_refusal = _plan_refusal(STORED[stored]), _payload_refusal()

        case = f"previous={previous} kid={kid}"
        assert (plan_refusal is None) == (payload_refusal is None), case
        assert (payload_refusal is not None) == (previous == "real" and kid == "unset"), case
        if plan_refusal is not None:
            assert KID_ENV in plan_refusal and KID_ENV in str(payload_refusal), case
