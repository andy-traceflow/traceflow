"""GoHighLevel adapter (stub).

Default CRM recommendation for new clients — 40% recurring affiliate.
Real implementation is the first Phase 0 build after the platform
skeleton lands. The stub raises NotImplementedError so wiring it up
prematurely fails loudly.
"""

from __future__ import annotations

from typing import Any

from app.models.client_config import ClientConfig
from app.models.lead import Lead, LeadCreate


class GoHighLevelAdapter:
    name = "ghl"

    async def push_lead(self, lead: Lead, config: ClientConfig) -> str:
        raise NotImplementedError("GoHighLevel adapter not yet implemented")

    async def update_lead(
        self,
        external_id: str,
        updates: dict[str, Any],
        config: ClientConfig,
    ) -> None:
        raise NotImplementedError("GoHighLevel adapter not yet implemented")

    async def parse_webhook(
        self,
        payload: dict[str, Any],
        config: ClientConfig,
    ) -> LeadCreate:
        raise NotImplementedError("GoHighLevel adapter not yet implemented")

    async def health_check(self, config: ClientConfig) -> bool:
        return False
