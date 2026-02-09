# src/zena_redialog_graph.py
"""
Создание графа агента реаниматора диалога (Redialog).

Важно:
- граф НЕ создаётся на import-time
- возвращаем скомпилированный граф через async-фабрику,
  чтобы LangGraph runtime мог корректно загрузить его при старте

Дополнительно (единый паттерн архитектуры):
- кеширование графа на процесс через GraphRegistry
- TTL/force_reload берём из runtime.settings
- логи кеша — через runtime.logging.get_graph_cache_logger()
"""

from __future__ import annotations

from langgraph.graph.state import CompiledStateGraph

from .graphs.registry import GraphRegistry
from .graphs.redialog_factory import create_redialog_graph
from .runtime.logging import get_graph_cache_logger
from .runtime.settings import get_settings


logger = get_graph_cache_logger()
_registry: GraphRegistry[CompiledStateGraph] = GraphRegistry()


async def graph_agent_redialog() -> CompiledStateGraph:
    s = get_settings()
    # ключ фиксированный, т.к. это отдельный тип графа
    return await _registry.aget_or_create(
        "redialog",
        lambda: create_redialog_graph(),
        ttl_s=s.graph_cache_ttl_s,
        force_reload=s.graph_cache_force_reload,
        logger=logger,
        port=None,  # нет MCP порта
    )

