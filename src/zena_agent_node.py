"""Модуль описывающий ноды графа."""

import os
import time
from pathlib import Path
from typing import Literal, Union

import aiofiles
import httpx
from jinja2 import Template
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime
from langgraph.types import Command

from .zena_common import _content_to_text, _func_name, logger
from .zena_postgres import data_collection_postgres, delete_history_messages
from .zena_state import Context, State

PREDEFINED_MESSAGES = [
    "Ваше сообщение не может быть обработано 🚫",
    "Пожалуйста, отправьте корректные данные 🙏",
    "Мы не можем принять это сообщение ❌",
    "Ай-ай-ай, ругаться плохо!",
    "Давайте без таких слов 🙂",
    "Попробуйте выразиться по-другому 😉",
    "Нехорошо так говорить 😇",
    "Давайте держать общение в позитивном ключе!",
]

PREDEFINED_STOP = "стоп"  # По этому кодовому слову чистится история диалога.


async def verification_message(
    state: "State", runtime: "Runtime[Context]"
) -> Command[Literal["data_collection", "__end__"]]:
    """Функция проверки.

    Функция проверки сообщения на:
    1. стоп - слово для очистки истории сообщений,
    2. на запретные темы
    3. на которые отвечаем
    """
    try:
        ctx = runtime.context or {}
        user_companychat = ctx.get("_user_companychat")

        messages = state["messages"]
        last_msg_content: Union[str, list, None] = (
            messages[-1].content if messages else None
        )
        last_message = _content_to_text(last_msg_content).strip()

        if last_message.lower() == PREDEFINED_STOP:
            await delete_history_messages(user_companychat)
            return Command(
                goto="__end__",
                update={
                    "messages": [AIMessage(content="Память очищена")],
                    "user_companychat": user_companychat,
                },
            )
        elif last_message in PREDEFINED_MESSAGES:
            return Command(
                goto="__end__",
                update={
                    "messages": [AIMessage(content=last_message)],
                    "user_companychat": user_companychat,
                },
            )
        else:
            return Command(
                goto="data_collection",
                update={
                    "user_companychat": user_companychat,
                },
            )

    except Exception as err:
        raise RuntimeError(
            f"{_func_name(0)}: ошибка верификации первого сообщения: {err}"
        ) from err


async def data_collection(state: State) -> State:
    """Загрузка данных из Postgres для контекста."""
    try:
        t0 = time.perf_counter()
        gathered = await data_collection_postgres(state["user_companychat"])
        duration = round(time.perf_counter() - t0, 4)
        return {
            **gathered,
            "query": state["messages"][-1].content,
            "time_all": duration,
            "time_node": [{"data_collection": duration}],
        }
    except Exception as err:
        raise RuntimeError(
            f"{_func_name(0)}: ошибка загрузки данных из Postgres: {err}"
        ) from err


async def builder_prompt(state: State) -> State:
    """Рендеринг системного промпта из шаблона (без блокирующего I/O)."""
    try:
        t0 = time.perf_counter()

        template_prompt_system = state["data"]["template_prompt_system"]
        tpl_path = Path(__file__).parent / "template" / template_prompt_system

        async with aiofiles.open(tpl_path, encoding="utf-8") as f:
            source = await f.read()
        prompt_system = Template(source).render(**state["data"])

        duration = round(time.perf_counter() - t0, 4)
        logger.info("✅ Промпт отрендерен: %d символов", len(prompt_system))
        return {
            "prompt_system": prompt_system,
            "template_prompt_system": template_prompt_system,
            "time_all": duration,
            "time_node": [{"builder_prompt": duration}],
        }
    except Exception as err:
        raise RuntimeError(
            f"{_func_name(0)}: ошибка формирования промпта: {err}"
        ) from err


async def mcp_tools(state: State) -> State:
    """Инициализация MCP инструментов через SSE с динамическим портом и фильтрацией."""
    try:
        t0 = time.perf_counter()

        mcp_port = state["data"].get("mcp_port")

        client = MultiServerMCPClient(
            {
                "company": {
                    "transport": "sse",
                    "url": f"http://172.17.0.1:{mcp_port}/sse",
                }
            }
        )
        all_tools = await client.get_tools()

        # Базовые инструменты (доступны всегда)
        allowed_tool_names = [
            "zena_faq",
            "zena_services",
            "zena_product_search",
        ]

        dialog_state = state["data"].get("dialog_state", "new")

        if dialog_state not in ["new"]:
            allowed_tool_names.append("zena_record_product_id")

        if dialog_state not in ["new", "selecting"]:
            allowed_tool_names.extend(
                ["zena_record_time", "zena_available_time_for_master"]
            )
        # Фильтруем инструменты
        filtered_tools = [tool for tool in all_tools if tool.name in allowed_tool_names]

        logger.info(f"✅ Порт {mcp_port}, dialog_state='{dialog_state}'")
        logger.info(
            f"✅ Доступно {len(filtered_tools)} из {len(all_tools)} инструментов: {[t.name for t in filtered_tools]}"
        )
        duration = round(time.perf_counter() - t0, 4)
        return {
            "tools": filtered_tools,
            "time_all": duration,
            "time_node": [{"mcp_tools": duration}],
            "dialog_state": dialog_state,
        }
    except Exception as err:
        raise RuntimeError(
            f"{_func_name(0)}: ошибка формирования списка инструментов: {err}"
        ) from err


async def agent(state: State) -> State:
    """Узел агента с MCP инструментами."""
    try:
        t0 = time.perf_counter()

        openai_proxy = os.getenv("OPENAI_PROXY_URL")
        openai_model = os.getenv("OPENAI_MODEL")
        openai_api_key = os.getenv("OPENAI_API_KEY")

        http_client = httpx.AsyncClient(proxy=openai_proxy, timeout=60.0)
        model = init_chat_model(
            model=openai_model,
            temperature=0,
            api_key=openai_api_key,
            http_async_client=http_client,
        ).bind_tools(state["tools"])

        # Формируем сообщения с системным промптом
        messages = []
        if state.get("prompt_system"):
            messages.append({"role": "system", "content": state["prompt_system"]})

        # Добавляем историю сообщений
        messages.extend(state["messages"])
        logger.info(f"🤖 Агент обрабатывает запрос: {state['messages'][-1].content}")

        # Вызываем модель
        response = await model.ainvoke(messages)

        # Логируем результат
        if hasattr(response, "content") and response.content:
            logger.info(f"✅ Агент ответил: {response.content[:100]}...")
        if hasattr(response, "tool_calls") and response.tool_calls:
            logger.info(
                f"🔧 Вызов инструментов: {[tc['name'] for tc in response.tool_calls]}"
            )

        duration = round(time.perf_counter() - t0, 4)
        return {
            "messages": [response],
            "time_all": duration,
            "time_node": [{"agent": duration}],
        }
    except Exception as err:
        raise RuntimeError(f"{_func_name(0)}: ошибка в работе агента: {err}") from err


async def tools_node(state: State) -> State:
    """Узел для вызова инструментов."""
    try:
        t0 = time.perf_counter()

        last_message = state["messages"][-1]
        tool_node = ToolNode(state["tools"])
        # Логируем вызовы инструментов
        logger.info(
            f"🔧 Выполнение инструментов: {[tc['name'] for tc in last_message.tool_calls]}"
        )

        # Вызываем инструменты
        result = await tool_node.ainvoke(state)

        # Выделяем имя инструмента, аргументы и результат для логгирования
        tools_name = [tc["name"] for tc in last_message.tool_calls]
        args = [tc["args"] for tc in last_message.tool_calls]
        args_clean = [{k: v for k, v in d.items() if k != "session_id"} for d in args]
        tools_args = [{name: args} for name, args in zip(tools_name, args_clean)]
        tools_results = [{msg.name: msg.content} for msg in result["messages"]]

        # Название последнего диалога определяет новое состояние диалога.
        # Если его нет в map_state, тогда состояние остается прежним.
        map_state = {
            "zena_product_search": "selecting",
            "zena_record_product_id": "record",
            "zena_record_time": "posrecord",
        }
        dialog_state_new = map_state.get(tools_name[-1], state["dialog_state"])

        duration = round(time.perf_counter() - t0, 4)
        result.update(
            {
                "time_all": duration,
                "time_node": [{"tools_node": duration}],
                "tools_name": tools_name,
                "tools_args": tools_args,
                "tools_results": tools_results,
                "dialog_state_new": dialog_state_new,
            }
        )
        logger.info("✅ Инструменты выполнены успешно")
        return result

    except Exception as err:
        raise RuntimeError(
            f"{_func_name(0)}: ошибка выполнения инструмента: {err}"
        ) from err


async def should_continue(state: State) -> str:
    """Условие продолжения: есть ли вызовы инструментов."""
    try:
        last_message = state["messages"][-1]
        # Проверяем, есть ли tool_calls
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            logger.info(
                f"🔧 Вызов инструментов: {[tc['name'] for tc in last_message.tool_calls]}"
            )
            return "tools"
        else:
            logger.info("✅ Ответ готов, завершаем")
            return "end"

    except Exception as err:
        raise RuntimeError(
            f"{_func_name(0)}: ошибка условного ветвления: {err}"
        ) from err


async def count_tokens(state: State) -> State:
    """Функция подсчета токенов."""
    try:
        total_input = 0
        total_output = 0
        total_all = 0

        for msg in state.get("messages", []):
            usage = {}
            # 1) Прямой путь: usage_metadata на сообщении (AIMessage/HumanMessage могут иметь)
            if hasattr(msg, "usage_metadata") and isinstance(msg.usage_metadata, dict):
                usage = msg.usage_metadata or {}
            # 2) Fallback: внутри response_metadata.token_usage (часто кладут SDK)
            if not usage and hasattr(msg, "response_metadata"):
                meta = msg.response_metadata or {}
                if isinstance(meta, dict):
                    tu = meta.get("token_usage") or {}
                    if isinstance(tu, dict):
                        # нормализуем к единому виду
                        usage = {
                            "input_tokens": tu.get(
                                "prompt_tokens", tu.get("input_tokens", 0)
                            ),
                            "output_tokens": tu.get(
                                "completion_tokens", tu.get("output_tokens", 0)
                            ),
                            "total_tokens": tu.get("total_tokens", 0),
                        }
            if usage:
                inp = int(usage.get("input_tokens", 0) or 0)
                out = int(usage.get("output_tokens", 0) or 0)
                tot = int(usage.get("total_tokens", 0) or (inp + out))
                total_input += inp
                total_output += out
                total_all += tot

        return {
            "tokens": {
                "input_tokens": total_input,
                "output_tokens": total_output,
                "total_tokens": total_all,
            }
        }
    except Exception as err:
        raise RuntimeError(
            f"{_func_name(0)}: ошибка при подсчете токенов: {err}"
        ) from err
