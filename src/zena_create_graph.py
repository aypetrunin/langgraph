"""Создание системых графов/шаблонов для каждой компании."""

import os
import asyncio

from langgraph.runtime import Runtime
from langchain_core.runnables import RunnableConfig
from langgraph_sdk import get_client
from langgraph.graph import END, START, StateGraph

from .zena_create_agent import create_agent_mcp
from .zena_state import Context, InputState, OutputState, State
from .zena_common import logger

async def schedule_memories(state: State, runtime: Runtime[Context], config: RunnableConfig) -> None:
    """Prompt the bot to respond to the user, incorporating memories (if provided)."""
    # configurable = ChatConfigurable.from_context()
    logger.info("schedule_memories")
    print(f"config: {config}")
    logger.info(f"runtime: {runtime}")
    memory_client = get_client()
    logger.info(f"memory_client: {memory_client}")
    logger.info(f"state: {state}")
    logger.info(f"messages: {state['messages']}")

    thread_id = config["configurable"]["thread_id"]
    logger.info(f"thread_id: {thread_id}")
    
    logger.info(f"thread_id: {thread_id}")

    await memory_client.runs.create(
        # We enqueue the memory formation process on the same thread.
        # This means that IF this thread doesn't receive more messages before `after_seconds`,
        # it will read from the shared state and extract memories for us.
        # If a new request comes in for this thread before the scheduled run is executed,
        # that run will be canceled, and a **new** one will be scheduled once
        # this node is executed again.
        thread_id=thread_id,
        # This memory-formation run will be enqueued and run later
        # If a new run comes in before it is scheduled, it will be cancelled,
        # then when this node is executed again, a *new* run will be scheduled
        multitask_strategy="enqueue",
        # This lets us "debounce" repeated requests to the memory graph
        # if the user is actively engaging in a conversation. This saves us $$ and
        # can help reduce the occurrence of duplicate memories.
        after_seconds=5,
        # Specify the graph and/or graph configuration to handle the memory processing
        assistant_id='memory_graph',
        input={"messages": state['messages']},
        config={
            "configurable": {
                # Ensure the memory service knows where to save the extracted memories 
                "user_id": 'Andrey',
                # "memory_types": configurable.memory_types,
            },
        },
    )



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
    workflow.add_node("schedule_memories", schedule_memories)
    workflow.add_edge(START, "agent")
    workflow.add_edge("agent", "schedule_memories")
    workflow.add_edge("schedule_memories", END)
    
    return workflow.compile()


MCP_PORT_ALISA = os.getenv("MCP_PORT_ALISA") # 5001 / 15001
MCP_PORT_SOFIA = os.getenv("MCP_PORT_SOFIA") # 5002 / 15002
MCP_PORT_ANISA = os.getenv("MCP_PORT_ANISA") # 5005 / 15005
MCP_PORT_ANNITTA = os.getenv("MCP_PORT_ANNITTA") # 5006 / 15006
MCP_PORT_ANASTASIA = os.getenv("MCP_PORT_ANASTASIA") # 5007 / 15007
MCP_PORT_ALENA = os.getenv("MCP_PORT_ALENA") # 5020 / 15020

graph_alisa = asyncio.run(create_agent_graph(MCP_PORT_ALISA))
graph_sofia = asyncio.run(create_agent_graph(MCP_PORT_SOFIA))
graph_anisa = asyncio.run(create_agent_graph(MCP_PORT_ANISA))
graph_annitta = asyncio.run(create_agent_graph(MCP_PORT_ANNITTA))
graph_anastasia = asyncio.run(create_agent_graph(MCP_PORT_ANASTASIA))
graph_alena = asyncio.run(create_agent_graph(MCP_PORT_ALENA))
