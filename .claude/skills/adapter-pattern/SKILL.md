---
name: adapter-pattern
description: How to build and modify integration adapters (CRMs like GoHighLevel/HubSpot/Monday, e-commerce like Shopify, messaging like Twilio). Load when adding a new external system, updating an existing adapter, or troubleshooting cross-system data translation. Covers the three-layer model: adapters, field mappings, generic webhook configs.
---

# Adapter Pattern

TraceFlow handles many client systems through three layers: **adapters** (Layer 1), **field mappings** (Layer 2), and **generic webhook configs** (Layer 3). Use this skill when adding integrations or debugging translation issues.

## When to use which layer

| Situation | Layer to use |
|---|---|
| 3+ clients use the same CRM | Build a full adapter (Layer 1) |
| 2 clients on the same CRM but different custom fields | Use field mappings (Layer 2) |
| One client uses an obscure system | Generic webhook config (Layer 3) |
| Client has a homegrown PHP CRM from 2014 | Generic webhook config (Layer 3) |

**Do not** build a full adapter speculatively. The cost of adapter maintenance compounds; only build when sustained demand justifies it.

## Layer 1: Adapter interface

```python
from typing import Protocol
from app.models import Lead, ClientConfig

class CRMAdapter(Protocol):
    """All CRM adapters conform to this interface."""
    
    async def push_lead(
        self,
        lead: Lead,
        config: ClientConfig,
    ) -> str:
        """Create lead in external system. Returns external_id."""
        ...
    
    async def update_lead(
        self,
        external_id: str,
        updates: dict,
        config: ClientConfig,
    ) -> None:
        """Update an existing lead."""
        ...
    
    async def parse_webhook(
        self,
        payload: dict,
        config: ClientConfig,
    ) -> Lead:
        """Translate inbound webhook → canonical Lead."""
        ...
    
    async def health_check(
        self,
        config: ClientConfig,
    ) -> bool:
        """Verify credentials and connectivity."""
        ...
```

## Layer 1: Implementation pattern

Use this template for any new adapter:

```python
# app/adapters/<system_name>.py

import httpx
from app.models import Lead, ClientConfig
from app.adapters.base import CRMAdapter
from app.field_mappings import resolve_mappings

class GoHighLevelAdapter:
    BASE_URL = "https://services.leadconnectorhq.com"
    
    async def push_lead(self, lead: Lead, config: ClientConfig) -> str:
        credentials = config.crm_credentials
        api_key = credentials["api_key"]
        location_id = credentials["location_id"]
        
        # Look up field mappings — DO NOT hardcode field names
        mappings = await resolve_mappings(
            client_id=lead.client_id,
            integration="crm"
        )
        
        # Build payload using canonical → external translation
        payload = {
            "firstName": (lead.contact_name or "").split()[0],
            "lastName": " ".join((lead.contact_name or "").split()[1:]) or "",
            "phone": lead.phone,
            "email": lead.email,
            "locationId": location_id,
            "customField": {}
        }
        
        # Apply per-client custom field mappings
        for canonical_field in ['sqft', 'service_type', 'budget_range', 'timeframe']:
            mapping = mappings.get(canonical_field)
            if not mapping:
                continue
            
            value = getattr(lead, canonical_field, None)
            if value is None:
                continue
            
            # Apply transform if configured
            if mapping.transform:
                value = apply_transform(value, mapping.transform)
            
            # Route to correct field type
            if mapping.external_field_type == "custom_field":
                payload["customField"][mapping.external_field] = value
            else:
                payload[mapping.external_field] = value
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.BASE_URL}/contacts/",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()["contact"]["id"]
    
    async def parse_webhook(self, payload: dict, config: ClientConfig) -> Lead:
        # Inverse direction: external payload → canonical Lead
        mappings = await resolve_mappings(client_id=config.client_id, integration="crm")
        
        lead_data = {
            "client_id": config.client_id,
            "external_id": payload.get("contact_id"),
            "source_system": "ghl",
            "contact_name": f"{payload.get('firstName', '')} {payload.get('lastName', '')}".strip(),
            "phone": payload.get("phone"),
            "email": payload.get("email"),
            "raw_payload": payload,
        }
        
        # Reverse-map custom fields
        custom_fields = payload.get("customField", {})
        for canonical_field, mapping in mappings.items():
            if mapping.external_field_type == "custom_field":
                value = custom_fields.get(mapping.external_field)
                if value is not None and mapping.transform:
                    value = apply_inverse_transform(value, mapping.transform)
                if value is not None:
                    lead_data[canonical_field] = value
        
        return Lead(**lead_data)
    
    async def health_check(self, config: ClientConfig) -> bool:
        api_key = config.crm_credentials["api_key"]
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.BASE_URL}/locations/me",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=5.0,
            )
            return response.status_code == 200
```

## Adapter registry

```python
# app/adapters/__init__.py

ADAPTER_REGISTRY: dict[str, CRMAdapter] = {
    "ghl": GoHighLevelAdapter(),
    "hubspot": HubSpotAdapter(),
    "monday": MondayAdapter(),
    "generic": GenericWebhookAdapter(),  # Layer 3 fallback
}

def get_adapter(provider: str) -> CRMAdapter:
    if provider not in ADAPTER_REGISTRY:
        raise ValueError(f"Unknown CRM provider: {provider}")
    return ADAPTER_REGISTRY[provider]
```

## Layer 2: Field mappings

Field mappings live in `client_field_mappings` and are queried at runtime:

```python
async def resolve_mappings(
    client_id: UUID,
    integration: str,
) -> dict[str, FieldMapping]:
    """Returns canonical_field → FieldMapping for this client + integration."""
    rows = await db.fetch_all(
        """
        select canonical_field, external_field, external_field_type, transform
        from client_field_mappings
        where client_id = $1 and integration = $2
        """,
        client_id, integration,
    )
    return {row.canonical_field: FieldMapping(**row) for row in rows}
```

### Transform types

The `transform` JSONB column supports:

```python
def apply_transform(value, transform: dict):
    transform_type = transform["type"]
    
    if transform_type == "value_map":
        # {"countertop": "Kitchen Counter"}
        return transform["mapping"].get(value, value)
    
    elif transform_type == "regex_replace":
        # {"pattern": "...", "replacement": "..."}
        import re
        return re.sub(transform["pattern"], transform["replacement"], str(value))
    
    elif transform_type == "numeric_scale":
        # multiply or divide; for sqft <-> sqm conversions, etc.
        return value * transform["factor"]
    
    elif transform_type == "concatenate":
        # combine multiple canonical fields into one external field
        # special case: receives a dict of all canonical values
        return transform["separator"].join(
            str(value.get(f, "")) for f in transform["fields"]
        )
    
    elif transform_type == "split":
        # opposite of concatenate; split one value into multiple
        return str(value).split(transform["separator"])
    
    else:
        raise ValueError(f"Unknown transform type: {transform_type}")
```

## AI-assisted field mapping generation

During onboarding, use Claude to propose initial mappings:

```python
async def suggest_field_mappings(
    sample_record: dict,
    integration: str,
) -> list[FieldMapping]:
    """Ask Claude to map a sample external record to our canonical schema."""
    prompt = FIELD_MAPPING_PROMPT.format(
        canonical_schema=CANONICAL_LEAD_FIELDS,
        sample_record=json.dumps(sample_record, indent=2),
        integration=integration,
    )
    
    response = await anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    
    # Claude returns JSON; parse and validate
    suggested = json.loads(extract_json(response.content[0].text))
    return [FieldMapping(**m) for m in suggested]
```

Founder reviews + approves before writing to DB.

## Layer 3: Generic webhook handler

For long-tail systems where building an adapter isn't justified:

```python
@app.post("/webhooks/generic/{client_id}/{slug}")
async def handle_generic_webhook(client_id: UUID, slug: str, request: Request):
    payload = await request.json()
    
    config = await GenericWebhookConfig.get(client_id=client_id, slug=slug)
    if not config:
        raise HTTPException(status_code=404)
    
    # Verify signature
    verify_signature(request, secret=config.signing_secret)
    
    # Extract fields using configured expressions
    lead_data = {"client_id": client_id, "source_system": slug, "raw_payload": payload}
    
    for canonical_field, expression in config.field_extractors.items():
        value = extract_value(payload, expression, config.parser_type)
        if value is not None:
            lead_data[canonical_field] = value
    
    lead = Lead(**lead_data)
    await process_new_lead(lead)
    return {"ok": True}


def extract_value(payload: dict, expression: str, parser_type: str):
    if parser_type == "jsonpath":
        import jsonpath_ng
        expr = jsonpath_ng.parse(expression)
        matches = [m.value for m in expr.find(payload)]
        return matches[0] if matches else None
    
    elif parser_type == "jq":
        import jq
        return jq.compile(expression).input(payload).first()
    
    elif parser_type == "python_template":
        # Safe restricted-eval; avoid in production without sandboxing
        raise NotImplementedError("python_template parser requires sandbox")
    
    else:
        raise ValueError(f"Unknown parser type: {parser_type}")
```

## Testing adapters

Every new adapter ships with:

1. **Unit tests** for `parse_webhook` and `push_lead` translation
2. **Integration test** that hits the real external API in a sandbox/test mode
3. **Tenant isolation test** confirming the adapter doesn't accidentally cross tenants
4. **Health check test** verifying credential validation

```python
# tests/adapters/test_ghl.py

async def test_ghl_push_lead_translates_canonical_to_external():
    adapter = GoHighLevelAdapter()
    lead = make_test_lead(sqft=120, service_type="countertop")
    config = make_test_config(crm_provider="ghl")
    
    # Mock the GHL API
    with mock_httpx_post() as mock:
        await adapter.push_lead(lead, config)
        
        # Assert the outbound payload matches expected translation
        called_payload = mock.last_call.json
        assert called_payload["customField"]["project_size_sqft"] == 120
        assert called_payload["customField"]["service_category"] == "Kitchen Counter"
```

## Adapter health monitoring

Every active client has a health check run hourly:

```python
async def hourly_adapter_health_check():
    async for client in fetch_active_clients():
        async with set_tenant_context(client.id):
            adapter = get_adapter(client.config.crm_provider)
            healthy = await adapter.health_check(client.config)
            
            if not healthy:
                await alert_founder(client_id=client.id, issue="adapter_unhealthy")
                await mark_integration_unhealthy(client.id, client.config.crm_provider)
```

This catches credential rotations, API outages, and rate-limit issues before clients notice.
