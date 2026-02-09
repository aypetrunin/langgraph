# src/zena_create_agent.py
"""
Модуль описывающий ноды графа.

Версия с безопасной загрузкой MCP tools:
- таймаут на get_tools()
- retry + backoff + jitter
- fallback на пустой список tools (граф не падает, просто без инструментов)
- MCP host/scheme/таймауты конфигурируются через env

Важно про модели:
- LLM модели больше НЕ экспортируются из zena_common
- берём модели через zena_models.get_models()
- init_models() вызывается внутри init_resources() (см. zena_resources.py),
  поэтому create_agent_mcp() предполагает, что init_resources() уже был вызван
  (в zena_create_graph это так).
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from time import perf_counter
from typing import Optional

from langchain.agents import create_agent
from langchain_core.tools.base import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.graph.state import CompiledStateGraph

from langchain.agents.middleware import (
    # ClearToolUsesEdit,
    # ToolRetryMiddleware,
    # SummarizationMiddleware,
    # ContextEditingMiddleware,
    # LLMToolSelectorMiddleware,
    ModelFallbackMiddleware,
    # PIIMiddleware,
    ToolCallLimitMiddleware,
)

from .zena_models import get_models
from .zena_state import State, Context

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

logger = logging.getLogger(__name__)

# ---------------------------
# MCP settings (через env)
# ---------------------------

MCP_HOST = os.getenv("MCP_HOST", "172.17.0.1")
MCP_SCHEME = os.getenv("MCP_SCHEME", "http")

# Таймаут на получение tools (сек)
MCP_TOOLS_TIMEOUT_S = float(os.getenv("MCP_TOOLS_TIMEOUT_S", "3.0"))

# Кол-во повторов: 0 = без повторов, 2 = 3 попытки всего
MCP_RETRIES = int(os.getenv("MCP_RETRIES", "2"))

# Базовый backoff в секундах
MCP_BACKOFF_BASE_S = float(os.getenv("MCP_BACKOFF_BASE_S", "0.3"))

# Если нужно — можно добавить отдельный флаг, чтобы в прод падать, а не фолбечить:
MCP_FAIL_HARD = os.getenv("MCP_FAIL_HARD", "0") == "1"


async def _get_tools_safe(mcp_port: int) -> list[BaseTool]:
    """
    Получение инструментов из MCP-сервера по выбранному порту.
    Логирует:
      - время загрузки tools
      - fallback=true/false
      - количество tools
    """
    url = f"{MCP_SCHEME}://{MCP_HOST}:{mcp_port}/sse"
    started_at = time.monotonic()

    async def attempt() -> list[BaseTool]:
        client = MultiServerMCPClient(
            {
                "company": {
                    "transport": "sse",
                    "url": url,
                }
            }
        )
        try:
            return await asyncio.wait_for(
                client.get_tools(),
                timeout=MCP_TOOLS_TIMEOUT_S,
            )
        finally:
            # best-effort close, если клиент поддерживает
            aclose = getattr(client, "aclose", None)
            if callable(aclose):
                try:
                    await aclose()
                except Exception:
                    logger.debug("event=mcp_client_close_failed port=%s", mcp_port, exc_info=True)

    last_exc: Optional[BaseException] = None

    for i in range(MCP_RETRIES + 1):
        try:
            tools = await attempt()
            duration_ms = int((time.monotonic() - started_at) * 1000)
            logger.info(
                "event=mcp_tools_load port=%s fallback=false tools_count=%s duration_ms=%s",
                mcp_port,
                len(tools),
                duration_ms,
            )
            return tools

        except Exception as e:
            last_exc = e

            if MCP_FAIL_HARD:
                raise

            if i >= MCP_RETRIES:
                break

            sleep_s = MCP_BACKOFF_BASE_S * (2**i) + random.random() * MCP_BACKOFF_BASE_S
            logger.warning(
                "event=mcp_tools_retry port=%s attempt=%s/%s sleep_s=%.2f error=%r",
                mcp_port,
                i + 1,
                MCP_RETRIES + 1,
                sleep_s,
                e,
            )
            await asyncio.sleep(sleep_s)

    # fallback
    duration_ms = int((time.monotonic() - started_at) * 1000)
    logger.error(
        "event=mcp_tools_load port=%s fallback=true tools_count=0 duration_ms=%s error=%r",
        mcp_port,
        duration_ms,
        last_exc,
    )
    return []


async def create_agent_mcp(mcp_port: int) -> CompiledStateGraph:
    """
    Создаёт агента (ноду графа) для конкретной компании/порта MCP.

    Важно:
    - функция async (никаких asyncio.run внутри)
    - предполагается, что init_resources() уже был вызван ДО неё
      (в zena_create_graph.create_agent_graph это так).
    """
    t0 = perf_counter()

    # 1) tools
    t_tools0 = perf_counter()
    tools = await _get_tools_safe(mcp_port)
    tools_s = perf_counter() - t_tools0

    # 2) models
    # Если init_models() не был вызван, get_models() бросит RuntimeError — это хорошо,
    # потому что ошибка будет явной (обычно означает, что забыли await init_resources()).
    models = get_models()
    model_4o = models.model_4o
    model_4o_mini = models.model_4o_mini
    model_4o_mini_reserv = models.model_4o_mini_reserv

    # 3) agent creation
    t_agent0 = perf_counter()
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
            # GetCRMGOMiddleware(),
            # DynamicMCPPortMiddleware(),

            DynamicSystemPrompt(),
            ToolSelectorMiddleware(),

            # SaveResultToolsMiddleware(),
            # TrimMessages(),

            ToolMonitoringMiddleware(),
            GetCountToken(),
            GetToolArgs(),
            GetCRMGOOnboardStage(),
            ResetData(),
            SaveResponceAgent(),

            # --- optional middlewares ---
            ModelFallbackMiddleware(
                model_4o_mini,
                model_4o_mini_reserv,
            ),
            ToolCallLimitMiddleware(run_limit=10),
        ],
    )
    agent_s = perf_counter() - t_agent0
    total_s = perf_counter() - t0

    logger.info(
        "event=agent_create_done port=%s tools_count=%s tools_s=%.3f agent_s=%.3f total_s=%.3f",
        mcp_port,
        len(tools),
        tools_s,
        agent_s,
        total_s,
    )

    return agent


# ---------------------------
# Локальная диагностика (по желанию)
# ---------------------------

async def _debug_list_tools(ports: list[int]) -> None:
    for port in ports:
        tools = await _get_tools_safe(port)
        logger.info("[debug] tools(%s)=%s", port, [t.name for t in tools])


if __name__ == "__main__":
    # Пример локального запуска для проверки MCP
    # cd /home/.../langgraph
    # uv run python -m src.zena_create_agent
    asyncio.run(_debug_list_tools([5020]))



# cd /home/copilot_superuser/petrunin/zena/langgraph
# uv run python -m src.zena_create_agent