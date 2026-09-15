"""Runtime adapter dispatch.

Adapters are constructed once, on first use, and reused — they hold no
per-event state. Construction is lazy (not at import) because it reads
credentials from the environment and fails closed when they are missing;
the lifespan handler triggers it at boot.
"""

from __future__ import annotations

from collections.abc import Callable

from app.adapters.base import Destination
from app.adapters.hubspot import HubSpotAdapter
from app.adapters.monday import MondayAdapter
from app.adapters.notion import NotionAdapter
from app.adapters.sheets import SheetsAdapter
from app.adapters.slack import SlackAdapter

_FACTORIES: dict[str, Callable[[], Destination]] = {
    "hubspot": HubSpotAdapter,
    "monday": MondayAdapter,
    "notion": NotionAdapter,
    "sheets": SheetsAdapter,
    "slack": SlackAdapter,
}
_INSTANCES: dict[str, Destination] = {}


def register_adapter(name: str, factory: Callable[[], Destination]) -> None:
    """Register (or replace) an adapter factory at runtime. For tests + future destinations."""
    _FACTORIES[name] = factory
    _INSTANCES.pop(name, None)


def get_adapter(provider: str) -> Destination:
    """Return the singleton adapter for a destination name, constructing it on first call.

    Raises ValueError for an unknown name, AdapterConfigError (a ValueError)
    when the adapter's credentials are missing.
    """
    if provider not in _FACTORIES:
        raise ValueError(f"Unknown destination: {provider!r}. Known: {', '.join(list_providers())}")
    if provider not in _INSTANCES:
        _INSTANCES[provider] = _FACTORIES[provider]()
    return _INSTANCES[provider]


def list_providers() -> list[str]:
    return sorted(_FACTORIES.keys())


def reset_registry() -> None:
    """Drop constructed instances so the next get_adapter() re-reads the environment. Tests only."""
    _INSTANCES.clear()
