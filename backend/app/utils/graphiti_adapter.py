"""
GraphitiAdapter — wraps graphiti-core to implement GraphRepository protocol.

Uses Neo4j as the graph store. Bridges async graphiti-core to sync Flask context
via nest_asyncio + a dedicated event loop per adapter instance.
"""
from __future__ import annotations

import asyncio
import sys
import threading
from datetime import datetime
from typing import Any

import nest_asyncio

from ..repositories.graph_repository import NodeDetail, SearchResult
from .logger import get_logger

logger = get_logger('mirofish.graphiti_adapter')


def _get_or_create_loop() -> asyncio.AbstractEventLoop:
    """Get or create an event loop safe for use in Flask (sync) context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    nest_asyncio.apply(loop)
    return loop


class GraphitiAdapter:
    """
    Graphiti-core adapter implementing GraphRepository protocol.

    Wraps graphiti-core's async API with a sync interface for Flask.
    Uses Neo4j for persistent graph storage and an LLM for entity extraction.
    """

    def __init__(
        self,
        neo4j_uri: str,
        neo4j_user: str,
        neo4j_password: str,
        llm_api_key: str,
        llm_base_url: str,
        llm_model: str,
        embedder_model: str = "text-embedding-3-small",
    ) -> None:
        self._neo4j_uri = neo4j_uri
        self._neo4j_user = neo4j_user
        self._neo4j_password = neo4j_password
        self._llm_api_key = llm_api_key
        self._llm_base_url = llm_base_url
        self._llm_model = llm_model
        self._embedder_model = embedder_model
        self._entity_types: dict | None = None  # set via load_ontology()
        self._loop = _get_or_create_loop()
        self._lock = threading.Lock()  # serializes all async calls on the shared event loop
        self._graphiti = self._run(self._init_graphiti())

    def _run(self, coro) -> Any:
        """Run async coroutine synchronously using the adapter's event loop.

        The lock serializes calls from concurrent Flask threads — nest_asyncio allows
        nested loops but does NOT handle concurrent tasks from different threads safely.
        """
        with self._lock:
            return self._loop.run_until_complete(coro)

    def _run_with_retry(self, coro_factory, max_retries: int = 3) -> Any:
        """Run a coroutine factory with exponential backoff retry on Gemini 503 errors."""
        import time as _time
        for attempt in range(max_retries + 1):
            try:
                with self._lock:
                    return self._loop.run_until_complete(coro_factory())
            except Exception as e:
                err = str(e)
                is_503 = '503' in err or 'unavailable' in err.lower() or 'high demand' in err.lower()
                if is_503 and attempt < max_retries:
                    wait = 2 ** attempt
                    logger.warning(f"Gemini 503 in graphiti, retry {attempt + 1}/{max_retries} in {wait}s")
                    _time.sleep(wait)
                    continue
                raise

    def load_ontology(self, ontology: dict) -> None:
        """Convert MiroFish ontology entity types to graphiti format and store."""
        from pydantic import BaseModel as PydanticBase
        entity_types: dict[str, type] = {}
        for et in ontology.get("entity_types", []):
            name = et.get("name", "").strip()
            description = et.get("description", f"A {name} entity")
            if not name:
                continue
            # Dynamic Pydantic model — graphiti uses __doc__ as the entity description
            model = type(name, (PydanticBase,), {"__doc__": description})
            entity_types[name] = model
        self._entity_types = entity_types or None
        logger.info(f"Loaded {len(entity_types)} entity types from ontology: {list(entity_types.keys())}")

    async def _init_graphiti(self):
        """Initialize Graphiti client with LLM, embedder, and Neo4j config."""
        from graphiti_core import Graphiti
        from graphiti_core.llm_client.gemini_client import GeminiClient
        from graphiti_core.llm_client.config import LLMConfig
        from graphiti_core.embedder.gemini import GeminiEmbedder, GeminiEmbedderConfig
        from graphiti_core.cross_encoder.client import CrossEncoderClient

        llm_config = LLMConfig(
            api_key=self._llm_api_key,
            model=self._llm_model,
        )
        llm_client = GeminiClient(config=llm_config)

        embedder_config = GeminiEmbedderConfig(
            api_key=self._llm_api_key,
            embedding_model=self._embedder_model,
        )
        embedder = GeminiEmbedder(config=embedder_config)

        # Passthrough reranker — returns passages unranked, avoids OpenAI dependency
        class PassthroughReranker(CrossEncoderClient):
            async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
                return [(p, 1.0) for p in passages]

        cross_encoder = PassthroughReranker()

        client = Graphiti(
            uri=self._neo4j_uri,
            user=self._neo4j_user,
            password=self._neo4j_password,
            llm_client=llm_client,
            embedder=embedder,
            cross_encoder=cross_encoder,
        )
        await client.build_indices_and_constraints()
        return client

    # ── Episode ingestion ─────────────────────────────────────────────────────

    def add_episode(
        self,
        graph_id: str,
        episode_text: str,
        episode_name: str = "",
    ) -> dict[str, Any]:
        """Add a single text episode to the graph."""
        return self._run_with_retry(lambda: self._add_episode_async(graph_id, episode_text, episode_name))

    async def _add_episode_async(
        self,
        graph_id: str,
        episode_text: str,
        episode_name: str,
    ) -> dict[str, Any]:
        from graphiti_core.nodes import EpisodeType

        result = await self._graphiti.add_episode(
            name=episode_name or f"episode_{graph_id}",
            episode_body=episode_text,
            source_description=f"MiroFish episode for {graph_id}",
            reference_time=datetime.now(),
            source=EpisodeType.text,
            group_id=graph_id,
            entity_types=self._entity_types,
        )
        return {"uuid": getattr(result.episode, "uuid", ""), "processed": True}

    def add_episodes_batch(
        self,
        graph_id: str,
        episodes: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        """Add multiple text episodes concurrently."""
        return self._run_with_retry(lambda: self._add_episodes_batch_async(graph_id, episodes))

    async def _add_episodes_batch_async(
        self,
        graph_id: str,
        episodes: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        from graphiti_core.utils.bulk_utils import RawEpisode
        from graphiti_core.nodes import EpisodeType

        # Convert to RawEpisode format
        raw_episodes = [
            RawEpisode(
                name=ep.get("name", f"ep_{i}"),
                content=ep["data"],
                source_description=f"MiroFish episode batch for {graph_id}",
                source=EpisodeType.text,
                reference_time=datetime.now(),
            )
            for i, ep in enumerate(episodes)
        ]

        # add_episode_bulk is marked WIP in graphiti 0.11.x and has known issues.
        # Use sequential add_episode calls instead.
        output = []
        for i, ep in enumerate(episodes):
            try:
                result = await self._add_episode_async(
                    graph_id, ep["data"], ep.get("name", f"ep_{i}")
                )
                output.append(result)
            except Exception as ep_err:
                logger.warning(f"Episode {i} failed: {ep_err}")
                output.append({"uuid": "", "processed": False, "error": str(ep_err)})
        return output

    # ── Search ────────────────────────────────────────────────────────────────

    def search(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
    ) -> SearchResult:
        """Semantic search over the knowledge graph."""
        return self._run_with_retry(lambda: self._search_async(graph_id, query, limit))

    async def _search_async(
        self,
        graph_id: str,
        query: str,
        limit: int,
    ) -> SearchResult:
        results = await self._graphiti.search(
            query=query,
            group_ids=[graph_id],
            num_results=limit,
        )
        nodes = []
        edges = []
        facts = []

        for r in results:
            fact = getattr(r, "fact", "") or getattr(r, "content", "") or str(r)
            facts.append(fact)
            # Normalize into edge-like dicts for compatibility with existing consumers
            edges.append({
                "uuid": getattr(r, "uuid", ""),
                "name": getattr(r, "name", ""),
                "fact": fact,
                "source_node_uuid": getattr(r, "source_node_uuid", ""),
                "target_node_uuid": getattr(r, "target_node_uuid", ""),
            })

        return SearchResult(nodes=nodes, edges=edges, facts=facts)

    # ── Node / edge enumeration ───────────────────────────────────────────────

    def get_all_nodes(self, graph_id: str) -> list[dict[str, Any]]:
        """Retrieve all nodes belonging to this graph."""
        return self._run(self._get_all_nodes_async(graph_id))

    async def _get_all_nodes_async(self, graph_id: str) -> list[dict[str, Any]]:
        """Query Neo4j for all entity nodes in the graph."""
        try:
            async with self._graphiti.driver.session(database=self._graphiti.database) as session:
                query = """
                MATCH (n:Entity)
                WHERE n.group_id = $group_id
                RETURN n
                LIMIT 1000
                """
                result = await session.run(query, {"group_id": graph_id})
                records = await result.fetch(1000)

                nodes = []
                for record in records:
                    node = record["n"]
                    nodes.append({
                        "uuid": node.get("uuid", ""),
                        "name": node.get("name", ""),
                        "labels": list(node.labels) if hasattr(node, "labels") else [],
                        "summary": node.get("summary", ""),
                        "attributes": node.get("attributes", {}),
                    })
                return nodes
        except Exception as e:
            logger.warning(f"get_all_nodes failed: {e}")
            return []

    def get_all_edges(self, graph_id: str) -> list[dict[str, Any]]:
        """Retrieve all edges belonging to this graph."""
        return self._run(self._get_all_edges_async(graph_id))

    async def _get_all_edges_async(self, graph_id: str) -> list[dict[str, Any]]:
        """Query Neo4j for all entity edges in the graph."""
        try:
            async with self._graphiti.driver.session(database=self._graphiti.database) as session:
                query = """
                MATCH (source:Entity)-[r:RELATES_TO]->(target:Entity)
                WHERE r.group_id = $group_id
                RETURN r, source.uuid AS source_uuid, target.uuid AS target_uuid
                LIMIT 1000
                """
                result = await session.run(query, {"group_id": graph_id})
                records = await result.fetch(1000)

                edges = []
                for record in records:
                    edge = record["r"]
                    edges.append({
                        "uuid": edge.get("uuid", ""),
                        "name": edge.get("name", ""),
                        "fact": edge.get("fact", ""),
                        "source_node_uuid": record["source_uuid"],
                        "target_node_uuid": record["target_uuid"],
                    })
                return edges
        except Exception as e:
            logger.warning(f"get_all_edges failed: {e}")
            return []

    def get_node(self, graph_id: str, node_uuid: str) -> NodeDetail | None:
        """Retrieve a single node by UUID."""
        return self._run(self._get_node_async(graph_id, node_uuid))

    async def _get_node_async(self, graph_id: str, node_uuid: str) -> NodeDetail | None:
        """Query Neo4j for a single node."""
        try:
            async with self._graphiti.driver.session(database=self._graphiti.database) as session:
                query = """
                MATCH (n:Entity)
                WHERE n.uuid = $uuid AND n.group_id = $group_id
                RETURN n
                LIMIT 1
                """
                result = await session.run(query, {"uuid": node_uuid, "group_id": graph_id})
                records = await result.fetch(1)

                if not records:
                    return None

                node = records[0]["n"]
                return NodeDetail(
                    uuid=node.get("uuid", node_uuid),
                    name=node.get("name", ""),
                    labels=list(node.labels) if hasattr(node, "labels") else [],
                    summary=node.get("summary", ""),
                    attributes=node.get("attributes", {}),
                )
        except Exception as e:
            logger.warning(f"get_node failed: {e}")
            return None

    def get_graph_info(self, graph_id: str) -> dict[str, Any]:
        """Return basic metadata about the graph."""
        nodes = self.get_all_nodes(graph_id)
        edges = self.get_all_edges(graph_id)
        return {
            "graph_id": graph_id,
            "node_count": len(nodes),
            "edge_count": len(edges),
        }

    def delete_graph(self, graph_id: str) -> None:
        """Delete all data associated with this graph_id."""
        self._run(self._delete_graph_async(graph_id))

    async def _delete_graph_async(self, graph_id: str) -> None:
        """Delete all nodes and edges for a graph_id via Neo4j."""
        try:
            async with self._graphiti.driver.session(database=self._graphiti.database) as session:
                query = """
                MATCH (n:Entity|Episodic|Community)
                WHERE n.group_id = $group_id
                DETACH DELETE n
                """
                await session.run(query, {"group_id": graph_id})
        except Exception as e:
            logger.warning(f"delete_graph failed: {e}")
