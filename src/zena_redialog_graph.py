"""Граф агента реанимации диалога.

Простой граф START → agent_redialog → END.
Используется для генерации follow-up вопроса к клиенту,
который перестал отвечать в диалоге.

Экспортируется как graph_agent_redialog для langgraph.json
(endpoint: agent_zena_redialog).
"""

from langgraph.graph import END, START, StateGraph

from .zena_redialog_agent import AgentState, agent_redialog

# Граф: START → agent_redialog → END
graph_agent_redialog = StateGraph(state_schema=AgentState)
graph_agent_redialog.add_node("agent_redialog", agent_redialog)
graph_agent_redialog.add_edge(START, "agent_redialog")
graph_agent_redialog.add_edge("agent_redialog", END)
graph_agent_redialog = graph_agent_redialog.compile()
