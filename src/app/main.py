"""FastAPI app entrypoint. Wires middleware, routers, lifecycle.

Run locally:
    uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from jwt.algorithms import get_default_algorithms
from starlette.responses import Response
from starlette.types import Scope

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

# Admin SPA (admin-ui/, built into src/app/static/admin). Static files are
# public by design — the SPA renders its login screen until /api/admin/login
# succeeds; all data sits behind the admin JWT. Mounted conditionally so dev
# checkouts and CI without a built bundle still import cleanly.
#
# Cache strategy for atomic deploys: Vite emits hash-named assets, so they are
# immutable and safe to cache for a year. index.html must NOT be cached — a
# returning visitor would otherwise keep loading a stale shell that points at
# the previous build's (now-deleted) asset hashes. Default StaticFiles sends no
# Cache-Control, which browsers treat as heuristically cacheable, so set it.
class _AdminStaticFiles(StaticFiles):
    """StaticFiles with deploy-safe cache headers for the admin SPA."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        # Normalize separators — StaticFiles hands us OS-native paths (backslashes
        # on Windows dev), so match on a forward-slash form to stay cross-platform.
        is_asset = path.replace("\\", "/").startswith("assets/")
        if is_asset and response.status_code in (200, 304):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            # index.html (and the SPA fallback) — always revalidate so a new
            # deploy's asset hashes are picked up on the next navigation.
            response.headers["Cache-Control"] = "no-cache"
        return response


_ADMIN_UI_DIR = Path(__file__).parent / "static" / "admin"
if _ADMIN_UI_DIR.is_dir():
    app.mount("/admin", _AdminStaticFiles(directory=_ADMIN_UI_DIR, html=True), name="admin-ui")
else:  # pragma: no cover - depends on whether the bundle was built
    logger.info("admin SPA bundle not found at %s — /admin not mounted", _ADMIN_UI_DIR)


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
