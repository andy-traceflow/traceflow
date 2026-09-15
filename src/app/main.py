"""FastAPI app entrypoint. Wires three routers and the lifecycle.

Run locally:
    uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import get_settings, require_startup_settings
from app.db import close_pool, init_pool
from app.log import configure_logging
from app.pipeline import build_pipeline
from app.routers import events, health
from app.webhooks import shopify

_settings = get_settings()
configure_logging(_settings.log_level, _settings.log_format)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: validate fail-closed settings, mapping, adapter; init Sentry + DB pool."""
    settings = get_settings()

    # Fail closed: a deploy without its webhook signing secret must not come up.
    require_startup_settings(settings)
    # Load mapping.yaml and construct the destination adapter now, so a bad
    # mapping or missing credentials refuse the boot instead of
    # dead-lettering the first event. The web service never runs the
    # pipeline itself — this is purely the startup check.
    build_pipeline()
    logger.info("mapping + destination validated", extra={"destination": settings.destination})

    if not settings.alert_webhook_url:
        logger.warning("ALERT_WEBHOOK_URL not set — dead-lettered events will only appear in logs and /health")

    if settings.sentry_dsn:
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.environment,
            traces_sample_rate=0.1,
        )
        logger.info("Sentry initialized")

    await init_pool()
    try:
        yield
    finally:
        await close_pool()


app = FastAPI(
    title="SIA Kit",
    version="0.1.0",
    lifespan=lifespan,
)

# Reject requests with an unexpected Host header before they reach any handler.
# Opt-in: only active when ALLOWED_HOSTS is set.
if _settings.allowed_hosts_list:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_settings.allowed_hosts_list)

app.include_router(shopify.router)
app.include_router(health.router)
app.include_router(events.router)
