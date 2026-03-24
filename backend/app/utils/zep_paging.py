"""
Stub replacement for zep_paging.

Mem0's get_all() returns the full dataset in one call, so paginated
node/edge fetching is no longer needed.  These stubs exist to avoid
breaking any import that still references this module.
"""

from typing import Any, List


def fetch_all_nodes(client: Any, graph_id: str) -> List:
    """No-op stub — Mem0 uses get_all() instead of paginated node fetch."""
    return []


def fetch_all_edges(client: Any, graph_id: str) -> List:
    """No-op stub — Mem0 uses get_all() instead of paginated edge fetch."""
    return []
