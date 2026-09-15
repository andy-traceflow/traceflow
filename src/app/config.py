"""Typed application settings loaded from environment variables.

Single-tenant: every setting here describes the one deployment this
service is. Destination credentials (Monday, Notion, Slack, ...) are
read by the adapter that needs them at construction time — see
`src/app/adapters/`. The webhook signing secret is validated at startup
(Phase 2), not per request.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    # Environment
    environment: str = "development"
    log_level: str = "INFO"
    base_url: str = "http://localhost:8000"

    # Database — direct Postgres DSN for the asyncpg pool
    supabase_db_url: str = ""

    # Host allow-list for TrustedHostMiddleware. Comma-separated; supports
    # wildcards (e.g. "*.onrender.com"). Empty (default) disables the check.
    # When set, localhost + testserver are always appended for dev/tests.
    allowed_hosts: str = ""

    # Observability
    sentry_dsn: str = ""

    @property
    def allowed_hosts_list(self) -> list[str]:
        """Configured hosts + always-on dev/test hosts. Empty when unset →
        caller skips TrustedHostMiddleware entirely."""
        hosts = [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]
        if not hosts:
            return []
        for local in ("localhost", "127.0.0.1", "testserver"):
            if local not in hosts:
                hosts.append(local)
        return hosts

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
