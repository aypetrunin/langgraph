"""Middleware, выполняемые перед запуском агента.

VerifyInputMessage — проверяет входящее сообщение:
  - «стоп» → очистка истории диалога
  - «phone» → удаление персональных данных
  - сообщение из списка запрещённых тем → возврат клиенту без агента
GetDatabaseMiddleware — загружает данные из PostgreSQL (канал, промпты,
  категории, услуги, мастера, пользователь) и внешних API (CRM GO).
GetKeyWordMiddleware — ищет услуги по ключевым словам из промо-таблицы.
GetCRMGOMiddleware — загружает данные онбординга из CRM GO (порт 5020).
"""

from __future__ import annotations

import time
from typing import Any, Union

from langchain.agents.middleware import (
    AgentMiddleware,
    hook_config,
)
from langchain_core.messages import AIMessage, BaseMessage
from langgraph.runtime import Runtime

from .zena_common import _content_to_text
from .zena_logging import bind_contextvars, clear_contextvars, get_logger, mark_graph_start
from .zena_postgres import (
    data_collection_postgres,
    data_user_info,
    delete_history_messages,
    delete_personal_data,
    fetch_key_words,
    save_query_from_human_in_postgres,
)
from .zena_requests import fetch_crm_go_client_info
from .zena_state import Context, State

logger = get_logger()

# Список сообщений из httpservice на запрещенные темы.
# которые передаем клиенту через бота.
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

# По этому кодовому слову чистится история диалога.
PREDEFINED_STOP = "стоп"
PREDEFINED_DEL_PERSONAL_DATA = "phone" 


class VerifyInputMessage(AgentMiddleware):
    """Проверяет входящее сообщение перед запуском агента."""

    @hook_config(can_jump_to=["end"])
    async def abefore_agent(
        self,
        state: State,
        runtime: Runtime[Context],
    ) -> dict[str, Any] | None:
        """Обрабатывает стоп-команды и запрещённые сообщения."""
        try:
            ctx = runtime.context or {}
            user_companychat = ctx.get("_user_companychat")

            # Привязываем user_cc ко всем логам этого запроса
            request_id = ctx.get("_request_id") or f"{user_companychat}:{int(time.time())}"
            clear_contextvars()
            bind_contextvars(user_cc=user_companychat, request_id=request_id)
            mark_graph_start()

            logger.info("middleware.started", middleware="VerifyInputMessage")

            studio = ctx.get("_studio", False)
            logger.debug("config.studio", studio=studio)


            messages = state["messages"]
            last_msg_content: Union[str, list[BaseMessage], None] = (
                messages[-1].content if messages else None
            )
            last_message = _content_to_text(last_msg_content).strip()
            
            # Сoхранение сообщения из LangSmith Studio (тестирование).
            if studio:
                await save_query_from_human_in_postgres(user_companychat, last_message)

            if last_message.lower() == PREDEFINED_STOP:
                await delete_history_messages(user_companychat)
                data = await data_user_info(user_companychat)
                return {
                    "messages": [AIMessage(content="Память очищена")],
                    "user_companychat": user_companychat,
                    **data,
                    "jump_to": "end"
                }
            if last_message.lower() == PREDEFINED_DEL_PERSONAL_DATA:
                await delete_personal_data(user_companychat)
                data = await data_user_info(user_companychat)
                return {
                    "messages": [AIMessage(content="Персональные данные удалены")],
                    "user_companychat": user_companychat,
                    **data,
                    "jump_to": "end"
                }
            elif last_message in PREDEFINED_MESSAGES:
                return {
                    "messages": [AIMessage(content=last_message)],
                    "user_companychat": user_companychat,
                    "jump_to": "end"
                }
            else:
                return {
                    "user_companychat": user_companychat,
                }

        except Exception:
            logger.exception("middleware.error", middleware="VerifyInputMessage")
            return {
                "messages": [AIMessage(content='Бот временно не работает')],
                "jump_to": "end"
            }


class GetDatabaseMiddleware(AgentMiddleware):
    """Middleware реализует функцию чтения данных из базы данных."""

    _LIST_DEFAULT_KEYS = (
        "items_search",
        "item_selected",
        "available_time",
        "available_sequences",
        "office_id",
        "desired_date",
        "desired_time",
        "desired_master",
        "user_records",
    )

    @hook_config(can_jump_to=["end"])
    async def abefore_agent(
        self,
        state: State,
        runtime: Runtime[Context],
    ) -> dict[str, Any] | None:
        """Загружает данные из PostgreSQL и внешних API перед запуском агента."""
        try:
            logger.info("middleware.started", middleware="GetDatabase")

            ctx = runtime.context or {}
            access_token = ctx.get("_access_token")
            user_companychat = ctx.get("_user_companychat")
            reply_to_history_id = ctx.get("_reply_to_history_id")

            gathered = await data_collection_postgres(user_companychat)
            if not isinstance(gathered, dict):
                raise TypeError(f"data_collection_postgres returned {type(gathered)!r}, expected dict")

            data = gathered.setdefault("data", {})
            state_data = state.get("data") or {}

            # dialog_state / dialog_state_in
            dialog_state = state_data.get("dialog_state") or "new"
            data["dialog_state"] = dialog_state
            data["dialog_state_in"] = dialog_state
            data["user_companychat"] = user_companychat
            data["reply_to_history_id"] = reply_to_history_id
            data["access_token"] = access_token

            # дефолты для списковых ключей
            for key in self._LIST_DEFAULT_KEYS:
                data[key] = state_data.get(key) or data.get(key) or []
 
            mcp_port = data.get("mcp_port")
            logger.debug("config.mcp_port", mcp_port=mcp_port)

            if mcp_port == 5020:
                # Режим опроса клиента.
                onboarding_from_state = state_data.get("onboarding")
                if onboarding_from_state is not None:
                    data["onboarding"] = onboarding_from_state
                    return {
                        **gathered,
                    }

                # Проверка клиента на ввод телефона и согласия на обработку ПД.
                phone = data.get("phone")
                if phone:
                    response = await fetch_crm_go_client_info(phone=phone)
                    success = bool(response.get("success", False))
                    logger.info("crm.lookup", success=success)

                    onboarding = data.setdefault("onboarding", {})
                    onboarding["onboarding_status"] = success
                    if not success:
                        onboarding.setdefault("onboarding_stage", 0)

            return {
                **gathered,
            } 

        except Exception:
            logger.exception("middleware.error", middleware="GetDatabase")
            return {
                "messages": [AIMessage(content="Бот временно не работает")],
                "jump_to": "end",
            }


class GetKeyWordMiddleware(AgentMiddleware):
    """Middleware реализует функцию чтения данных из базы данных."""

    @hook_config(can_jump_to=["end"])
    async def abefore_agent(
        self,
        state: State,
        runtime: Runtime[Context],
    ) -> dict[str, Any] | None:
        """Ищет услуги по ключевым словам из промо-таблицы."""
        logger.info("middleware.started", middleware="GetKeyWord")
        try:
            channel_id = state["data"]["channel_id"]

            messages = state["messages"]
            last_msg_content: Union[str, list[BaseMessage], None] = (
                messages[-1].content if messages else None
            )
            last_message = _content_to_text(last_msg_content).strip()

            logger.debug("input.last_message", last_message=last_message)

            promo = await fetch_key_words(channel_id, last_message)
            logger.debug("keyword.promo_result", promo=promo)

            if not promo:
                return None
            
            data = state.get('data')
            data['items_search'] = promo
            data['dialog_state'] = 'promo'

            logger.debug("state.data", data=data)

            return {
                **data
            }

        except Exception:
            logger.exception("middleware.error", middleware="GetKeyWord")
            return {
                "messages": [AIMessage(content="Бот временно не работает")],
                "jump_to": "end",
            }


class GetCRMGOMiddleware(AgentMiddleware):
    """Middleware реализует функцию чтения данных из CRM GO."""

    ALLOWED_PORT = [5020]

    @hook_config(can_jump_to=["end"])
    async def abefore_agent(
        self,
        state: State,
        runtime: Runtime[Context],
    ) -> dict[str, Any] | None:
        """Читает данные onboarding из GO CRM."""
        try:
            logger.info("middleware.started", middleware="GetCRMGO")

            data = state.get("data", {})
            phone = data.get("phone")
            mcp_port = data.get("mcp_port")

            # Ранний возврат для нецелевого порта
            if mcp_port not in self.ALLOWED_PORT:
                data.setdefault("onboarding", {}).setdefault("onboarding", True)
                return {"data": data}
            
            if not state.get("data", {}).get('onboarding'):
                # Получаем и обрабатываем данные CRM
                logger.debug("crm.fetching")
                raw_onboarding = await fetch_crm_go_client_info(phone=phone)
                data["onboarding"] = raw_onboarding

            logger.debug("crm.onboarding", onboarding=data["onboarding"])

            return {"data": data}

        except Exception:
            logger.exception("middleware.error", middleware="GetCRMGO")
            return {
                "messages": [AIMessage(content='Бот временно не работает')],
                "jump_to": "end"
            }
