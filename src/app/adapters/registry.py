"""Runtime adapter dispatch.

Adapters are constructed once at import and reused — they hold no
per-request state. Per-tenant variation lives in `ClientConfig`, which
is passed into every call.
"""

from __future__ import annotations

from app.adapters.base import CRMAdapter
from app.adapters.ghl import GoHighLevelAdapter
from app.adapters.hubspot import HubSpotAdapter
from app.adapters.monday import MondayAdapter

_REGISTRY: dict[str, CRMAdapter] = {
    "ghl": GoHighLevelAdapter(),
    "hubspot": HubSpotAdapter(),
    "monday": MondayAdapter(),
}


def register_adapter(name: str, adapter: CRMAdapter) -> None:
    """Register a new adapter at runtime. For tests + future Phase 2 adapters."""
    _REGISTRY[name] = adapter


def get_adapter(provider: str) -> CRMAdapter:
    """Look up the adapter for a CRM provider string.

    Raises ValueError if unknown — callers should validate the
    crm_provider field on client_configs before reaching here.
    """
    if provider not in _REGISTRY:
        raise ValueError(f"Unknown CRM provider: {provider}")
    return _REGISTRY[provider]


def list_providers() -> list[str]:
    return sorted(_REGISTRY.keys())
