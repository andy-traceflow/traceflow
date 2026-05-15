---
name: fastapi-supabase
description: TraceFlow backend conventions — FastAPI app structure, Supabase client patterns, middleware, error handling, testing, deployment to Render, secret management. Load when writing or reviewing backend Python code. Complements multi-tenant-arch (which covers tenant isolation specifically).
---

# FastAPI + Supabase Backend

The TraceFlow backend is a single FastAPI service on Render, talking to a single Supabase project. This skill captures conventions for building it.

## Project structure

```
traceflow/
├── src/
│   └── app/
│       ├── __init__.py
│       ├── main.py                    # FastAPI app instantiation
│       ├── config.py                  # Settings, env loading
│       ├── db.py                      # Supabase / Postgres connection
│       │
│       ├── middleware/
│       │   ├── tenant_resolver.py     # Sets app.current_client_id
│       │   ├── signature_verify.py    # Webhook signature checks
│       │   └── request_logging.py
│       │
│       ├── webhooks/
│       │   ├── twilio.py              # /webhooks/twilio/*
│       │   ├── shopify.py
│       │   ├── crm.py
│       │   └── generic.py
│       │
│       ├── api/
│       │   ├── admin.py               # /api/admin/* (internal)
│       │   └── portal.py              # /api/portal/* (client-facing, later)
│       │
│       ├── models/
│       │   ├── client.py
│       │   ├── lead.py
│       │   ├── config.py
│       │   ├── message.py
│       │   └── event.py
│       │
│       ├── adapters/
│       │   ├── base.py                # Protocol
│       │   ├── ghl.py
│       │   ├── hubspot.py
│       │   ├── monday.py
│       │   ├── generic.py
│       │   └── registry.py
│       │
│       ├── services/
│       │   ├── lead_processor.py      # Domain: lead intake → CRM push
│       │   ├── qualifier.py           # AI qualification orchestration
│       │   ├── digest.py              # Daily digest generation
│       │   └── notifications.py       # Owner alerts, etc.
│       │
│       ├── prompts/
│       │   ├── greeting.py
│       │   ├── qualifier.py
│       │   ├── kb_responder.py
│       │   └── field_mapper.py
│       │
│       └── jobs/
│           ├── digest_cron.py
│           ├── adapter_health.py
│           └── ai_usage_reset.py
│
├── tests/
│   ├── conftest.py
│   ├── test_tenant_isolation.py       # NON-NEGOTIABLE
│   ├── adapters/
│   ├── webhooks/
│   ├── services/
│   └── prompts/
│       └── golden/
│
├── scripts/
│   ├── onboard-client.py
│   ├── suggest-field-mappings.py
│   ├── run-migrations.py
│   └── seed-dev-data.py
│
├── migrations/
│   └── *.sql
│
├── pyproject.toml
├── render.yaml                        # Render deployment config
└── .env.example
```

## Settings + config

```python
# app/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)
    
    # Environment
    environment: str = "development"  # 'development' | 'staging' | 'production'
    
    # Database
    supabase_url: str
    supabase_service_key: str  # ONLY for admin operations; never expose
    supabase_anon_key: str     # For client SDK (Phase 3+)
    
    # External APIs
    anthropic_api_key: str
    openai_api_key: str | None = None
    twilio_account_sid: str
    twilio_auth_token: str
    resend_api_key: str
    
    # App
    base_url: str = "https://api.traceflow.app"
    log_level: str = "INFO"
    sentry_dsn: str | None = None
    
    # Security
    admin_jwt_secret: str  # For admin auth
    
settings = Settings()
```

All secrets via environment variables. **Never commit `.env`**. `.env.example` documents what's needed.

## Database connection

```python
# app/db.py
import asyncpg
from contextlib import asynccontextmanager
from contextvars import ContextVar
from uuid import UUID

_pool: asyncpg.Pool | None = None
_current_tenant: ContextVar[UUID | None] = ContextVar('_current_tenant', default=None)

async def init_pool():
    global _pool
    _pool = await asyncpg.create_pool(
        dsn=settings.supabase_url,
        min_size=2,
        max_size=10,
    )

async def close_pool():
    if _pool:
        await _pool.close()

@asynccontextmanager
async def get_connection():
    """Get a connection scoped to the current tenant context."""
    async with _pool.acquire() as conn:
        client_id = _current_tenant.get()
        if client_id:
            await conn.execute(
                "set local app.current_client_id = $1",
                str(client_id),
            )
        yield conn

@asynccontextmanager
async def set_tenant_context(client_id: UUID):
    """For background jobs / admin code: explicitly scope to one tenant."""
    token = _current_tenant.set(client_id)
    try:
        async with get_connection() as conn:
            yield conn
    finally:
        _current_tenant.reset(token)
```

## Middleware pattern

```python
# app/middleware/tenant_resolver.py
from fastapi import Request, HTTPException

async def tenant_resolver_middleware(request: Request, call_next):
    client_id = await _extract_client_id(request)
    
    if client_id:
        # Verify client exists and is active (use admin context for this lookup)
        client = await Client.get_admin(client_id)
        if not client or client.status != 'active':
            raise HTTPException(status_code=404, detail="Client not found")
        
        # Verify webhook signature
        if request.url.path.startswith("/webhooks/"):
            verify_webhook_signature(request, client)
        
        # Set tenant context for downstream handlers
        token = _current_tenant.set(client_id)
        try:
            response = await call_next(request)
        finally:
            _current_tenant.reset(token)
        return response
    
    return await call_next(request)
```

## Webhook endpoint pattern

```python
# app/webhooks/twilio.py
from fastapi import APIRouter, Request, BackgroundTasks
from uuid import UUID

router = APIRouter(prefix="/webhooks/twilio")

@router.post("/missed-call/{client_id}")
async def missed_call_webhook(
    client_id: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
):
    payload = await request.form()
    
    # Always log raw event first (for debugging)
    await Event.create(
        event_type="twilio_missed_call",
        payload=dict(payload),
    )
    
    # Process in background — webhook responses must be <5s
    background_tasks.add_task(
        process_missed_call,
        client_id=client_id,
        caller_phone=payload["From"],
        called_number=payload["To"],
    )
    
    return {"ok": True}
```

**Always respond fast (<5s).** Twilio retries on 5xx or timeout. Heavy processing goes to background tasks or job queues.

## Service pattern (domain logic)

```python
# app/services/lead_processor.py

async def process_missed_call(
    client_id: UUID,
    caller_phone: str,
    called_number: str,
):
    """End-to-end: missed call → SMS sent."""
    
    async with set_tenant_context(client_id) as conn:
        config = await ClientConfig.get_current(conn)
        
        # Create lead
        lead = await Lead.create(
            conn,
            source_system="twilio_missed_call",
            phone=caller_phone,
            raw_payload={"to": called_number, "from": caller_phone},
        )
        
        # Generate greeting
        greeting = await generate_greeting(config, caller_phone)
        
        # Send via Twilio
        await send_sms(
            from_number=config.twilio_number,
            to_number=caller_phone,
            body=greeting,
        )
        
        # Log outbound message
        await Message.create(
            conn,
            lead_id=lead.id,
            direction="outbound",
            channel="sms",
            body=greeting,
            ai_generated=True,
        )
        
        # Track AI usage
        await increment_ai_usage(conn, count=1)
```

## Error handling

```python
# app/middleware/error_handler.py
from fastapi import Request
from fastapi.responses import JSONResponse
import sentry_sdk

async def error_handler_middleware(request: Request, call_next):
    try:
        return await call_next(request)
    except HTTPException:
        raise  # Let FastAPI handle these
    except Exception as e:
        # Log full context to Sentry
        sentry_sdk.capture_exception(e)
        
        # In production, return generic 500
        if settings.environment == "production":
            return JSONResponse(
                status_code=500,
                content={"error": "internal_error", "request_id": get_request_id()},
            )
        
        # In dev, show the traceback
        raise
```

## Testing patterns

```python
# tests/conftest.py
import pytest
import asyncpg

@pytest.fixture(scope="session")
async def db_pool():
    pool = await asyncpg.create_pool(dsn=TEST_DB_URL)
    yield pool
    await pool.close()

@pytest.fixture
async def test_client_a(db_pool):
    """Create an isolated test tenant for Client A."""
    async with db_pool.acquire() as conn:
        client = await create_test_client(conn, slug="test-client-a")
    yield client
    async with db_pool.acquire() as conn:
        await delete_test_client(conn, client.id)

@pytest.fixture
async def test_client_b(db_pool):
    """Create an isolated test tenant for Client B."""
    # ... same as above
```

**The tenant isolation test is non-negotiable. Update it whenever new endpoints are added.**

## Deployment

`render.yaml`:

```yaml
services:
  - type: web
    name: traceflow-api
    env: python
    plan: starter  # $7/mo
    buildCommand: pip install -e .
    startCommand: uvicorn app.main:app --host 0.0.0.0 --port $PORT
    healthCheckPath: /health
    envVars:
      - key: ENVIRONMENT
        value: production
      - fromGroup: traceflow-secrets

  - type: cron
    name: nightly-digest
    env: python
    schedule: "0 13 * * *"  # 6am Pacific
    buildCommand: pip install -e .
    startCommand: python -m app.jobs.digest_cron
    envVars:
      - fromGroup: traceflow-secrets

  - type: cron
    name: adapter-health-check
    env: python
    schedule: "0 * * * *"  # hourly
    buildCommand: pip install -e .
    startCommand: python -m app.jobs.adapter_health
    envVars:
      - fromGroup: traceflow-secrets

envVarGroups:
  - name: traceflow-secrets
    envVars:
      - key: SUPABASE_URL
        sync: false
      - key: SUPABASE_SERVICE_KEY
        sync: false
      # ... etc
```

## Logging

Use structured logging (JSON in production, pretty in dev):

```python
import structlog

logger = structlog.get_logger()

# In handlers
logger.info(
    "missed_call_received",
    client_id=str(client_id),
    caller_phone=caller_phone[-4:],  # last 4 only for privacy
    duration_ms=elapsed,
)
```

**Always include `client_id` in logs** for tenant-scoped events. Makes debugging cross-tenant issues actually possible.

## Common pitfalls

- **Forgetting `await`** on async DB calls. Mypy + strict typing catches most of these.
- **Querying without tenant context.** Will return empty or NULL if RLS is on; will leak if RLS is misconfigured. Run isolation tests.
- **Blocking the webhook response.** Twilio doesn't wait. Use BackgroundTasks or a job queue.
- **Hardcoding business hours, timezones, or phone formats.** Always per-client config.
- **Returning raw external API errors to clients.** Wrap them; never expose vendor stack traces.

## Local development

```bash
# .env (not committed)
SUPABASE_URL=postgresql://...local-supabase-instance
ANTHROPIC_API_KEY=sk-ant-...
# etc

# Start
uv pip install -e .
uvicorn app.main:app --reload --port 8000

# Run tests
pytest tests/ -v

# Run tenant isolation specifically
pytest tests/test_tenant_isolation.py -v --strict
```
