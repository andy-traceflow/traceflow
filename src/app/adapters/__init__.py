from app.adapters.base import CRMAdapter
from app.adapters.registry import get_adapter, register_adapter

__all__ = ["CRMAdapter", "get_adapter", "register_adapter"]
