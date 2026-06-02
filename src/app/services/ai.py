"""Anthropic API client accessor.

A single shared AsyncAnthropic client for TraceFlow's LLM touchpoints
(greeting, qualifier, KB responder, ...). The client holds a connection
pool — construct it once, not per request.
"""

from __future__ import annotations

from functools import lru_cache

from anthropic import AsyncAnthropic

from app.config import get_settings


@lru_cache
def get_anthropic_client() -> AsyncAnthropic:
    """Return the process-wide AsyncAnthropic client (API key from settings)."""
    return AsyncAnthropic(api_key=get_settings().anthropic_api_key)
