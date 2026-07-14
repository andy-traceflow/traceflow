"""Typed application settings loaded from environment variables.

Per-client integration credentials live in client_configs.crm_credentials JSONB,
NOT here. The settings in this file are platform-level (one TraceFlow installation,
many tenants).
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    # Environment
    environment: str = "development"
    log_level: str = "INFO"
    base_url: str = "http://localhost:8000"

    # Supabase
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_anon_key: str = ""
    supabase_db_url: str = ""  # direct Postgres DSN for asyncpg pool

    # AI providers
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""

    # Email transport
    resend_api_key: str = ""
    notify_from_email: str = "hello@traceflow.app"

    # Security
    admin_jwt_secret: str = ""
    allowed_origins: str = ""
    # Admin /login failure rate limiting. Default on; set
    # ADMIN_LOGIN_RATE_LIMIT_ENABLED=false to disable (e.g. during live
    # testing when repeated logins would otherwise trip the lockout).
    admin_login_rate_limit_enabled: bool = True

    # Demo mode — when true, serves a public no-login, read-only copy of the
    # admin UI at /demo backed entirely by in-memory fixtures (no DB access).
    # Web service only; never set on the cron services. See ADR / routers/demo.py.
    demo_mode: bool = False

    # Observability
    sentry_dsn: str = ""

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
