"""
Mem0 实体读取与过滤服务
从 Mem0 图谱中读取关系，筛选出符合预定义实体类型的节点
"""

import uuid
import hashlib
import time
from typing import Dict, Any, List, Optional, Set, Callable, TypeVar
from dataclasses import dataclass, field

from ..config import Config
from ..utils.logger import get_logger
from ..utils.mem0_client import get_mem0_client

logger = get_logger('mirofish.zep_entity_reader')

T = TypeVar('T')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _name_to_uuid(name: str) -> str:
    """Generate a deterministic UUID from an entity name (MD5-based)."""
    return str(uuid.UUID(hashlib.md5(name.encode()).hexdigest()))


def _parse_mem0_all(data) -> tuple:
    """Parse Mem0 get_all() response → (results, relations)."""
    if isinstance(data, dict):
        results = data.get("results") or []
        relations = data.get("relations") or []
    elif isinstance(data, list):
        results = data
        relations = []
    else:
        results, relations = [], []
    return results, relations


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class EntityNode:
    """实体节点数据结构"""
    uuid: str
    name: str
    labels: List[str]
    summary: str
    attributes: Dict[str, Any]
    related_edges: List[Dict[str, Any]] = field(default_factory=list)
    related_nodes: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "labels": self.labels,
            "summary": self.summary,
            "attributes": self.attributes,
            "related_edges": self.related_edges,
            "related_nodes": self.related_nodes,
        }

    def get_entity_type(self) -> Optional[str]:
        """获取实体类型（排除默认的 Entity 标签）"""
        for label in self.labels:
            if label not in ["Entity", "Node"]:
                return label
        return None


@dataclass
class FilteredEntities:
    """过滤后的实体集合"""
    entities: List[EntityNode]
    entity_types: Set[str]
    total_count: int
    filtered_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entities": [e.to_dict() for e in self.entities],
            "entity_types": list(self.entity_types),
            "total_count": self.total_count,
            "filtered_count": self.filtered_count,
        }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class ZepEntityReader:
    """
    Mem0 实体读取与过滤服务

    主要功能：
    1. 从 Mem0 图谱读取所有关系（边）并推导节点
    2. 通过语义搜索筛选符合预定义实体类型的节点
    3. 获取每个实体的相关边和关联节点信息
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or Config.MEM0_API_KEY
        if not self.api_key:
            raise ValueError("MEM0_KEY 未配置")

        self.client = get_mem0_client()

    def _call_with_retry(
        self,
        func: Callable[[], T],
        operation_name: str,
        max_retries: int = 3,
        initial_delay: float = 2.0
    ) -> T:
        """带重试机制的 API 调用"""
        last_exception = None
        delay = initial_delay

        for attempt in range(max_retries):
            try:
                return func()
            except Exception as e:
                last_exception = e
                if attempt < max_retries - 1:
                    logger.warning(
                        f"Mem0 {operation_name} 第 {attempt + 1} 次尝试失败: {str(e)[:100]}, "
                        f"{delay:.1f}秒后重试..."
                    )
                    time.sleep(delay)
                    delay *= 2
                else:
                    logger.error(f"Mem0 {operation_name} 在 {max_retries} 次尝试后仍失败: {str(e)}")

        raise last_exception

    def _get_all_raw(self, graph_id: str) -> tuple:
        """Fetch and parse get_all() for a graph → (results, relations)."""
        try:
            data = self.client.get_all(user_id=graph_id)
            return _parse_mem0_all(data)
        except Exception as e:
            logger.warning(f"Mem0 get_all 失败: {e}")
            return [], []

    def get_all_nodes(self, graph_id: str) -> List[Dict[str, Any]]:
        """
        获取图谱的所有节点（从关系中提取唯一实体名）

        Returns:
            节点列表（每个节点包含 uuid/name/labels/summary/attributes）
        """
        logger.info(f"获取图谱 {graph_id} 的所有节点...")

        _, relations = self._get_all_raw(graph_id)

        entity_names: Set[str] = set()
        for rel in relations:
            if isinstance(rel, dict):
                src = rel.get("source") or rel.get("source_node") or ""
                tgt = rel.get("target") or rel.get("destination") or rel.get("target_node") or ""
                if src:
                    entity_names.add(src)
                if tgt:
                    entity_names.add(tgt)

        nodes_data = [
            {
                "uuid": _name_to_uuid(name),
                "name": name,
                "labels": [],
                "summary": "",
                "attributes": {},
            }
            for name in entity_names
        ]

        logger.info(f"共获取 {len(nodes_data)} 个节点")
        return nodes_data

    def get_all_edges(self, graph_id: str) -> List[Dict[str, Any]]:
        """
        获取图谱的所有边（从 Mem0 relations 映射）

        Returns:
            边列表
        """
        logger.info(f"获取图谱 {graph_id} 的所有边...")

        _, relations = self._get_all_raw(graph_id)

        edges_data = []
        for rel in relations:
            if not isinstance(rel, dict):
                continue
            src = rel.get("source") or rel.get("source_node") or ""
            tgt = rel.get("target") or rel.get("destination") or rel.get("target_node") or ""
            rel_name = rel.get("relationship") or rel.get("name") or ""
            fact = rel.get("fact") or rel.get("description") or rel_name
            rel_id = rel.get("id") or _name_to_uuid(f"{src}-{rel_name}-{tgt}")

            edges_data.append({
                "uuid": str(rel_id),
                "name": rel_name,
                "fact": fact,
                "source_node_uuid": _name_to_uuid(src) if src else "",
                "target_node_uuid": _name_to_uuid(tgt) if tgt else "",
                "source_name": src,
                "target_name": tgt,
                "attributes": {},
            })

        logger.info(f"共获取 {len(edges_data)} 条边")
        return edges_data

    def get_node_edges(self, node_uuid: str, graph_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        获取指定节点的所有相关边（通过过滤 get_all_edges 实现）

        Args:
            node_uuid: 节点 UUID
            graph_id: 图谱 ID（当从 get_entity_with_context 调用时提供）

        Returns:
            边列表
        """
        if not graph_id:
            logger.warning("get_node_edges: 未提供 graph_id，无法获取边")
            return []

        try:
            all_edges = self.get_all_edges(graph_id)
            return [
                e for e in all_edges
                if e["source_node_uuid"] == node_uuid or e["target_node_uuid"] == node_uuid
            ]
        except Exception as e:
            logger.warning(f"获取节点 {node_uuid} 的边失败: {str(e)}")
            return []

    def filter_defined_entities(
        self,
        graph_id: str,
        defined_entity_types: Optional[List[str]] = None,
        enrich_with_edges: bool = True
    ) -> FilteredEntities:
        """
        筛选出符合预定义实体类型的节点。

        Mem0 没有类型标签，因此对每种实体类型执行语义搜索，
        将出现在搜索结果中的实体名称归类为该类型。

        Args:
            graph_id: 图谱 ID
            defined_entity_types: 预定义的实体类型列表（可选）
            enrich_with_edges: 是否获取每个实体的相关边信息

        Returns:
            FilteredEntities
        """
        logger.info(f"开始筛选图谱 {graph_id} 的实体...")

        all_edges = self.get_all_edges(graph_id)

        # 从边中提取所有唯一实体名
        entity_names: Set[str] = set()
        for edge in all_edges:
            if edge.get("source_name"):
                entity_names.add(edge["source_name"])
            if edge.get("target_name"):
                entity_names.add(edge["target_name"])

        total_count = len(entity_names)

        # 为每个实体名生成基础 node dict
        nodes_by_name: Dict[str, Dict] = {
            name: {
                "uuid": _name_to_uuid(name),
                "name": name,
                "labels": [],
                "summary": "",
                "attributes": {},
            }
            for name in entity_names
        }
        node_lookup = {n["uuid"]: n for n in nodes_by_name.values()}

        # ------------------------------------------------------------------
        # 确定每个实体的类型
        # ------------------------------------------------------------------
        entity_type_map: Dict[str, str] = {}  # entity_name → entity_type

        if defined_entity_types:
            for entity_type in defined_entity_types:
                try:
                    search_data = self.client.search(
                        entity_type,
                        user_id=graph_id,
                        limit=50
                    )
                    results, _ = _parse_mem0_all(search_data)

                    for mem in results:
                        mem_text = ""
                        if isinstance(mem, dict):
                            mem_text = (
                                mem.get("memory") or
                                mem.get("text") or
                                mem.get("content") or ""
                            )
                        elif isinstance(mem, str):
                            mem_text = mem

                        if not mem_text:
                            continue

                        for name in entity_names:
                            if name and name.lower() in mem_text.lower():
                                if name not in entity_type_map:
                                    entity_type_map[name] = entity_type

                except Exception as e:
                    logger.warning(f"搜索实体类型 {entity_type} 失败: {e}")

        # ------------------------------------------------------------------
        # 构建 EntityNode 列表
        # ------------------------------------------------------------------
        filtered_entities: List[EntityNode] = []
        entity_types_found: Set[str] = set()

        for name, node_data in nodes_by_name.items():
            entity_type = entity_type_map.get(name)

            if defined_entity_types and not entity_type:
                continue  # 没有匹配到任何预定义类型，跳过

            if not entity_type:
                # defined_entity_types 为 None 时：检查现有 labels
                custom_labels = [l for l in node_data.get("labels", []) if l not in ["Entity", "Node"]]
                if not custom_labels:
                    continue
                entity_type = custom_labels[0]

            entity_types_found.add(entity_type)

            entity = EntityNode(
                uuid=node_data["uuid"],
                name=name,
                labels=[entity_type, "Entity"],
                summary=node_data.get("summary", ""),
                attributes=node_data.get("attributes", {}),
            )

            if enrich_with_edges:
                related_edges = []
                related_node_uuids: Set[str] = set()

                for edge in all_edges:
                    if edge["source_node_uuid"] == entity.uuid:
                        related_edges.append({
                            "direction": "outgoing",
                            "edge_name": edge["name"],
                            "fact": edge["fact"],
                            "target_node_uuid": edge["target_node_uuid"],
                        })
                        related_node_uuids.add(edge["target_node_uuid"])
                    elif edge["target_node_uuid"] == entity.uuid:
                        related_edges.append({
                            "direction": "incoming",
                            "edge_name": edge["name"],
                            "fact": edge["fact"],
                            "source_node_uuid": edge["source_node_uuid"],
                        })
                        related_node_uuids.add(edge["source_node_uuid"])

                entity.related_edges = related_edges

                related_nodes = []
                for related_uuid in related_node_uuids:
                    if related_uuid in node_lookup:
                        rn = node_lookup[related_uuid]
                        related_nodes.append({
                            "uuid": rn["uuid"],
                            "name": rn["name"],
                            "labels": rn.get("labels", []),
                            "summary": rn.get("summary", ""),
                        })
                entity.related_nodes = related_nodes

            filtered_entities.append(entity)

        logger.info(
            f"筛选完成: 总节点 {total_count}, 符合条件 {len(filtered_entities)}, "
            f"实体类型: {entity_types_found}"
        )

        return FilteredEntities(
            entities=filtered_entities,
            entity_types=entity_types_found,
            total_count=total_count,
            filtered_count=len(filtered_entities),
        )

    def get_entity_with_context(
        self,
        graph_id: str,
        entity_uuid: str
    ) -> Optional[EntityNode]:
        """
        获取单个实体及其完整上下文（边和关联节点）

        Args:
            graph_id: 图谱 ID
            entity_uuid: 实体 UUID（确定性哈希，由实体名生成）

        Returns:
            EntityNode 或 None
        """
        try:
            all_edges = self.get_all_edges(graph_id)

            # 反查实体名（确定性 UUID 是从名称生成的）
            entity_name: Optional[str] = None
            for edge in all_edges:
                if edge.get("source_node_uuid") == entity_uuid and edge.get("source_name"):
                    entity_name = edge["source_name"]
                    break
                if edge.get("target_node_uuid") == entity_uuid and edge.get("target_name"):
                    entity_name = edge["target_name"]
                    break

            if not entity_name:
                return None

            # 搜索该实体相关的记忆
            summary = ""
            try:
                search_data = self.client.search(entity_name, user_id=graph_id, limit=10)
                results, _ = _parse_mem0_all(search_data)
                if results:
                    first = results[0]
                    if isinstance(first, dict):
                        summary = first.get("memory") or first.get("text") or ""
            except Exception:
                pass

            # 获取相关边和节点
            edges = [
                e for e in all_edges
                if e["source_node_uuid"] == entity_uuid or e["target_node_uuid"] == entity_uuid
            ]

            all_nodes = self.get_all_nodes(graph_id)
            node_map = {n["uuid"]: n for n in all_nodes}

            related_edges = []
            related_node_uuids: Set[str] = set()

            for edge in edges:
                if edge["source_node_uuid"] == entity_uuid:
                    related_edges.append({
                        "direction": "outgoing",
                        "edge_name": edge["name"],
                        "fact": edge["fact"],
                        "target_node_uuid": edge["target_node_uuid"],
                    })
                    related_node_uuids.add(edge["target_node_uuid"])
                else:
                    related_edges.append({
                        "direction": "incoming",
                        "edge_name": edge["name"],
                        "fact": edge["fact"],
                        "source_node_uuid": edge["source_node_uuid"],
                    })
                    related_node_uuids.add(edge["source_node_uuid"])

            related_nodes = []
            for related_uuid in related_node_uuids:
                if related_uuid in node_map:
                    rn = node_map[related_uuid]
                    related_nodes.append({
                        "uuid": rn["uuid"],
                        "name": rn["name"],
                        "labels": rn.get("labels", []),
                        "summary": rn.get("summary", ""),
                    })

            return EntityNode(
                uuid=entity_uuid,
                name=entity_name,
                labels=["Entity"],
                summary=summary,
                attributes={},
                related_edges=related_edges,
                related_nodes=related_nodes,
            )

        except Exception as e:
            logger.error(f"获取实体 {entity_uuid} 失败: {str(e)}")
            return None

    def get_entities_by_type(
        self,
        graph_id: str,
        entity_type: str,
        enrich_with_edges: bool = True
    ) -> List[EntityNode]:
        """
        获取指定类型的所有实体

        Args:
            graph_id: 图谱 ID
            entity_type: 实体类型（如 "Student", "PublicFigure" 等）
            enrich_with_edges: 是否获取相关边信息

        Returns:
            实体列表
        """
        result = self.filter_defined_entities(
            graph_id=graph_id,
            defined_entity_types=[entity_type],
            enrich_with_edges=enrich_with_edges
        )
        return result.entities
