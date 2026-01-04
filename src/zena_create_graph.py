"""Создание системых графов/шаблонов для каждой компании."""

import asyncio

from langgraph.graph import END, START, StateGraph

from .zena_create_agent import create_agent_mcp
from .zena_state import Context, InputState, OutputState, State


async def create_agent_graph(port: int):
    """Универсальная фабрика для LangGraph CLI."""
    
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


graph_5001 = asyncio.run(create_agent_graph(5001))
graph_5002 = asyncio.run(create_agent_graph(5002))
graph_5005 = asyncio.run(create_agent_graph(5005))
graph_5006 = asyncio.run(create_agent_graph(5006))
graph_5007 = asyncio.run(create_agent_graph(5007))
graph_5020 = asyncio.run(create_agent_graph(5020))
