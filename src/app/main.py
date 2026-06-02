"""FastAPI app entrypoint. Wires middleware, routers, lifecycle.

Run locally:
    uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from jwt.algorithms import get_default_algorithms

from app.config import get_settings
from app.db import close_pool, init_pool
from app.middleware.tenant_resolver import tenant_resolver_middleware
from app.routers import admin, calculator, kb, kb_export
from app.webhooks import crm, generic, shopify, twilio

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: init DB pool + Sentry. Shutdown: close DB pool."""
    settings = get_settings()

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
    title="TraceFlow API",
    version="0.1.0",
    lifespan=lifespan,
)

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.middleware("http")(tenant_resolver_middleware)

# Webhook routes — tenant identified via path or payload
app.include_router(shopify.router)
app.include_router(twilio.router)
app.include_router(crm.router)
app.include_router(generic.router)

# Tenant-scoped API routes — admin/portal-facing
app.include_router(kb.router)
app.include_router(kb_export.router)
app.include_router(calculator.router)

# Founder-only admin — cross-tenant, bypasses RLS, separate auth scope
app.include_router(admin.router)


@app.get("/")
def root() -> dict:
    return {"status": "ok", "service": "traceflow-api"}


@app.get("/health")
def health() -> dict:
    """Liveness + minimal deploy diagnostics.

    Reports the git commit Render injected at build time and the JWT
    algorithms PyJWT can actually verify — `cryptography` not being
    installed silently disables ES256/RS256, so surfacing this here
    catches that class of deploy regression.
    """
    return {
        "status": "ok",
        "environment": _settings.environment,
        "git_commit": os.getenv("RENDER_GIT_COMMIT", "unknown")[:7],
        "jwt_algorithms": sorted(get_default_algorithms().keys()),
    }
