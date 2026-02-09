"""
Модуль: graphs.factory
======================

Этот модуль отвечает **исключительно за сборку LangGraph-графа**.

Он НЕ знает:
- кто и как вызывает граф,
- какие есть persona (sofia/anisa/...),
- как работает кеш или TTL,
- как устроен runtime.

Он ЗНАЕТ:
- как из порта MCP собрать один LangGraph,
- какую структуру имеет граф,
- какие state/schema используются.

-------------------------------------------------------------------------------
ОБЩАЯ ИДЕЯ
-------------------------------------------------------------------------------

Фабрика строит граф фиксированной структуры:

    START → "agent" → END

Где:
- `agent` создаётся через `create_agent_mcp(mcp_port=...)`
- перед созданием агента выполняется `init_resources()` (fail-fast)

-------------------------------------------------------------------------------
ЗАЧЕМ ВЫНОСИТЬ В ОТДЕЛЬНЫЙ МОДУЛЬ
-------------------------------------------------------------------------------

1) **Single Responsibility**
   - registry = кеш + TTL + lock
   - factory  = как собрать граф
   - entrypoint = как это всё связать для runtime

2) **Тестируемость**
   - фабрику легко мокать
   - registry тестируется отдельно
   - entrypoint остаётся тупым

3) **Будущие расширения**
   - разные типы графов
   - несколько узлов
   - conditional edges
   - разные фабрики под разные продукты

-------------------------------------------------------------------------------
КОНТРАКТ
-------------------------------------------------------------------------------

create_agent_graph(port: int) -> CompiledStateGraph

- всегда возвращает **скомпилированный** LangGraph
- не кеширует
- не логирует кеш
- не читает env
"""

from __future__ import annotations

from langgraph.graph import START, END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ..zena_create_agent import create_agent_mcp
from ..zena_resources import init_resources
from ..zena_state import Context, InputState, OutputState, State


async def create_agent_graph(port: int) -> CompiledStateGraph:
    """
    Создать LangGraph-граф для одного агента.

    Шаги:
    1) Fail-fast инициализация shared ресурсов
    2) Создание agent-узла (MCP на заданном порту)
    3) Построение графа START → agent → END
    4) Компиляция графа

    Аргументы:
        port: int
            Порт MCP-сервера, к которому будет подключён агент.

    Возвращает:
        CompiledStateGraph
    """
    # 1) shared ресурсы (Postgres, Redis, HTTP, LLM, ...)
    await init_resources()

    # 2) агент (может лениво подтягивать MCP tools)
    agent = await create_agent_mcp(mcp_port=port)

    # 3) граф
    workflow = StateGraph(
        state_schema=State,
        input_schema=InputState,
        output_schema=OutputState,
        context_schema=Context,
    )

    workflow.add_node("agent", agent)
    workflow.add_edge(START, "agent")
    workflow.add_edge("agent", END)

    # 4) compile
    return workflow.compile()
