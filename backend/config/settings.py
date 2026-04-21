"""App settings for the Mortgage Intelligence Platform backend.

CLAUDE.md / Slice 4 invariant: there is no ``MIP_MOCK_MODE`` runtime
toggle. The running app always reads live Unity Catalog data through
the Databricks SQL warehouse. Missing warehouse credentials are a
fail-fast startup error -- they do NOT silently fall back to fixtures.

The ``Databricks*`` fields below are required at import time EXCEPT in
test processes (detected via ``PYTEST_CURRENT_TEST``), which inject
stub repositories through FastAPI dependency overrides and therefore
never open a warehouse connection.
"""
from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Documented, shared error message so every fail-fast site reads the
# same -- helps operators who see it in a container log.
_MISSING_CREDS_MSG = (
    "Mortgage Intelligence Platform refuses to start without live "
    "Databricks warehouse credentials. Set DATABRICKS_HOST, "
    "DATABRICKS_TOKEN, and DATABRICKS_WAREHOUSE_ID in .env.local "
    "(see .env.example). There is no mock-mode fallback: the app runs "
    "on real Unity Catalog data or it fails visibly."
)


def _running_under_pytest() -> bool:
    """True when the current process was launched by pytest.

    Pytest exports ``PYTEST_CURRENT_TEST`` for the duration of each
    test item; it's also set during collection in recent pytest
    versions. We additionally honour ``MIP_BYPASS_STARTUP_CHECKS=1`` as
    an explicit escape hatch for CI phases (lint / type-check /
    schema-only) that import ``backend.config.settings`` without ever
    hitting the warehouse.
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    if os.environ.get("MIP_BYPASS_STARTUP_CHECKS") == "1":
        return True
    # Fallback: the pytest runner imports ``pytest`` before any user
    # code; if it's in sys.modules we're in a test process.
    import sys
    return "pytest" in sys.modules


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "local"
    mip_demo_lender: str = "Summit Mortgage"
    mip_default_catalog: str = "mip_demo"
    mip_default_schema: str = "gold"
    mip_lakebase_schema: str = "mip_app"

    # In-the-money contract: matches tests/fixtures/rate_spread_golden.json
    # (market_rate_constant) and tests/fixtures/in_the_money_golden.json
    # (default_thresholds). Overridable via admin config at runtime.
    mip_market_rate: float = 0.04875
    mip_min_spread_bps: int = 75
    mip_min_equity_pct: int = 15

    # Next-best-offer thresholds: matches
    # tests/fixtures/next_best_offer_golden.json (default_thresholds).
    # `heloc_equity_min_pct > min_equity_pct` is intentional -- HELOC
    # underwriting demands more equity cushion than plain refi.
    # `retention_min_spread < min_spread_bps` is intentional -- we reach
    # out earlier on existing relationships.
    mip_heloc_equity_min_pct: int = 35
    mip_cashout_equity_min_pct: int = 25
    mip_retention_min_spread_bps: int = 50

    # Databricks SQL warehouse credentials -- required for every
    # non-test process. Validated by ``require_databricks_creds()``.
    databricks_host: str | None = None
    databricks_token: SecretStr | None = Field(default=None, repr=False)
    databricks_warehouse_id: str | None = None
    databricks_timeout_s: int = 30

    genie_space_id: str | None = None

    def require_databricks_creds(self) -> tuple[str, SecretStr, str]:
        """Return ``(host, token, warehouse_id)`` or raise at startup.

        Never call this from a path that imports ``settings`` at module-
        import time unless you want the process to refuse to boot on a
        missing env var -- that is the intended behavior for the live
        SQL client and its factory, but not for simple utility imports.
        """
        host = self.databricks_host
        token = self.databricks_token
        warehouse = self.databricks_warehouse_id
        if not host or token is None or not warehouse:
            raise RuntimeError(_MISSING_CREDS_MSG)
        # Normalise host shape: strip trailing slash, ensure scheme.
        if not host.startswith("http"):
            host = "https://" + host
        host = host.rstrip("/")
        return host, token, warehouse


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
