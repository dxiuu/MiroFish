"""
图谱构建服务 (Mem0)
接口2：使用 Mem0 Graph Memory 构建知识图谱
"""

import uuid
import time
import hashlib
import threading
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass

from ..config import Config
from ..models.task import TaskManager, TaskStatus
from ..utils.mem0_client import get_mem0_client
from .text_processor import TextProcessor


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _name_to_uuid(name: str) -> str:
    """Generate a deterministic UUID from an entity name (MD5-based)."""
    return str(uuid.UUID(hashlib.md5(name.encode()).hexdigest()))


def _parse_mem0_all(data) -> tuple:
    """Parse Mem0 get_all() / search() response → (results, relations)."""
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
class GraphInfo:
    """图谱信息"""
    graph_id: str
    node_count: int
    edge_count: int
    entity_types: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "entity_types": self.entity_types,
        }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class GraphBuilderService:
    """
    图谱构建服务
    负责调用 Mem0 API 构建知识图谱
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or Config.MEM0_API_KEY
        if not self.api_key:
            raise ValueError("MEM0_KEY 未配置")

        self.client = get_mem0_client()
        self.task_manager = TaskManager()

    def build_graph_async(
        self,
        text: str,
        ontology: Dict[str, Any],
        graph_name: str = "MiroFish Graph",
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        batch_size: int = 3
    ) -> str:
        """
        异步构建图谱

        Returns:
            任务ID
        """
        task_id = self.task_manager.create_task(
            task_type="graph_build",
            metadata={
                "graph_name": graph_name,
                "chunk_size": chunk_size,
                "text_length": len(text),
            }
        )

        thread = threading.Thread(
            target=self._build_graph_worker,
            args=(task_id, text, ontology, graph_name, chunk_size, chunk_overlap, batch_size)
        )
        thread.daemon = True
        thread.start()

        return task_id

    def _build_graph_worker(
        self,
        task_id: str,
        text: str,
        ontology: Dict[str, Any],
        graph_name: str,
        chunk_size: int,
        chunk_overlap: int,
        batch_size: int
    ):
        """图谱构建工作线程"""
        try:
            self.task_manager.update_task(
                task_id,
                status=TaskStatus.PROCESSING,
                progress=5,
                message="开始构建图谱..."
            )

            # 1. 创建图谱（Mem0 隐式创建，这里仅生成 ID）
            graph_id = self.create_graph(graph_name)
            self.task_manager.update_task(
                task_id,
                progress=10,
                message=f"图谱已创建: {graph_id}"
            )

            # 2. 设置本体
            self.set_ontology(graph_id, ontology)
            self.task_manager.update_task(
                task_id,
                progress=15,
                message="本体已设置"
            )

            # 3. 文本分块
            chunks = TextProcessor.split_text(text, chunk_size, chunk_overlap)
            total_chunks = len(chunks)
            self.task_manager.update_task(
                task_id,
                progress=20,
                message=f"文本已分割为 {total_chunks} 个块"
            )

            # 4. 分批发送数据（20% → 90%）
            self.add_text_batches(
                graph_id, chunks, batch_size,
                lambda msg, prog: self.task_manager.update_task(
                    task_id,
                    progress=20 + int(prog * 0.7),
                    message=msg
                )
            )

            # 5. 获取图谱信息
            self.task_manager.update_task(
                task_id,
                progress=90,
                message="获取图谱信息..."
            )

            graph_info = self._get_graph_info(graph_id)

            # 完成
            self.task_manager.complete_task(task_id, {
                "graph_id": graph_id,
                "graph_info": graph_info.to_dict(),
                "chunks_processed": total_chunks,
            })

        except Exception as e:
            import traceback
            error_msg = f"{str(e)}\n{traceback.format_exc()}"
            self.task_manager.fail_task(task_id, error_msg)

    def create_graph(self, name: str) -> str:
        """
        生成图谱 ID（Mem0 在首次 add() 时隐式创建图谱，无需显式调用）
        """
        graph_id = f"mirofish_{uuid.uuid4().hex[:16]}"
        return graph_id

    def set_ontology(self, graph_id: str, ontology: Dict[str, Any]):
        """
        将本体摘要作为首条记忆存入 Mem0，供后续实体类型检索使用
        """
        entity_names = [e["name"] for e in ontology.get("entity_types", [])]
        edge_names = [e["name"] for e in ontology.get("edge_types", [])]

        ontology_text = (
            f"图谱本体定义 - 实体类型: {', '.join(entity_names)}; "
            f"边类型: {', '.join(edge_names)}"
        )

        try:
            self.client.add(
                messages=[{"role": "user", "content": ontology_text}],
                user_id=graph_id,
                metadata={"type": "ontology", "source": "ontology_definition"}
            )
        except Exception:
            pass  # 本体存储失败不阻断流程

    def add_text_batches(
        self,
        graph_id: str,
        chunks: List[str],
        batch_size: int = 3,
        progress_callback: Optional[Callable] = None
    ) -> List[str]:
        """
        分批添加文本到 Mem0。
        Mem0 同步处理，不返回 episode UUID，返回空列表。
        """
        total_chunks = len(chunks)

        for i in range(0, total_chunks, batch_size):
            batch_chunks = chunks[i:i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (total_chunks + batch_size - 1) // batch_size

            if progress_callback:
                progress = (i + len(batch_chunks)) / total_chunks
                progress_callback(
                    f"发送第 {batch_num}/{total_batches} 批数据 ({len(batch_chunks)} 块)...",
                    progress
                )

            for chunk in batch_chunks:
                try:
                    self.client.add(
                        messages=[{"role": "user", "content": chunk}],
                        user_id=graph_id,
                        metadata={"source": "document"}
                    )
                except Exception as e:
                    if progress_callback:
                        progress_callback(f"块发送失败: {str(e)}", 0)
                    raise

        return []

    def _get_graph_info(self, graph_id: str) -> GraphInfo:
        """获取图谱信息"""
        try:
            data = self.client.get_all(user_id=graph_id)
            _, relations = _parse_mem0_all(data)
        except Exception:
            relations = []

        # 从 relations 的 source/target 提取唯一实体名
        entity_names: set = set()
        for rel in relations:
            if isinstance(rel, dict):
                src = rel.get("source") or rel.get("source_node") or ""
                tgt = rel.get("target") or rel.get("destination") or rel.get("target_node") or ""
                if src:
                    entity_names.add(src)
                if tgt:
                    entity_names.add(tgt)

        return GraphInfo(
            graph_id=graph_id,
            node_count=len(entity_names),
            edge_count=len(relations),
            entity_types=[]
        )

    def get_graph_data(self, graph_id: str) -> Dict[str, Any]:
        """
        获取完整图谱数据（包含详细信息）
        """
        try:
            data = self.client.get_all(user_id=graph_id)
            _, relations = _parse_mem0_all(data)
        except Exception:
            relations = []

        # 从 relations 提取唯一实体名 → nodes
        entity_names: set = set()
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
                "created_at": None,
            }
            for name in entity_names
        ]

        name_to_uuid_map = {name: _name_to_uuid(name) for name in entity_names}

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
                "fact_type": rel_name,
                "source_node_uuid": name_to_uuid_map.get(src, _name_to_uuid(src)),
                "target_node_uuid": name_to_uuid_map.get(tgt, _name_to_uuid(tgt)),
                "source_node_name": src,
                "target_node_name": tgt,
                "attributes": {},
                "created_at": None,
                "valid_at": None,
                "invalid_at": None,
                "expired_at": None,
                "episodes": [],
            })

        return {
            "graph_id": graph_id,
            "nodes": nodes_data,
            "edges": edges_data,
            "node_count": len(nodes_data),
            "edge_count": len(edges_data),
        }

    def delete_graph(self, graph_id: str):
        """删除图谱（删除该 user_id 下的所有记忆）"""
        self.client.delete_all(user_id=graph_id)
