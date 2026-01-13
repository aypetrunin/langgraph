"""Модуль описывающий ноды графа."""

import httpx
import re

from langchain.agents import create_agent
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_core.tools.base import BaseTool
from langgraph.graph.state import CompiledStateGraph

from langchain.agents.middleware import (
    ClearToolUsesEdit,
    ToolRetryMiddleware,
    SummarizationMiddleware,
    ContextEditingMiddleware,
    LLMToolSelectorMiddleware,
    ModelFallbackMiddleware,
    PIIMiddleware,
)


from .zena_common import logger, model_4o, model_4o_mini, model_4o_mini_reserv
from .zena_state import State, Context
# from .zena_memory import memory

from .zena_middleware_before_agent import (
    VerifyInputMessage,
    GetDatabaseMiddleware,
    GetCRMGOMiddleware,
    GetKeyWordMiddleware,
    # DynamicMCPPortMiddleware,
)
from .zena_middleware_wrap_model import (
    DynamicSystemPrompt,
    ToolSelectorMiddleware,
)
from .zena_middleware_wrap_tool import (
    ToolMonitoringMiddleware,
)
from .zena_middleware_after_agent import (
    SaveResponceAgent,
    ResetData,
)
from .zena_middleware_before_model import (
    # SaveResultToolsMiddleware,
    TrimMessages,
)
from .zena_middleware_after_model import (
    GetCountToken,
    GetToolArgs,
    GetCRMGOOnboardStage,
)

from .zena_state import State


async def create_agent_mcp(mcp_port: int) -> CompiledStateGraph:

    async def _get_tools(mcp_port: int) -> list[BaseTool]:
        """Получение инструментов из MCP-сервера по выбранному порту."""

        url = f"http://172.17.0.1:{mcp_port}/sse"
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
    logger.info(f"create_agent_mcp tools ({mcp_port}): {tools_name}")
    
    agent = create_agent( 
        model=model_4o_mini,
        state_schema=State,
        context_schema=Context,
        system_prompt='Ты полезный помошник',
        tools=tools,
        middleware=[
            VerifyInputMessage(),
            GetDatabaseMiddleware(),
            GetKeyWordMiddleware(),
            # GetCRMGOMiddleware(),
            # DynamicMCPPortMiddleware(),
            DynamicSystemPrompt(),
            # personalized_prompt,
            ToolSelectorMiddleware(),
            # SaveResultToolsMiddleware(),
            # TrimMessages(),
            ToolMonitoringMiddleware(),
            GetCountToken(),
            GetToolArgs(),
            GetCRMGOOnboardStage(),
            ResetData(),
            SaveResponceAgent(),
            
            # ContextEditingMiddleware(
            #     edits=[
            #         ClearToolUsesEdit(
            #             trigger=2000,
            #             keep=2,
            #             clear_tool_inputs=True,
            #             exclude_tools=[],
            #             placeholder="[cleared]",
            #         ),
            #     ],
            # ),
            ModelFallbackMiddleware(
                model_4o_mini,
                model_4o_mini_reserv,
            ),
            # PIIMiddleware(
            #     "phone",
            #     detector = re.compile(r'^(\+?7|8)?[\s.-]?(\(?\d{3,5}\)?)?[\s.-]?\d{3}[\s.-]?\d{2}[\s.-]?\d{2}$'),
            #     strategy="mask",
            # ),
            # PIIMiddleware(
            #     "email",
            #     strategy="mask",
            # ),

            # ToolRetryMiddleware(
            #     max_retries=1,
            #     backoff_factor=2.0,
            #     initial_delay=1.0,
            #     on_failure='return_message'
            # ),

            # SummarizationMiddleware(
            #     model=model_4o_mini,
            #     max_tokens_before_summary=4000,
            #     messages_to_keep=10,
            # ),

            # LLMToolSelectorMiddleware(
            #     model=model_4o_mini,
            #     max_tools=2,
            # ),
        ]
    )
    return agent

import asyncio
async def main():
    async def _get_tools(mcp_port: int) -> list[BaseTool]:
        """Получение инструментов из MCP-сервера по выбранному порту."""

        url = f"http://172.17.0.1:{mcp_port}/sse"
        client = MultiServerMCPClient(
            {
                "company": {
                    "transport": "sse",
                    "url": url,
                }
            }
        )
        return await client.get_tools()

    mcp_port = [5020]
    for port in mcp_port:
        tools = await _get_tools(port)

        tools_name = [tool.name for tool in tools]
        logger.info(f"create_agent_mcp tools ({port}): {tools_name}")


if __name__ == "__main__":
    asyncio.run(main())


# cd /home/copilot_superuser/petrunin/zena/langgraph
# uv run python -m src.zena_create_agent