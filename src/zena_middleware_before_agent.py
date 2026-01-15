"""Middleware before agent."""

from __future__ import annotations

from typing import Any, Union


from langgraph.runtime import Runtime
from langchain_core.messages import AIMessage, BaseMessage
from langchain.agents.middleware import (
    AgentState,
    AgentMiddleware,
    hook_config,
)
from .zena_state import State, Context
from .zena_common import logger, _content_to_text
from .zena_postgres import (
    delete_history_messages,
    delete_personal_data,
    data_collection_postgres,
    save_query_from_human_in_postgres,
    data_user_info,
    fetch_key_words,
)
from .zena_requests import fetch_personal_info, fetch_crm_go_client_info

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
    @hook_config(can_jump_to=["end"])
    async def abefore_agent(
        self,
        state: State,
        runtime: Runtime[Context],
    ) -> dict[str, Any] | None:
        
        try:
            logger.info("===abefore_agent===VerifyInputMessage===")

            ctx = runtime.context or {}
            user_companychat = ctx.get("_user_companychat")
            studio = ctx.get("_studio", False)
            logger.info(f"studio: {studio}")


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
                # responce_mem = await memory.delete_all(run_id='test')
                # logger.info(f"responce_mem delete: {responce_mem}")
                return {
                    "messages": [AIMessage(content="Память очищена")],
                    "user_companychat": user_companychat,
                    **data,
                    "jump_to": "end"
                }
            if last_message.lower() == PREDEFINED_DEL_PERSONAL_DATA:
                await delete_personal_data(user_companychat)
                data = await data_user_info(user_companychat)
                # responce_mem = await memory.delete_all(run_id='test')
                # logger.info(f"responce_mem delete: {responce_mem}")
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

        except Exception as err:
            logger.exception(f"VerifyInputMessage: {err}")
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
    )

    @hook_config(can_jump_to=["end"])
    async def abefore_agent(
        self,
        state: State,
        runtime: Runtime[Context],
    ) -> dict[str, Any] | None:
        try:
            logger.info("===GetDatabaseMiddleware===")

            ctx = runtime.context or {}
            user_companychat = ctx.get("_user_companychat")

            gathered = await data_collection_postgres(user_companychat)
            if not isinstance(gathered, dict):
                raise TypeError(f"data_collection_postgres returned {type(gathered)!r}, expected dict")

            data = gathered.setdefault("data", {})
            state_data = state.get("data") or {}
            logger.info(f"state_data: {state_data}")
            logger.info(f"gathered: {gathered}")

            # dialog_state / dialog_state_in
            dialog_state = state_data.get("dialog_state") or "new"
            data["dialog_state"] = dialog_state
            data["dialog_state_in"] = dialog_state

            # дефолты для списковых ключей
            for key in self._LIST_DEFAULT_KEYS:
                data[key] = state_data.get(key) or data.get(key) or []

            mcp_port = data.get("mcp_port")
            logger.info("mcp_port=%s", mcp_port)

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
                    logger.info("GO lookup by phone success=%s", success)

                    onboarding = data.setdefault("onboarding", {})
                    onboarding["onboarding_status"] = success
                    if not success:
                        onboarding.setdefault("onboarding_stage", 0)

            return {
                **gathered,
            } 

        except Exception as err:
            logger.exception("GetDatabaseMiddleware error: %s", err)
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

        logger.info("===GetKeyWordMiddleware===")
        try:
            channel_id = state["data"]["channel_id"]

            messages = state["messages"]
            last_msg_content: Union[str, list[BaseMessage], None] = (
                messages[-1].content if messages else None
            )
            last_message = _content_to_text(last_msg_content).strip()

            logger.info(f"last_message: {last_message}")

            promo = await fetch_key_words(channel_id, last_message)
            logger.info(f"promo: {promo}")

            if not promo:
                return None
            
            data = state.get('data')
            data['items_search'] = promo
            data['dialog_state'] = 'promo'

            logger.info(f"data: {data}")

            return {
                **data
            }

        except Exception as err:
            logger.exception("GetKeyWordMiddleware error: %s", err)
            return {
                "messages": [AIMessage(content="Бот временно не работает")],
                "jump_to": "end",
            }

# class GetDatabaseMiddleware(AgentMiddleware):
#     """Middleware реализует функцию чтения данных из базы данных."""

#     @hook_config(can_jump_to=["end"])
#     async def abefore_agent(
#         self,
#         state: State,
#         runtime: Runtime[Context],
#     ) -> dict[str, Any] | None:
#         """Функция реализует функцию чтения данных из базы данных."""

#         try:
#             logger.info("===GetDatabaseMiddleware===")
#             logger.info(f"\nstart - state:\n{state}")

#             ctx = runtime.context or {}
#             studio = ctx.get("_studio", False)
#             user_companychat = ctx.get("_user_companychat")

#             messages = state["messages"]
#             last_msg_content: Union[str, list[BaseMessage], None] = (
#                 messages[-1].content if messages else None
#             )
#             last_message = _content_to_text(last_msg_content).strip()

#             gathered = await data_collection_postgres(user_companychat)
#             logger.info(f"gathered: {gathered}")
#             # 'dialog_state_in' - сохранение состояния диалога в начале работы агента.
#             # 'dialog_state' - состояние диалога во время работы агента, может меняться,
#             # в конце работы агента будет сохранено конечное состояние диалога.
#             # gathered['data']['dialog_state_in'] = gathered['data']['dialog_state']
#             if state.get("data", {}).get('dialog_state'):
#                 gathered['data']['dialog_state'] = state['data']['dialog_state']
#                 gathered['data']['dialog_state_in'] = state['data']['dialog_state']
#             else:
#                 gathered['data']['dialog_state'] = 'new'
#                 gathered['data']['dialog_state_in'] = 'new'
            
#             if state.get("data", {}).get('item_selected'):
#                 gathered['data']['item_selected'] = state['data']['item_selected']
#             else:
#                 gathered['data']['item_selected'] = []
            
#             if state.get("data", {}).get('available_time'):
#                 gathered['data']['available_time'] = state['data']['available_time']
#             else:
#                 gathered['data']['available_time'] = []
            
#             if state.get("data", {}).get('available_sequences'):
#                 gathered['data']['available_sequences'] = state['data']['available_sequences']
#             else:
#                 gathered['data']['available_sequences'] = []

#             mcp_port = gathered['data'].get("mcp_port")
#             logger.info(f"mcp_port:{mcp_port}")
            
#             if mcp_port == 5020:
#                 # Режим опроса клиента.
#                 if state.get("data", {}).get('onboarding'):
#                     gathered['data']['onboarding'] = state['data']['onboarding']
#                     return{**gathered}
                
#                 # Проверка клиента на ввод телефона и и соглпсия обработку ПД. 
#                 phone = gathered['data']['phone']
#                 if phone:
#                     # Проверка клиента на наличие в GO по телефону.
#                     response = await fetch_crm_go_client_info(phone=phone)
#                     logger.info(f"response: {response}")
#                     logger.info(f"{response.get('success', False)}")
                    
#                     if response.get("success", False):
#                         logger.info("Клиент есть в системе GO.")
#                         gathered['data'].setdefault('onboarding', {})['onboarding_status'] = True
#                     else:
#                         logger.info("Добавление блока - onboarding для опроса клиента перед записью в GO.")
#                         gathered['data'].setdefault('onboarding', {})['onboarding_status'] = False
#                         gathered['data'].setdefault('onboarding', {})['onboarding_stage'] = 0

#             # filters = {
#             #     "OR":[{"user_id": user_companychat}, {"agent_id": "assistent"}],
#             #     "AND": [{"run_id": "test"},]
#             # }
#             # memory_list = await memory.get_all(filters=filters, version="v2")
    
#             # context = "Relevant information from previous conversations:\n"
#             # for mem in memory_list:
#             #     context += f"- {mem['memory']}\n"
#             # print(f"\nmemories: {context}")

#             # responce_mem = await memory.add(
#             #     messages=[{"role": "user", "content":query}],
#             #     user_id=str(user_companychat),
#             #     run_id="test",
#             #     infer=True,
#             #     version="v2",
#             #     output_format="v1.1"
#             # )
#             # logger.info(f"responce_mem: {responce_mem}")

#             return{
#                 **gathered,
#             }
#         except Exception as err:
#             logger.exception(f"GetDatabaseMiddleware: {err}")
#             return {
#                 "messages": [AIMessage(content='Бот временно не работает')],
#                 "jump_to": "end"
#             }


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
            logger.info("===GetCRMGOMiddleware===")

            data = state.get("data", {})
            phone = data.get("phone")
            mcp_port = data.get("mcp_port")

            # Ранний возврат для нецелевого порта
            if mcp_port not in self.ALLOWED_PORT:
                data.setdefault("onboarding", {}).setdefault("onboarding", True)
                return {"data": data}
            
            if not state.get("data", {}).get('onboarding'):
                # Получаем и обрабатываем данные CRM
                logger.info(f"fetch_crm_go_client_info")
                raw_onboarding = await fetch_crm_go_client_info(phone=phone)
                data["onboarding"] = raw_onboarding

            logger.info(f"onboarding: {data['onboarding']}")

            return {"data": data}

        except Exception as err:
            logger.exception(f"GetCRMGOMiddleware: {err}")
            return {
                "messages": [AIMessage(content='Бот временно не работает')],
                "jump_to": "end"
            } 

# class DynamicMCPPortMiddleware(AgentMiddleware):
#     async def abefore_agent(self, state: State, runtime: Runtime[Context]) -> dict | None:
#         logger.info("===before_agent===DynamicMCPPortMiddleware===")

#         mcp_port = state.get("data", {}).get("mcp_port")  # Default to 4007 if not in context
#         tools = await get_tools(mcp_port=mcp_port)  # Fetch tools dynamically based on port

#         logger.info(f"mcp_port: {mcp_port}")
#         logger.info(f"mcp_tools: {[tool.name for tool in tools]}")

#         return {
#             "tools": tools  # Update state with dynamically fetched tools
#         }