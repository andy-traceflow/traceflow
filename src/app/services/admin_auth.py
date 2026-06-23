"""Admin authentication for the self-hosted /api/admin surface (ADR-0004).

Identity lives in the ``admin_users`` table (migration 017): bcrypt password
hashes, a role column as the future-RBAC hook, and ``is_active`` as the kill
switch. ``POST /api/admin/login`` verifies credentials and mints a 12-hour
HS256 JWT signed with ``ADMIN_JWT_SECRET``; ``require_admin_user`` is the
dependency every other admin route hangs off — it verifies the token, then
re-loads the admin row and checks ``is_active`` on every request, so
deactivating an account revokes its tokens immediately (stronger than a jti
denylist, no state needed).

This module deliberately does NOT touch ``middleware/auth.py``'s Supabase
path (``verify_jwt`` / ``require_admin`` — client-portal Phase 3 machinery).
The old static-secret bearer (``verify_admin_token``) is retired: admin
requests now always carry a login-issued JWT with a real identity, so audit
rows can name the actor.

bcrypt is used directly rather than via passlib — passlib (last release
2020) crashes importing bcrypt>=4.1. bcrypt truncates input at 72 bytes;
create_admin.py enforces 8–72-byte passwords. ``checkpw`` blocks the event
loop ~250ms, acceptable because it only runs on /login, which is
rate-limited.

The login rate limiter is in-memory and per-process — correct for the
single Render instance; with horizontal scaling each instance holds its own
buckets (worst case N×limit attempts). Revisit with a shared store if the
service ever scales out.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings
from app.db import get_service_connection, set_demo
from app.demo import DEMO_ADMIN_NAME, DEMO_EMAIL

logger = logging.getLogger(__name__)

# Own scheme instance — distinct from middleware.auth's so the two auth
# worlds never share state.
bearer_scheme = HTTPBearer(auto_error=True)

TOKEN_TTL = timedelta(hours=12)
JWT_ALGORITHM = "HS256"

# Precomputed bcrypt hash of a random pad. Verified against on unknown-email
# logins so that path costs the same ~250ms as a wrong-password check —
# response timing never reveals whether an email exists. Never gensalt() at
# import time; that would add ~250ms to every cold start.
_DUMMY_HASH = "$2b$12$HfhJazm9WDDRDlQ195TwJO7e5aD6nwh.nf.dtlPc394V.M6WuzDdS"


@dataclass(frozen=True)
class AdminInfo:
    """The authenticated admin, as loaded fresh from admin_users."""

    id: UUID
    email: str
    name: str
    role: str
    last_login_at: datetime | None = None


# ===========================================================================
# Password hashing
# ===========================================================================


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time bcrypt check. A malformed stored hash fails closed."""
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def equalize_timing(password: str) -> None:
    """Burn one bcrypt verify against the dummy hash. Called on unknown-email
    logins so they cost the same as a wrong-password check — response timing
    never reveals whether an email exists."""
    verify_password(password, _DUMMY_HASH)


# ===========================================================================
# Tokens
# ===========================================================================


def mint_admin_token(
    admin_id: UUID,
    email: str,
    role: str,
    *,
    expires_in: timedelta = TOKEN_TTL,
) -> tuple[str, datetime]:
    """Issue an HS256 admin session token. Returns (token, expires_at)."""
    settings = get_settings()
    if not settings.admin_jwt_secret:
        raise RuntimeError("ADMIN_JWT_SECRET unset — cannot mint admin tokens")
    now = datetime.now(UTC)
    expires_at = now + expires_in
    token = jwt.encode(
        {
            "sub": str(admin_id),
            "email": email,
            "role": role,
            "iat": now,
            "exp": expires_at,
        },
        settings.admin_jwt_secret,
        algorithm=JWT_ALGORITHM,
    )
    return token, expires_at


async def require_admin_user(
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> AdminInfo:
    """The gate every admin route (except /login) depends on.

    Verifies the bearer JWT's signature + expiry, then loads the admin_users
    row and confirms it is still active. 401 for anything token-shaped that
    fails (missing/garbage/expired/unknown subject); 403 only when identity
    is proven but the account is disabled; 503 when auth isn't configured.
    """
    settings = get_settings()
    if not settings.admin_jwt_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin auth not configured (ADMIN_JWT_SECRET unset)",
        )

    try:
        payload = jwt.decode(
            creds.credentials,
            settings.admin_jwt_secret,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(status_code=401, detail="Admin token expired") from e
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail="Invalid admin token") from e

    try:
        admin_id = UUID(str(payload["sub"]))
    except ValueError as e:
        raise HTTPException(status_code=401, detail="Invalid admin token") from e

    # Demo session: a demo-role token (minted by /api/demo-login, only when
    # DEMO_MODE is on) is confined to the in-memory FakeConn and never loads a
    # real admin_users row. set_demo(True) flips get_service_connection() to the
    # fake connection for the rest of this request. Gated on BOTH the role claim
    # AND demo_mode, so the demo identity is inert anywhere demo mode is off
    # (it falls through and 401s as an unknown admin).
    if payload.get("role") == "demo" and settings.demo_mode:
        set_demo(True)
        return AdminInfo(id=admin_id, email=DEMO_EMAIL, name=DEMO_ADMIN_NAME, role="demo")
    # Real request — clear any stale demo flag inherited on a reused task.
    set_demo(False)

    async with get_service_connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, email, name, role, is_active, last_login_at
            FROM admin_users
            WHERE id = $1
            """,
            admin_id,
        )
    if row is None:
        raise HTTPException(status_code=401, detail="Unknown admin")
    if not row["is_active"]:
        raise HTTPException(status_code=403, detail="Admin account disabled")

    return AdminInfo(
        id=admin_id,
        email=row["email"],
        name=row["name"],
        role=row["role"],
        last_login_at=row["last_login_at"],
    )


_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


async def forbid_demo_writes(
    request: Request, admin: AdminInfo = Depends(require_admin_user)
) -> None:
    """Router dependency: a demo-role session may only read. Blocks every
    mutating verb with a 403 — the hard stop that keeps the public demo from
    e.g. firing leads.py's repush at a real CRM. Real roles pass through.
    Depends on require_admin_user (cached per request), so the gate still runs
    exactly once."""
    if admin.role == "demo" and request.method not in _SAFE_METHODS:
        raise HTTPException(status_code=403, detail="This is a read-only demo")


# ===========================================================================
# Login rate limiting
# ===========================================================================


class LoginRateLimiter:
    """Sliding-window limiter over login FAILURES, keyed by client IP.

    Failures only: a successful login resets the bucket, so the founder
    re-logging in all day never locks themselves out, while an attacker
    cannot exploit that (a "success" requires the password). Window log
    (deque of monotonic timestamps) rather than a fixed window — no
    boundary-burst artifact, and memory is bounded at ``max_attempts``
    floats per IP plus a periodic sweep of stale keys.
    """

    def __init__(
        self,
        max_attempts: int = 5,
        window_seconds: float = 900.0,
        clock: Callable[[], float] = time.monotonic,
        sweep_threshold: int = 10_000,
    ) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._clock = clock
        self._sweep_threshold = sweep_threshold
        self._failures: dict[str, deque[float]] = {}

    def _prune(self, ip: str, now: float) -> deque[float]:
        bucket = self._failures.setdefault(ip, deque())
        cutoff = now - self.window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        return bucket

    def check(self, ip: str) -> None:
        """Raise 429 when the IP has exhausted its failure budget."""
        now = self._clock()
        bucket = self._prune(ip, now)
        if len(bucket) >= self.max_attempts:
            retry_after = max(1, int(bucket[0] + self.window_seconds - now) + 1)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed login attempts — try again later",
                headers={"Retry-After": str(retry_after)},
            )

    def record_failure(self, ip: str) -> None:
        now = self._clock()
        self._prune(ip, now).append(now)
        if len(self._failures) > self._sweep_threshold:
            self._sweep(now)

    def reset(self, ip: str) -> None:
        self._failures.pop(ip, None)

    def _sweep(self, now: float) -> None:
        cutoff = now - self.window_seconds
        stale = [ip for ip, bucket in self._failures.items() if not bucket or bucket[-1] <= cutoff]
        for ip in stale:
            del self._failures[ip]


# Module-level singleton; routers/admin/auth.py references it via the module
# attribute (admin_auth.login_limiter) so tests can swap it out per-test.
login_limiter = LoginRateLimiter()


def client_ip(request: Request) -> str:
    """Best-effort client IP: first X-Forwarded-For hop (Render's proxy sets
    it) or the socket peer. Shared-NAT callers share a bucket — acceptable
    for a founder-only login."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"
