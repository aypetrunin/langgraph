# src/mcp/registry.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Awaitable, Any, Optional

from langchain_core.tools.structured import StructuredTool

# Названия backend — лучше смысловые (не “5007”), но пока можно так:
MCP_BACKEND_CLASSIC = "classic"
MCP_BACKEND_LIST_5007 = "list_5007"
MCP_BACKEND_ALENA_5020 = "alena_5020"


# временно: определяем backend по порту
# (позже заменим на settings: MCP_BACKEND_SOFIA=..., etc.)
def resolve_backend_name(mcp_port: int | None) -> str:
    if mcp_port in {15020, 5020}:
        return MCP_BACKEND_ALENA_5020
    if mcp_port in {15007, 5007}:
        return MCP_BACKEND_LIST_5007
    return MCP_BACKEND_CLASSIC


@dataclass(frozen=True)
class MCPBackend:
    name: str

    # выбор инструментов для LLM
    def allowed_tool_names(self, *, dialog_state: str, data: dict) -> set[str]:
        raise NotImplementedError

    # выбор модели
    async def select_model(self, *, dialog_state: str, data: dict):
        raise NotImplementedError

    # postprocessors для tool результатов
    def tool_postprocessors(self) -> dict[str, Callable[..., Awaitable[Any]]]:
        raise NotImplementedError
