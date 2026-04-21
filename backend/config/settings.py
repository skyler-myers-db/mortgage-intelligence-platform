from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "local"
    mip_mock_mode: bool = True
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
    # `heloc_equity_min_pct > min_equity_pct` is intentional — HELOC
    # underwriting demands more equity cushion than plain refi.
    # `retention_min_spread < min_spread_bps` is intentional — we reach
    # out earlier on existing relationships.
    mip_heloc_equity_min_pct: int = 35
    mip_cashout_equity_min_pct: int = 25
    mip_retention_min_spread_bps: int = 50

    databricks_host: str | None = None
    databricks_warehouse_id: str | None = None
    genie_space_id: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
