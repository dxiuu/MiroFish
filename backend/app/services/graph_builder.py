"""
图谱构建服务
接口2：使用Zep API构建Standalone Graph
"""

import os
import uuid
import time
import threading
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass

from ..utils.graph_client import make_graph_client
from ..config import Config
from ..models.task import TaskManager, TaskStatus
from .text_processor import TextProcessor


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


class GraphBuilderService:
    """
    图谱构建服务
    负责调用Zep API构建知识图谱
    """
    
    def __init__(self, api_key: Optional[str] = None):
        self.client = make_graph_client()
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
        
        Args:
            text: 输入文本
            ontology: 本体定义（来自接口1的输出）
            graph_name: 图谱名称
            chunk_size: 文本块大小
            chunk_overlap: 块重叠大小
            batch_size: 每批发送的块数量
            
        Returns:
            任务ID
        """
        # 创建任务
        task_id = self.task_manager.create_task(
            task_type="graph_build",
            metadata={
                "graph_name": graph_name,
                "chunk_size": chunk_size,
                "text_length": len(text),
            }
        )
        
        # 在后台线程中执行构建
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
            
            # 1. 创建图谱
            graph_id = self.create_graph(graph_name)
            self.task_manager.update_task(
                task_id,
                progress=10,
                message=f"图谱已创建: {graph_id}"
            )
            
            # 2. 加载本体实体类型到图谱客户端
            if hasattr(self.client, 'load_ontology'):
                self.client.load_ontology(ontology)
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
            
            # 4. 分批发送数据
            episode_uuids = self.add_text_batches(
                graph_id, chunks, batch_size,
                lambda msg, prog: self.task_manager.update_task(
                    task_id,
                    progress=20 + int(prog * 0.4),  # 20-60%
                    message=msg
                )
            )
            
            # 5. 处理完成（Graphiti同步完成，无需等待）
            self.task_manager.update_task(
                task_id,
                progress=60,
                message="数据已处理"
            )
            
            # 6. 获取图谱信息
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
        """创建Zep图谱（公开方法）"""
        graph_id = f"mirofish_{uuid.uuid4().hex[:16]}"
        
        # Graphiti creates graphs implicitly on first episode add; no explicit creation needed.
        # For Zep backend, graph creation happens via the adapter.
        if hasattr(self.client, 'graph'):
            self.client.graph.create(
                graph_id=graph_id,
                name=name,
                description="MiroFish Social Simulation Graph"
            )
        
        return graph_id
    
    def set_ontology(self, graph_id: str, ontology: Dict[str, Any]):
        """将本体实体类型加载到图谱客户端（用于Graphiti entity_types）"""
        if hasattr(self.client, 'load_ontology'):
            self.client.load_ontology(ontology)
    
    def add_text_batches(
        self,
        graph_id: str,
        chunks: List[str],
        batch_size: int = 3,
        progress_callback: Optional[Callable] = None
    ) -> List[str]:
        """分批添加文本到图谱，返回所有 episode 的 uuid 列表"""
        episode_uuids = []
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

            # 构建episode数据
            episodes = [
                {"data": chunk, "name": f"chunk_{i+j}"}
                for j, chunk in enumerate(batch_chunks)
            ]

            # 发送到图谱客户端
            try:
                batch_result = self.client.add_episodes_batch(
                    graph_id=graph_id,
                    episodes=episodes
                )

                # 收集返回的 episode uuid
                if batch_result and isinstance(batch_result, list):
                    for result in batch_result:
                        ep_uuid = result.get('uuid', '')
                        if ep_uuid:
                            episode_uuids.append(ep_uuid)

                # 避免请求过快
                time.sleep(1)

            except Exception as e:
                if progress_callback:
                    progress_callback(f"批次 {batch_num} 发送失败: {str(e)}", 0)
                raise

        return episode_uuids
    
    def _wait_for_episodes(
        self,
        episode_uuids: List[str],
        progress_callback: Optional[Callable] = None,
        timeout: int = 600
    ):
        """等待所有 episode 处理完成（已弃用 - Graphiti同步完成）"""
        # Graphiti的add_episode和add_episode_bulk同步完成，无需等待
        if progress_callback:
            progress_callback("处理完成", 1.0)
    
    def _get_graph_info(self, graph_id: str) -> GraphInfo:
        """获取图谱信息"""
        # 获取节点
        nodes = self.client.get_all_nodes(graph_id)

        # 获取边
        edges = self.client.get_all_edges(graph_id)

        # 统计实体类型
        entity_types = set()
        for node in nodes:
            if node.get('labels'):
                for label in node['labels']:
                    if label not in ["Entity", "Node", "EntityNode"]:
                        entity_types.add(label)

        return GraphInfo(
            graph_id=graph_id,
            node_count=len(nodes),
            edge_count=len(edges),
            entity_types=list(entity_types)
        )
    
    def get_graph_data(self, graph_id: str) -> Dict[str, Any]:
        """
        获取完整图谱数据（包含详细信息）

        Args:
            graph_id: 图谱ID

        Returns:
            包含nodes和edges的字典，包括时间信息、属性等详细数据
        """
        nodes = self.client.get_all_nodes(graph_id)
        edges = self.client.get_all_edges(graph_id)

        # 创建节点映射用于获取节点名称
        node_map = {}
        for node in nodes:
            node_map[node.get("uuid", "")] = node.get("name", "")

        nodes_data = []
        for node in nodes:
            nodes_data.append({
                "uuid": node.get("uuid", ""),
                "name": node.get("name", ""),
                "labels": node.get("labels", []),
                "summary": node.get("summary", ""),
                "attributes": node.get("attributes", {}),
                "created_at": None,
            })

        edges_data = []
        for edge in edges:
            edges_data.append({
                "uuid": edge.get("uuid", ""),
                "name": edge.get("name", ""),
                "fact": edge.get("fact", ""),
                "fact_type": edge.get("name", ""),
                "source_node_uuid": edge.get("source_node_uuid", ""),
                "target_node_uuid": edge.get("target_node_uuid", ""),
                "source_node_name": node_map.get(edge.get("source_node_uuid", ""), ""),
                "target_node_name": node_map.get(edge.get("target_node_uuid", ""), ""),
                "attributes": edge.get("attributes", {}),
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
        """删除图谱"""
        if hasattr(self.client, 'graph'):
            self.client.graph.delete(graph_id=graph_id)
        else:
            self.client.delete_graph(graph_id)

