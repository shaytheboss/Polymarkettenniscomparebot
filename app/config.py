from pydantic_settings import BaseSettings
from typing import List, Optional


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql://tennisbot:tennisbot@localhost:5432/tennisbot"

    # Telegram
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""

    # API keys
    api_tennis_key: str = ""           # api-tennis.com (RapidAPI)
    sportradar_api_key: str = ""       # backup live scores
    polymarket_api_key: str = ""
    polymarket_proxy_url: str = ""     # HTTP/SOCKS proxy for Polymarket (blocked from Railway IPs)
                                       # e.g. http://user:pass@proxy:8080 or socks5://user:pass@proxy:1080
    polymarket_relay_url: str = ""     # Cloudflare Worker relay URL — preferred free solution
                                       # Deploy cloudflare-worker.js, set this to https://xxx.workers.dev

    # App
    app_env: str = "development"
    secret_key: str = "changeme"
    cors_origins: str = "http://localhost:5173"

    # Scheduler intervals (seconds)
    live_scores_interval: int = 15     # how often to poll live scores
    elo_refresh_interval: int = 86400  # once per day
    polymarket_interval: int = 20
    analyzer_interval: int = 30

    # Alert thresholds (defaults, can be overridden per user)
    default_min_edge_pp: float = 5.0
    default_max_model_gap_pp: float = 15.0
    alert_dedup_minutes: int = 15

    # ELO source
    elo_source: str = "tennis_abstract"  # "tennis_abstract" or "sackmann_github"

    # Sentry
    sentry_dsn: str = ""

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",")]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
