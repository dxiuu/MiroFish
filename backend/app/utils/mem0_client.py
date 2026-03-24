"""Mem0 MemoryClient singleton factory."""

from threading import Lock
from mem0 import MemoryClient

from ..config import Config

_client = None
_lock = Lock()


def get_mem0_client() -> MemoryClient:
    """Return a shared MemoryClient instance (thread-safe, lazy init)."""
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _client = MemoryClient(api_key=Config.MEM0_API_KEY)
    return _client
