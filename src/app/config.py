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

    # Database — direct Postgres DSN for the asyncpg pool. REQUIRED: without
    # it the webhook would 200 and persist nothing, which is the exact
    # failure this template exists to prevent.
    supabase_db_url: str = ""

    # Which adapter receives every event: monday | hubspot | notion | slack |
    # sheets. The adapter reads its own credentials (see .env.example) and the
    # app refuses to start if any are missing.
    destination: str = ""

    # Path to the mapping file. Default: mapping.yaml in the working
    # directory, falling back to the repo root. Only set this to point a
    # test or a one-off run at a different file.
    mapping_path: str = ""

    # Shopify webhook HMAC secret. Shopify admin → Settings → Notifications →
    # Webhooks → the "signed with" value at the bottom of the page. REQUIRED:
    # the app refuses to start without it (see require_startup_settings).
    shopify_webhook_secret: str = ""

    # Host allow-list for TrustedHostMiddleware. Comma-separated; supports
    # wildcards (e.g. "*.onrender.com"). Empty (default) disables the check.
    # When set, localhost + testserver are always appended for dev/tests.
    allowed_hosts: str = ""

    # Where the delivery worker runs.
    #   inprocess (default): a background task inside the web service. One
    #     Render service, nothing lost on restart — the queue is the events
    #     table, so the worker simply resumes when the process does.
    #   separate: run `python -m app.worker` as its own Render worker or
    #     cron; the web service only receives.
    worker_mode: str = "inprocess"

    # Bearer token for GET /events and POST /events/{id}/replay. REQUIRED.
    # Generate one: python -c "import secrets; print(secrets.token_urlsafe(32))"
    admin_token: str = ""

    # Slack incoming-webhook URL that receives one message per dead-lettered
    # event. Optional but strongly recommended — without it, dead letters
    # are only visible in the logs and on /health.
    alert_webhook_url: str = ""

    # Observability
    sentry_dsn: str = ""
    log_format: str = "json"  # json | text

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


# Settings whose absence must stop the process, not degrade it. Checked once
# in the lifespan handler so a misconfigured deploy fails at boot — never on
# the first webhook, and never by silently skipping a check.
REQUIRED_AT_STARTUP: tuple[str, ...] = (
    "shopify_webhook_secret",
    "supabase_db_url",
    "destination",
    "admin_token",
)


WORKER_MODES: frozenset[str] = frozenset({"inprocess", "separate"})


def require_startup_settings(settings: Settings | None = None) -> None:
    """Raise RuntimeError naming every missing or invalid fail-closed setting."""
    s = settings if settings is not None else get_settings()
    missing = [name.upper() for name in REQUIRED_AT_STARTUP if not getattr(s, name)]
    if missing:
        raise RuntimeError(
            "refusing to start: missing required environment variables: " + ", ".join(missing)
        )
    if s.worker_mode not in WORKER_MODES:
        raise RuntimeError(
            f"refusing to start: WORKER_MODE must be one of {sorted(WORKER_MODES)}, "
            f"got {s.worker_mode!r}"
        )
