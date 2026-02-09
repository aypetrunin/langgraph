# src/graphs/redialog_factory.py
"""
Модуль: graphs.redialog_factory
===============================

Этот модуль отвечает за **сборку отдельного типа LangGraph-графа** — Redialog,
который “реанимирует” диалог: на вход получает состояние диалога и возвращает
ОДИН естественный вопрос, который помогает продолжить разговор.

-------------------------------------------------------------------------------
ЗАЧЕМ ЭТО НУЖНО (ПРОВЕРКА МАСШТАБИРУЕМОСТИ АРХИТЕКТУРЫ)
-------------------------------------------------------------------------------

В проекте уже есть основной тип графов (persona/MCP), где агент подключается к MCP
по порту и использует общий State/Context/Input/Output.

Redialog-граф — это **второй тип**:
- другая state_schema (AgentState),
- другой агент/подграф (get_agent_redialog),
- другая задача.

Вынос в отдельную фабрику демонстрирует, что архитектура масштабируется:
- registry (кеш/TTL/force_reload) — общий,
- settings/logging — общие,
- factory — специфична под тип графа.

-------------------------------------------------------------------------------
СТРУКТУРА ГРАФА
-------------------------------------------------------------------------------

Фиксированная структура:

    START → "agent_redialog" → END

Узел "agent_redialog" — это готовый runnable/подграф, который возвращает
текст (вопрос) согласно system prompt.

-------------------------------------------------------------------------------
ВАЖНО
-------------------------------------------------------------------------------

- Эта фабрика **не кеширует** граф.
  Кеширование делается на уровне `GraphRegistry` (entrypoint слой).

- Эта фабрика **не читает env** и **не настраивает логирование**.

-------------------------------------------------------------------------------
КОНТРАКТ
-------------------------------------------------------------------------------

async def create_redialog_graph() -> CompiledStateGraph

- всегда возвращает **CompiledStateGraph**
- без import-time сайд-эффектов
"""

from __future__ import annotations

from langchain.agents.middleware.types import AgentState
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ..zena_redialog_agent import get_agent_redialog


async def create_redialog_graph() -> CompiledStateGraph:
    """
    Создать LangGraph-граф Redialog.

    Шаги:
    1) Получить “агента” redialog (реализован как runnable/подграф)
    2) Построить граф START → agent_redialog → END
    3) Скомпилировать и вернуть

    Возвращает:
        CompiledStateGraph
    """
    agent = await get_agent_redialog()

    workflow = StateGraph(state_schema=AgentState)
    workflow.add_node("agent_redialog", agent)
    workflow.add_edge(START, "agent_redialog")
    workflow.add_edge("agent_redialog", END)

    return workflow.compile()

