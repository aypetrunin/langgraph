import json

from typing import Any

from langgraph.runtime import Runtime
from langchain.agents.middleware import AgentMiddleware
from langchain.messages import RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from .zena_common import logger, _content_to_text
from .zena_state import State, Context


def parse_item(item: dict) -> dict:
    """Приведение к одному виду описания услуг."""
    # Используйте понимание словаря с безопасным получением и переходом к None, если ключ отсутствует.
    keys_map = {
        "item_id": "product_id",
        "item_name": "product_name",
        "item_duration": "duration", 
        "item_price": "price",
    }
    return {key: item.get(src_key) for key, src_key in keys_map.items() if item.get(src_key) is not None}


class SaveResultToolsMiddleware(AgentMiddleware):
    """Реализовано изменение статуса диалога, через вызов определенных инструментов."""

    async def abefore_model(
            self,
            state: State,
            runtime: Runtime[Context],
    ) -> dict[str, Any] | None:
        """Изменение статуса диалога.
        
        Изменение статуса диалога в зависимости от используемого инструмента.
        Примечание. В этом событии в последнем сообщении могут быть только сообщения
        типа HumanMessage и ToolMessage. В сообщении ToolMessage принимаем результат
        работы Tool. 
        """

        logger.info("===before_model===SaveResultToolsMiddleware===")

        # Проверка на не пустой список диалога.
        messages = state.get("messages")
        if not messages:
            return None

        # Обрабатываем только сообщения типа ToolMessage
        last_message = messages[-1]
        if type(last_message).__name__ != 'ToolMessage':
            logger.info("Инструмент не выполнялся.")
            return None

        data = state['data']

        dialog_state = data.get('dialog_state')
        tool_name = getattr(last_message, "name", "")

        raw_content = _content_to_text(getattr(last_message, "content", ""))
        logger.info(f"raw_content: {raw_content}")

        try:
            tools_result = json.loads(raw_content) if raw_content.strip() else ""
        except (json.JSONDecodeError, TypeError):
            # НЕ JSON — оставляем как есть, но приводим к списку
            tools_result = raw_content if raw_content else ""

        logger.info(f"\ndialog_state_old: {dialog_state}")
        logger.info(f"\ntool_name: {tool_name}")
        logger.info(f"\ntools_result: {tools_result}") 

        if data['mcp_port'] in [4001, 5001, 4002, 5002, 4005, 5005, 4006, 5006, 4007, 5007]:

            if tool_name == "zena_product_search_":
                if tools_result:
                    data.setdefault('items_search', [])

                    # текущие id в накопленном списке
                    existing_ids = {
                        item['item_id']
                        for item in data['items_search']
                        if 'item_id' in item
                    }
                    logger.info(f"existing_ids: {existing_ids}")
                    # новые элементы без дублей
                    new_items = []
                    for raw_item in tools_result:
                        item = parse_item(raw_item)
                        if item['item_id'] not in existing_ids:
                            new_items.append(item)
                            existing_ids.add(item['item_id'])
                    
                    logger.info(f"new_items: {new_items}")
                    # аккуратно накапливаем
                    data['items_search'].extend(new_items)

                    data['dialog_state'] = "selecting"


            elif tool_name in ["zena_remember_product_id_", "zena_remember_product_id_list_"]:
                if tools_result:
                    items = [parse_item(item) for item in tools_result]
                    data['item_selected'] = items
                    data['dialog_state'] = "remember"

            elif tool_name in ["zena_available_time_for_master_list_"]:
                if tools_result:
                    data.setdefault('available_time', [])
                    data.setdefault('available_sequences', [])
                    data['available_time'].append(tools_result[0])
                    data['available_sequences'].append(tools_result[1])
                    data['dialog_state'] = "available_time"

            elif tool_name in ["zena_avaliable_time_for_master_"]:
                if tools_result:
                    data.setdefault('available_time', [])
                    data['available_time'].append(tools_result)
                    data['dialog_state'] = "available_time"

            elif tool_name == "zena_record_time_":
                if tools_result and tools_result.get("success"):
                    data['dialog_state'] = "postrecord"

            elif tool_name == "zena_recommendations_": 
                data.update({
                    "dialog_state": "new",
                    "items_search": [],
                    "item_selected": [],
                    "available_time": [],
                    "available_sequences": [],
                })

        elif data['mcp_port'] in [5020]: 

            if tool_name == "zena_get_client_lessons":
                if tools_result and tools_result.get("success"):
                    data['dialog_state'] = "selecting"
                    data['items_search'] = tools_result
            
            if tool_name == "zena_remember_lesson_id":
                data['dialog_state'] = "remember"
                data["item_selected"] = tools_result

            if tool_name == "zena_update_client_lesson":
                if isinstance(tools_result, list):
                    tools_result = tools_result[0] if tools_result else {}

                if tools_result and tools_result.get("success"):
                    data["dialog_state"] = "new"
            
            if tool_name == "zena_update_client_info":
                if isinstance(tools_result, list):
                    tools_result = tools_result[0] if tools_result else {}

                if tools_result and tools_result.get("success"):
                    data["onboarding"]["onboarding_status"] = True

        logger.info(f"\ndialog_state_new: {data['dialog_state']}")
        return {
            "data": data,
            # "tools_name": [tool_name],
            # "tools_result": [tools_result],
        }
    

class TrimMessages(AgentMiddleware):
    """Ограничение количества сообщений для модели."""

    async def abefore_model(
            self,
            state: State,
            runtime: Runtime[Context],
    ) -> dict[str, Any] | None:
        """Ограничение количества сообщений для модели."""

        logger.info("===before_model===TrimMessages===")

        MAX_COUNT_MASSAGES = 20


        # Проверка на не пустой список диалога.
        messages = state.get("messages")
        if not messages:
            return None

        logger.info(f"Количество сообщений: {len(messages)}. Максимум: {MAX_COUNT_MASSAGES}")

        if len(messages) <= MAX_COUNT_MASSAGES:
            return None

        first_msg = messages[0]
        recent_messages = messages[-MAX_COUNT_MASSAGES:] if len(messages) % 2 == 0 else messages[-MAX_COUNT_MASSAGES-1:]
        new_messages = [first_msg] + recent_messages

        logger.info(f"Количество сообщений обрезано до : {MAX_COUNT_MASSAGES} шт.")

        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *new_messages
            ]
        }
