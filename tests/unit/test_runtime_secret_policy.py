from __future__ import annotations

import pytest
from pydantic import SecretStr

from backend.config.runtime_secret_policy import (
    require_distinct_rotation_secrets,
    require_strong_runtime_secret,
    runtime_secret_text,
)


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        "changeme",
        "MIP-COTALITY-ID-MASK-V1",
        "<replace-with-secret>",
        "<replace-with-secret",
        "replace-with-secret>",
    ],
)
def test_runtime_secret_text_rejects_unset_and_placeholder_values(value: object) -> None:
    assert runtime_secret_text(value) is None


def test_runtime_secret_text_unwraps_secret_str_and_trims_whitespace() -> None:
    secret = "a" * 32
    assert runtime_secret_text(SecretStr(f"  {secret}  ")) == secret


def test_runtime_secret_text_honors_caller_placeholders() -> None:
    assert runtime_secret_text("tenant-placeholder", extra_placeholders={"TENANT-PLACEHOLDER"}) is None


def test_require_strong_runtime_secret_counts_utf8_bytes() -> None:
    secret = "é" * 16
    assert require_strong_runtime_secret(secret, name="TEST_SECRET") == secret


def test_require_strong_runtime_secret_rejects_short_or_placeholder_values() -> None:
    with pytest.raises(ValueError, match="at least 32 UTF-8 bytes"):
        require_strong_runtime_secret("short", name="TEST_SECRET")
    with pytest.raises(ValueError, match="must not be a placeholder"):
        require_strong_runtime_secret("<missing", name="TEST_SECRET")


def test_require_distinct_rotation_secrets_rejects_reused_key() -> None:
    with pytest.raises(ValueError, match="PREVIOUS_SECRET must differ from CURRENT_SECRET"):
        require_distinct_rotation_secrets(
            "same-secret",
            "same-secret",
            current_name="CURRENT_SECRET",
            previous_name="PREVIOUS_SECRET",
        )


def test_require_distinct_rotation_secrets_allows_absent_or_distinct_previous_key() -> None:
    require_distinct_rotation_secrets(
        "current-secret",
        None,
        current_name="CURRENT_SECRET",
        previous_name="PREVIOUS_SECRET",
    )
    require_distinct_rotation_secrets(
        "current-secret",
        "previous-secret",
        current_name="CURRENT_SECRET",
        previous_name="PREVIOUS_SECRET",
    )
