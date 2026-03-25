"""
ZepAdapter — OSS/Cloud 兼容层。

自托管 Zep OSS 不支持以下 4 个 Cloud 专属 API：
  1. graph.set_ontology()   — OSS 无本体配置接口
  2. graph.add_batch()      — OSS 无批量 episode 接口
  3. episode.get().processed — OSS 可能无此字段（同步处理）
  4. graph.search(reranker=) — OSS 不支持 reranker 参数

ZepAdapter 通过组合模式包装原始 Zep 客户端，
对以上 4 个场景提供静默降级处理，对调用方透明。
"""

from __future__ import annotations

import time
from typing import Any

from zep_cloud.client import Zep

from .logger import get_logger

logger = get_logger('mirofish.zep_adapter')


class EpisodeAdapter:
    """包装 episode API，确保 .processed 字段始终存在。"""

    def __init__(self, episode: Any) -> None:
        self._episode = episode

    def get(self, uuid_: str) -> Any:
        ep = self._episode.get(uuid_=uuid_)
        if not hasattr(ep, 'processed'):
            # OSS 同步处理，无需轮询
            ep.processed = True
        return ep

    def __getattr__(self, name: str) -> Any:
        return getattr(self._episode, name)


class GraphAdapter:
    """包装 graph API，降级处理 OSS 不支持的方法。"""

    def __init__(self, graph: Any) -> None:
        self._graph = graph
        self.episode = EpisodeAdapter(graph.episode)

    def set_ontology(self, graph_ids: Any, entities: Any = None, edges: Any = None) -> None:
        """OSS 不支持时静默跳过（不中断流程）。"""
        try:
            return self._graph.set_ontology(graph_ids=graph_ids, entities=entities, edges=edges)
        except Exception as e:
            logger.warning(
                f"set_ontology 不可用（Zep OSS 不支持）: {type(e).__name__}。"
                "图谱将使用默认模式提取实体，不影响主流程。"
            )
            return None

    def add_batch(self, graph_id: str, episodes: list[Any]) -> list[Any]:
        """OSS 不支持时回退为逐条 add，含 3 次重试。"""
        try:
            return self._graph.add_batch(graph_id=graph_id, episodes=episodes)
        except Exception as e:
            logger.info(
                f"add_batch 不可用（{type(e).__name__}），回退为逐条 add（共 {len(episodes)} 条）"
            )
            results = []
            for ep in episodes:
                for attempt in range(3):
                    try:
                        result = self._graph.add(
                            graph_id=graph_id,
                            type=ep.type,
                            data=ep.data,
                        )
                        results.append(result)
                        break
                    except Exception as inner_e:
                        if attempt == 2:
                            raise
                        logger.warning(
                            f"逐条 add 第 {attempt + 1} 次失败: {inner_e}，1s 后重试…"
                        )
                        time.sleep(1)
            return results

    def search(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
        scope: str = "edges",
        reranker: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """OSS 不支持 reranker 参数时自动降级。"""
        try:
            return self._graph.search(
                graph_id=graph_id,
                query=query,
                limit=limit,
                scope=scope,
                reranker=reranker or "cross_encoder",
                **kwargs,
            )
        except TypeError as e:
            if "reranker" in str(e):
                logger.info("reranker 参数不支持（Zep OSS），已降级为不带 reranker 的搜索")
                return self._graph.search(
                    graph_id=graph_id,
                    query=query,
                    limit=limit,
                    scope=scope,
                    **kwargs,
                )
            raise

    def __getattr__(self, name: str) -> Any:
        return getattr(self._graph, name)


class ZepAdapter:
    """
    Zep 客户端适配器，透明包装原始 Zep 实例。

    用法：
        from zep_cloud.client import Zep
        from .zep_adapter import ZepAdapter

        client = ZepAdapter(Zep(api_url="http://localhost:8000", api_key="local"))
        # 所有调用与原始 Zep 客户端一致
    """

    def __init__(self, zep: Zep) -> None:
        self._zep = zep
        self.graph = GraphAdapter(zep.graph)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._zep, name)
