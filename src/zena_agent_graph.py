import asyncio
from langgraph.graph import END, START, StateGraph
from langchain_core.messages import HumanMessage, AIMessage


from zena.zena_common import logger
from zena.zena_state import State
from zena.zena_agent_node import (
    data_collection,
    builder_prompt,
    init_mcp_tools,
    agent_tokens,
    tools_node,
    should_continue,
)


async def main():
    # Создание графа
    workflow = StateGraph(State)
    workflow.add_node("data_collection", data_collection)
    workflow.add_node("builder_prompt", builder_prompt)
    workflow.add_node("init_mcp_tools", init_mcp_tools)
    workflow.add_node("agent", agent_tokens)
    workflow.add_node("tools", tools_node)
    
    # Связи между узлами
    workflow.add_edge(START, "data_collection")
    workflow.add_edge("data_collection", "builder_prompt")
    workflow.add_edge("builder_prompt", "init_mcp_tools")
    workflow.add_edge("init_mcp_tools", "agent")
    workflow.add_conditional_edges(
        "agent",
        should_continue,
        {"tools": "tools", "close_connection": END}
    )
    workflow.add_edge("tools", "agent")
    
    graph = workflow.compile()
    
    # Запуск графа
    logger.info("=" * 60)
    logger.info("🚀 Запуск агента Zena")
    logger.info("=" * 60)
     
    response = await graph.ainvoke({
        "messages": [
            HumanMessage(content="Нужен массаж головы")
        ],
        "user_companychat": 124
    })
    
    print("\n" + "=" * 60)
    print("📋 РЕЗУЛЬТАТ:")
    print("=" * 60)
    
    # Выводим последнее сообщение от агента
    for msg in response["messages"]:
        if isinstance(msg, AIMessage) and msg.content:
            print(f"\n🤖 Агент: {msg.content}")
    return response

if __name__ == "__main__":
    result = asyncio.run(main())

# Запуск
# cd /home/copilot_superuser/petrunin/agents
# uv run --active python -m zena.zena_agent_graph