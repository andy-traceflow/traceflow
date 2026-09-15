"""FastAPI app entrypoint. Wires two routers and the lifecycle.

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
from app.routers import health
from app.webhooks import shopify

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: validate fail-closed settings, init Sentry + DB pool. Shutdown: close pool."""
    settings = get_settings()

    # Fail closed: a deploy without its webhook signing secret must not come up.
    require_startup_settings(settings)

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

_settings = get_settings()

# Reject requests with an unexpected Host header before they reach any handler.
# Opt-in: only active when ALLOWED_HOSTS is set.
if _settings.allowed_hosts_list:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_settings.allowed_hosts_list)

app.include_router(shopify.router)
app.include_router(health.router)
