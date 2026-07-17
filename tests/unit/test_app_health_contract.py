from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.databricks.app_health_contract import authenticated_app_health


def _workspace(url: str = "https://mip-app.example") -> object:
    return SimpleNamespace(apps=SimpleNamespace(get=lambda _name: SimpleNamespace(url=url)))


def test_authenticated_health_binds_workspace_url_and_forbids_redirects() -> None:
    calls: list[str] = []
    client = SimpleNamespace(
        get=lambda url, **_kwargs: calls.append(url)
        or SimpleNamespace(status_code=302, json=lambda: {}, text="redirect")
    )

    with pytest.raises(RuntimeError, match="redirects are forbidden"):
        authenticated_app_health(
            _workspace(),
            app_name="mip-app",
            base_url="https://mip-app.example",
            bearer_token="secret-token",
            client=client,
        )

    assert calls == ["https://mip-app.example/api/health"]


def test_authenticated_health_rejects_mismatched_url_before_sending_token() -> None:
    calls: list[str] = []
    client = SimpleNamespace(get=lambda url, **_kwargs: calls.append(url))

    with pytest.raises(RuntimeError, match="does not match"):
        authenticated_app_health(
            _workspace(),
            app_name="mip-app",
            base_url="https://attacker.example",
            bearer_token="secret-token",
            client=client,
        )

    assert calls == []


@pytest.mark.parametrize(
    "url",
    (
        "http://mip-app.example",
        "https://user@mip-app.example",
        "https://mip-app.example/path",
        "https://mip-app.example?next=evil",
    ),
)
def test_authenticated_health_rejects_noncanonical_workspace_url(url: str) -> None:
    with pytest.raises(RuntimeError, match="URL is invalid"):
        authenticated_app_health(
            _workspace(url),
            app_name="mip-app",
            base_url=url,
            bearer_token="secret-token",
            client=SimpleNamespace(get=lambda *_args, **_kwargs: None),
        )
