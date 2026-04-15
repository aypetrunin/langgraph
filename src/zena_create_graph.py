"""Создание и экспорт графов агентов для каждой компании.

Этот модуль — точка входа для LangGraph CLI (langgraph.json).
Создаёт 8 агентских графов (по одному на компанию) параллельно при старте
через asyncio.gather(), что ускоряет запуск сервиса в ~6 раз.

Каждый граф имеет простую структуру: START → agent → END.
Порт MCP-сервера берётся из переменных окружения (MCP_PORT_*).

Экспортируемые графы:
- graph_sofia, graph_anisa, graph_annitta, graph_anastasia
- graph_alena, graph_valentina, graph_marina, graph_egoistka
"""

import asyncio
import os

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from .zena_create_agent import create_agent_mcp
from .zena_state import Context, InputState, OutputState, State


async def create_agent_graph(port: int) -> CompiledStateGraph:
    """Создаёт и компилирует граф агента для указанного MCP-порта."""
    agent = await create_agent_mcp(mcp_port=port)
    
    workflow = StateGraph(
        state_schema=State,
        input_schema=InputState,
        output_schema=OutputState,
        context_schema=Context,
    )
    workflow.add_node("agent", agent)
    workflow.add_edge(START, "agent")
    workflow.add_edge("agent", END)
    
    return workflow.compile()


# -------------------- MCP-порты для каждой компании --------------------
# Значения берутся из deploy/dev.env или deploy/prod.env.
# dev-порты: 5xxx, prod-порты: 15xxx.
MCP_PORT_SOFIA = os.getenv("MCP_PORT_SOFIA")          # 5002 / 15002
MCP_PORT_ANISA = os.getenv("MCP_PORT_ANISA")           # 5005 / 15005
MCP_PORT_ANNITTA = os.getenv("MCP_PORT_ANNITTA")       # 5006 / 15006
MCP_PORT_ANASTASIA = os.getenv("MCP_PORT_ANASTASIA")   # 5007 / 15007
MCP_PORT_ALENA = os.getenv("MCP_PORT_ALENA")           # 5020 / 15020
MCP_PORT_VALENTINA = os.getenv("MCP_PORT_VALENTINA")   # 5021 / 15021
MCP_PORT_MARINA = os.getenv("MCP_PORT_MARINA")         # 5024 / 15024
MCP_PORT_EGOISTKA = os.getenv("MCP_PORT_EGOISTKA")     # 5017 / 15017


async def _create_all_graphs() -> tuple[
    CompiledStateGraph,
    CompiledStateGraph,
    CompiledStateGraph,
    CompiledStateGraph,
    CompiledStateGraph,
    CompiledStateGraph,
    CompiledStateGraph,
    CompiledStateGraph,
]:
    """Create all agent graphs in parallel."""
    return await asyncio.gather(
        create_agent_graph(MCP_PORT_SOFIA),
        create_agent_graph(MCP_PORT_ANISA),
        create_agent_graph(MCP_PORT_ANNITTA),
        create_agent_graph(MCP_PORT_ANASTASIA),
        create_agent_graph(MCP_PORT_ALENA),
        create_agent_graph(MCP_PORT_VALENTINA),
        create_agent_graph(MCP_PORT_MARINA),
        create_agent_graph(MCP_PORT_EGOISTKA),
    )


(
    graph_sofia,
    graph_anisa,
    graph_annitta,
    graph_anastasia,
    graph_alena,
    graph_valentina,
    graph_marina,
    graph_egoistka,
) = asyncio.run(_create_all_graphs())
