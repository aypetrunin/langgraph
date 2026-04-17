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

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from .zena_create_agent import create_agent_mcp
from .zena_settings import get_settings
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
    settings = get_settings()
    return await asyncio.gather(
        create_agent_graph(settings.mcp_port_sofia),
        create_agent_graph(settings.mcp_port_anisa),
        create_agent_graph(settings.mcp_port_annitta),
        create_agent_graph(settings.mcp_port_anastasia),
        create_agent_graph(settings.mcp_port_alena),
        create_agent_graph(settings.mcp_port_valentina),
        create_agent_graph(settings.mcp_port_marina),
        create_agent_graph(settings.mcp_port_egoistka),
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
