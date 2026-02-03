"""Создание графа агента реаниматора диалога."""

from langgraph.graph import END, START, StateGraph

from .zena_redialog_agent import agent_redialog, AgentState

graph_agent_redialog = StateGraph(
    state_schema=AgentState,
)
graph_agent_redialog.add_node("agent_redialog", agent_redialog)
graph_agent_redialog.add_edge(START, "agent_redialog")
graph_agent_redialog.add_edge("agent_redialog", END)
    
graph_agent_redialog.compile()
