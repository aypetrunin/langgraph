"""Модуль описвающий граф агента."""

from langgraph.graph import END, START, StateGraph

from .zena_agent_node import (
    agent,
    builder_prompt,
    count_tokens,
    data_collection,
    mcp_tools,
    should_continue,
    tools_node,
    verification_message,
)
from .zena_state import Context, InputState, OutputState, State

workflow = StateGraph(
    state_schema=State,
    input_schema=InputState,
    output_schema=OutputState,
    context_schema=Context,
)

workflow.add_node("verification_message", verification_message)
workflow.add_node("data_collection", data_collection)
workflow.add_node("builder_prompt", builder_prompt)
workflow.add_node("mcp_tools", mcp_tools)
workflow.add_node("agent", agent)
workflow.add_node("tools", tools_node)
workflow.add_node("count_tokens", count_tokens)

# Связи между узлами
workflow.add_edge(START, "verification_message")
workflow.add_edge("data_collection", "builder_prompt")
workflow.add_edge("builder_prompt", "mcp_tools")
workflow.add_edge("mcp_tools", "agent")
workflow.add_conditional_edges(
    "agent", should_continue, {"tools": "tools", "end": "count_tokens"}
)
workflow.add_edge("tools", "agent")
workflow.add_edge("count_tokens", END)

graph = workflow.compile()
