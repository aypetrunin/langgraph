"""Фабрика агентов с MCP-инструментами и middleware-стеком.

Создаёт агента для конкретной компании (определяется по MCP-порту):
1. Подключается к MCP-серверу по SSE и получает доступные инструменты.
2. Собирает middleware-стек: валидация входа, загрузка данных из БД,
   динамический системный промпт, фильтрация инструментов, мониторинг,
   подсчёт токенов, fallback на резервную модель и т.д.
3. Возвращает скомпилированный граф агента.

Каждая компания (Sofia, Anisa, Alena и др.) получает свой экземпляр
агента с отдельным набором MCP-инструментов.
"""

import os

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ClearToolUsesEdit,
    ContextEditingMiddleware,
    ModelFallbackMiddleware,
    ToolCallLimitMiddleware,
)
from langchain_core.tools.base import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.graph.state import CompiledStateGraph

from .zena_common import model_4o_mini, model_4o_mini_reserv
from .zena_logging import get_logger
from .zena_middleware_after_agent import ResetData
from .zena_middleware_after_model import (
    GetCountToken,
    GetCRMGOOnboardStage,
    GetToolArgs,
)
from .zena_middleware_before_agent import (
    GetDatabaseMiddleware,
    GetKeyWordMiddleware,
    VerifyInputMessage,
)
from .zena_middleware_wrap_model import (
    DynamicSystemPrompt,
    ToolSelectorMiddleware,
)
from .zena_middleware_wrap_tool import ToolMonitoringMiddleware
from .zena_state import Context, State

logger = get_logger()


async def create_agent_mcp(mcp_port: int) -> CompiledStateGraph:
    """Создаёт агента с MCP-инструментами для указанного порта."""

    async def _get_tools(mcp_port: int) -> list[BaseTool]:
        """Получение инструментов из MCP-сервера по выбранному порту."""
        mcp_host = os.getenv("MCP_HOST", "172.17.0.1")
        url = f"http://{mcp_host}:{mcp_port}/sse"
        client = MultiServerMCPClient(
            {
                "company": {
                    "transport": "sse",
                    "url": url,
                }
            }
        )
        return await client.get_tools()

    tools = await _get_tools(mcp_port)
    
    tools_name = [tool.name for tool in tools]
    logger.info("agent.tools_loaded", mcp_port=mcp_port, tools=tools_name)
    
    # Middleware-стек выполняется в порядке добавления:
    # 1. before_agent: VerifyInputMessage → GetDatabaseMiddleware → GetKeyWordMiddleware
    # 2. wrap_model:   DynamicSystemPrompt → ToolSelectorMiddleware
    # 3. wrap_tool:    ToolMonitoringMiddleware
    # 4. after_model:  GetCountToken → GetToolArgs → GetCRMGOOnboardStage
    # 5. after_agent:  ResetData
    # 6. built-in:     ContextEditingMiddleware → ModelFallbackMiddleware → ToolCallLimitMiddleware
    agent = create_agent(
        model=model_4o_mini,
        state_schema=State,
        context_schema=Context,
        system_prompt="Ты полезный помощник",
        tools=tools,
        middleware=[
            VerifyInputMessage(),
            GetDatabaseMiddleware(),
            GetKeyWordMiddleware(),
            DynamicSystemPrompt(),
            ToolSelectorMiddleware(),
            ToolMonitoringMiddleware(),
            GetCountToken(),
            GetToolArgs(),
            GetCRMGOOnboardStage(),
            ResetData(),
            ContextEditingMiddleware(
                edits=[
                    ClearToolUsesEdit(
                        trigger=2000,
                        keep=2,
                        clear_tool_inputs=False,
                        exclude_tools=[],
                        placeholder="[cleared]",
                    ),
                ],
            ),
            ModelFallbackMiddleware(
                model_4o_mini,
                model_4o_mini_reserv,
            ),
            ToolCallLimitMiddleware(
                run_limit=20,
            ),
        ],
    )
    return agent