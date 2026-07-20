from __future__ import annotations

import pytest
from pydantic import SecretStr

from backend.config.settings import Settings, settings
from backend.services.campaign_intelligence import (
    issue_campaign_variant_provenance,
    verify_campaign_variant_provenance,
)
from backend.services.forced_degraded import _current_cookie_key
from backend.services.genie_actions import (
    _current_action_token_key,
    _previous_action_token_key,
)


def _test_secret(label: str) -> str:
    return f"{label}-" + ("x" * 32)


CURRENT_SECRET = _test_secret("current")
NEW_SECRET = _test_secret("rotated")


@pytest.mark.parametrize("app_env", ["dev", "sandbox", "customer", "production"])
def test_genie_action_token_requires_current_secret_outside_local_test(
    monkeypatch: pytest.MonkeyPatch,
    app_env: str,
) -> None:
    monkeypatch.setattr(settings, "app_env", app_env)
    monkeypatch.setattr(settings, "mip_genie_action_secret", SecretStr("legacy-secret"))
    monkeypatch.setattr(settings, "mip_genie_action_secret_current", None)

    with pytest.raises(RuntimeError, match="MIP_GENIE_ACTION_SECRET_CURRENT"):
        _current_action_token_key()


def test_genie_action_token_keeps_legacy_secret_usable_locally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "app_env", "local")
    monkeypatch.setattr(settings, "mip_genie_action_secret", SecretStr("legacy-secret"))
    monkeypatch.setattr(settings, "mip_genie_action_secret_current", None)

    key_id, secret = _current_action_token_key()

    assert key_id == settings.mip_genie_action_secret_kid
    assert secret == b"legacy-secret"


@pytest.mark.parametrize("app_env", ["sandbox", "customer", "production"])
def test_genie_action_token_rejects_weak_current_secret(
    monkeypatch: pytest.MonkeyPatch,
    app_env: str,
) -> None:
    monkeypatch.setattr(settings, "app_env", app_env)
    monkeypatch.setattr(settings, "mip_genie_action_secret_current", SecretStr("x"))

    with pytest.raises(RuntimeError, match="at least 32"):
        _current_action_token_key()


def test_genie_action_token_rejects_duplicate_rotation_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "mip_genie_action_secret_current", SecretStr(CURRENT_SECRET))
    monkeypatch.setattr(settings, "mip_genie_action_secret_previous", SecretStr(CURRENT_SECRET))

    with pytest.raises(RuntimeError, match="must differ"):
        _previous_action_token_key()


def test_forced_degraded_cookie_rejects_weak_production_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "mip_genie_action_secret_current", SecretStr("x"))

    with pytest.raises(RuntimeError, match="at least 32"):
        _current_cookie_key()


def test_campaign_provenance_rejects_legacy_current_key_outside_local_test() -> None:
    with pytest.raises(RuntimeError, match="MIP_GENIE_ACTION_SECRET_CURRENT"):
        issue_campaign_variant_provenance(
            generation_mode="reviewed_fallback",
            generator_label="Reviewed campaign framework",
            variant_name="Benefit-led",
            channel="email",
            subject="Review your mortgage options",
            body="Explore options with a licensed loan officer.",
            criteria_fingerprint="criteria-v1",
            settings=Settings(
                app_env="sandbox",
                mip_genie_action_secret="legacy-key-does-not-count",
                mip_genie_action_secret_current=None,
            ),
        )


def test_campaign_provenance_previous_key_is_bounded_by_existing_ttl() -> None:
    old_settings = Settings(
        app_env="production",
        mip_genie_action_secret_current=CURRENT_SECRET,
        mip_genie_action_secret_kid="old-v1",
    )
    criteria_fingerprint = "criteria-v1"
    token = issue_campaign_variant_provenance(
        generation_mode="reviewed_fallback",
        generator_label="Reviewed campaign framework",
        variant_name="Benefit-led",
        channel="email",
        subject="Review your mortgage options",
        body="Explore options with a licensed loan officer.",
        criteria_fingerprint=criteria_fingerprint,
        settings=old_settings,
        now=100,
    )
    rotated_settings = Settings(
        app_env="production",
        mip_genie_action_secret_current=NEW_SECRET,
        mip_genie_action_secret_previous=CURRENT_SECRET,
        mip_genie_action_secret_kid="new-v2",
        mip_genie_action_secret_previous_kid="old-v1",
    )
    variant = {
        "variant_name": "Benefit-led",
        "channel": "email",
        "subject": "Review your mortgage options",
        "body": "Explore options with a licensed loan officer.",
        "provenance_token": token,
    }

    assert verify_campaign_variant_provenance(
        variant,
        criteria_fingerprint=criteria_fingerprint,
        settings=rotated_settings,
        now=3_700,
    ) == ("reviewed_fallback", "Reviewed campaign framework")
    assert (
        verify_campaign_variant_provenance(
            variant,
            criteria_fingerprint=criteria_fingerprint,
            settings=rotated_settings,
            now=3_701,
        )
        is None
    )


def test_campaign_provenance_rejects_weak_production_key() -> None:
    with pytest.raises(RuntimeError, match="at least 32"):
        issue_campaign_variant_provenance(
            generation_mode="reviewed_fallback",
            generator_label="Reviewed campaign framework",
            variant_name="Benefit-led",
            channel="email",
            subject="Review your mortgage options",
            body="Explore options with a licensed loan officer.",
            criteria_fingerprint="criteria-v1",
            settings=Settings(
                app_env="production",
                mip_genie_action_secret_current="x",
            ),
        )


def test_campaign_provenance_rejects_duplicate_rotation_key() -> None:
    token = issue_campaign_variant_provenance(
        generation_mode="reviewed_fallback",
        generator_label="Reviewed campaign framework",
        variant_name="Benefit-led",
        channel="email",
        subject="Review your mortgage options",
        body="Explore options with a licensed loan officer.",
        criteria_fingerprint="criteria-v1",
        settings=Settings(
            app_env="production",
            mip_genie_action_secret_current=CURRENT_SECRET,
        ),
    )
    with pytest.raises(RuntimeError, match="must differ"):
        verify_campaign_variant_provenance(
            {
                "variant_name": "Benefit-led",
                "channel": "email",
                "subject": "Review your mortgage options",
                "body": "Explore options with a licensed loan officer.",
                "provenance_token": token,
            },
            criteria_fingerprint="criteria-v1",
            settings=Settings(
                app_env="production",
                mip_genie_action_secret_current=CURRENT_SECRET,
                mip_genie_action_secret_previous=CURRENT_SECRET,
            ),
        )
