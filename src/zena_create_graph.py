"""Создание системых графов/шаблонов для каждой компании."""

import os
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


MCP_PORT_ALISA = os.getenv("MCP_PORT_ALISA") # 5001 / 15001
MCP_PORT_SOFIA = os.getenv("MCP_PORT_SOFIA") # 5002 / 15002
MCP_PORT_ANISA = os.getenv("MCP_PORT_ANISA") # 5005 / 15005
MCP_PORT_ANNITTA = os.getenv("MCP_PORT_ANNITTA") # 5006 / 15006
MCP_PORT_ANASTASIA = os.getenv("MCP_PORT_ANASTASIA") # 5007 / 15007
MCP_PORT_ALENA = os.getenv("MCP_PORT_ALENA") # 5020 / 15020
MCP_PORT_VALENTINA = os.getenv("MCP_PORT_VALENTINA") # 5021 / 15021


graph_alisa = asyncio.run(create_agent_graph(MCP_PORT_ALISA))
graph_sofia = asyncio.run(create_agent_graph(MCP_PORT_SOFIA))
graph_anisa = asyncio.run(create_agent_graph(MCP_PORT_ANISA))
graph_annitta = asyncio.run(create_agent_graph(MCP_PORT_ANNITTA))
graph_anastasia = asyncio.run(create_agent_graph(MCP_PORT_ANASTASIA))
graph_alena = asyncio.run(create_agent_graph(MCP_PORT_ALENA))
graph_valentina = asyncio.run(create_agent_graph(MCP_PORT_VALENTINA))
