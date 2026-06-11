"""CRM adapter Protocol.

Every supported integration (GHL, HubSpot, Monday, generic webhook
config) conforms to this interface. Adapters live in
app/adapters/<provider>.py; the registry in app/adapters/registry.py
dispatches at runtime based on client_configs.crm_provider.

Field mapping (Layer 2) and signing-secret lookup happen outside the
adapter — they are stable across providers and live in services/.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from app.models.client_config import ClientConfig
from app.models.crm_contact import CRMContact
from app.models.lead import Lead, LeadCreate


@runtime_checkable
class CRMAdapter(Protocol):
    """All CRM adapters conform to this Protocol."""

    name: str  # 'ghl' | 'hubspot' | 'monday' | 'generic'

    async def push_lead(self, lead: Lead, config: ClientConfig) -> str:
        """Create the lead in the external system. Returns the external_id."""
        ...

    async def update_lead(
        self,
        external_id: str,
        updates: dict[str, Any],
        config: ClientConfig,
    ) -> None:
        """Update fields on an existing external record."""
        ...

    async def parse_webhook(
        self,
        payload: dict[str, Any],
        config: ClientConfig,
    ) -> LeadCreate:
        """Translate an inbound webhook from this provider into a canonical LeadCreate."""
        ...

    async def health_check(self, config: ClientConfig) -> bool:
        """Verify credentials + connectivity. Returns True if reachable."""
        ...

    async def lookup_by_phone(
        self,
        phone: str,
        config: ClientConfig,
    ) -> CRMContact | None:
        """Best-effort: find an existing contact by phone number.

        Returns None if not found, unsupported, or on error — callers must
        treat None as 'no match, proceed as potential lead', never as a hard
        failure. The caller_classification stage degrades gracefully on None,
        and post-reply intent classification is the safety net.

        Implementations MUST enforce their own short timeout (~2s): a slow CRM
        must never delay the missed-call SMS past its <30s target.
        """
        ...

    async def fetch_recovered_value(
        self,
        external_id: str,
        config: ClientConfig,
    ) -> Decimal | None:
        """Best-effort: the booked revenue the CRM has recorded for this contact.

        Returns the contact's confirmed booked dollars (e.g. HubSpot
        total_revenue / the sum of closed-won deals), or None if unsupported,
        not yet booked, not found, or on error. Like lookup_by_phone, callers
        MUST treat None as 'no confirmed value yet', never a hard failure.

        Runs in the background revenue_sync job, not on the hot path, but
        implementations SHOULD still enforce a short timeout so the job stays
        bounded across many leads.
        """
        ...
