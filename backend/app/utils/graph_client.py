"""
Graph client factory. Returns a GraphRepository instance based on GRAPH_BACKEND env var.

Usage:
    from ..utils.graph_client import make_graph_client
    client = make_graph_client()
"""
from __future__ import annotations

from ..config import Config


def make_graph_client() -> object:
    """
    Returns a GraphRepository-compatible client based on Config.GRAPH_BACKEND.

    'graphiti' → GraphitiAdapter (self-hosted Neo4j, no rate limits)
    'zep'      → ZepAdapter (Zep Cloud or self-hosted Zep OSS, legacy)
    """
    if Config.GRAPH_BACKEND == 'graphiti':
        from .graphiti_adapter import GraphitiAdapter
        return GraphitiAdapter(
            neo4j_uri=Config.GRAPHITI_NEO4J_URI,
            neo4j_user=Config.GRAPHITI_NEO4J_USER,
            neo4j_password=Config.GRAPHITI_NEO4J_PASSWORD,
            llm_api_key=Config.LLM_API_KEY,
            llm_base_url=Config.LLM_BASE_URL,
            llm_model=Config.LLM_MODEL_NAME,
            embedder_model=Config.GRAPHITI_EMBEDDER_MODEL,
        )
    else:
        # Default: Zep backend (legacy)
        from .zep_client import make_zep_client
        return make_zep_client()
