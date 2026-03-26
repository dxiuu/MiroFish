"""
Graph backend abstraction — duck-typed Protocol.
All graph backends (Zep, Graphiti) must implement this interface.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class SearchResult:
    """Search result from graph query."""
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)


@dataclass
class NodeDetail:
    """Detailed node information."""
    uuid: str
    name: str
    labels: list[str] = field(default_factory=list)
    summary: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)


class GraphRepository(Protocol):
    """Duck-typed protocol for graph backends."""

    def add_episode(self, graph_id: str, episode_text: str, episode_name: str = "") -> dict[str, Any]: ...
    def add_episodes_batch(self, graph_id: str, episodes: list[dict[str, str]]) -> list[dict[str, Any]]: ...
    def search(self, graph_id: str, query: str, limit: int = 10) -> SearchResult: ...
    def get_all_nodes(self, graph_id: str) -> list[dict[str, Any]]: ...
    def get_all_edges(self, graph_id: str) -> list[dict[str, Any]]: ...
    def get_node(self, graph_id: str, node_uuid: str) -> NodeDetail | None: ...
    def delete_graph(self, graph_id: str) -> None: ...
    def get_graph_info(self, graph_id: str) -> dict[str, Any]: ...
