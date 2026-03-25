"""
Zep 客户端工厂。

根据环境变量选择连接目标：
- ZEP_API_URL 设置时 → 自托管 Zep 实例（如 http://localhost:8000）
- 仅 ZEP_API_KEY 时  → Zep Cloud（托管服务）

用法：
    from ..utils.zep_client import make_zep_client
    client = make_zep_client()
"""

from __future__ import annotations

from zep_cloud.client import Zep

from ..config import Config


def make_zep_client(
    api_key: str | None = None,
    api_url: str | None = None,
) -> Zep:
    """
    返回已配置的 Zep 客户端。

    Args:
        api_key: 覆盖 Config.ZEP_API_KEY（可选）
        api_url: 覆盖 Config.ZEP_API_URL（可选）

    Returns:
        Zep 客户端实例

    Raises:
        ValueError: 既未配置 ZEP_API_URL 也未配置 ZEP_API_KEY
    """
    key = api_key or Config.ZEP_API_KEY
    url = api_url or Config.ZEP_API_URL

    if url:
        # 自托管模式：api_url 必填，api_key 可为任意字符串（或空）
        return Zep(api_url=url, api_key=key or "local")

    if not key:
        raise ValueError("ZEP_API_KEY 或 ZEP_API_URL 未配置")

    # Zep Cloud 模式
    return Zep(api_key=key)
