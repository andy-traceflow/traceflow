"""Health endpoint.

Phase 1 placeholder: liveness only. Phase 6 extends this with DB
connectivity, the destination adapter's `health_check()`, the count of
`dead` events, and the timestamp of the most recent successful delivery.
"""

from __future__ import annotations

import os

from fastapi import APIRouter

from app.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness + the git commit Render injected at build time."""
    settings = get_settings()
    return {
        "status": "ok",
        "environment": settings.environment,
        "git_commit": os.getenv("RENDER_GIT_COMMIT", "unknown")[:7],
    }
