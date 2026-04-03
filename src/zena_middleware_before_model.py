from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import BaseMessage, SystemMessage, AIMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES, RemoveMessage

from .zena_common import logger

class TrimMessages(AgentMiddleware):
    """Ограничение количества диалоговых сообщений без разрыва tool-блоков."""

    MAX_DIALOG_MESSAGES = 30

    async def abefore_model(
        self,
        state,
        runtime,
    ) -> dict[str, Any] | None:
        logger.info("=== before_model === TrimMessages ===")

        messages = state.get("messages")
        if not messages:
            return None

        system_messages = [msg for msg in messages if isinstance(msg, SystemMessage)]
        dialog_messages = [msg for msg in messages if not isinstance(msg, SystemMessage)]

        logger.info(
            "Всего сообщений: %s, system: %s, dialog: %s, лимит dialog: %s",
            len(messages),
            len(system_messages),
            len(dialog_messages),
            self.MAX_DIALOG_MESSAGES,
        )

        if len(dialog_messages) <= self.MAX_DIALOG_MESSAGES:
            logger.info("Обрезка не требуется")
            return None

        trimmed_dialog = self._trim_preserving_tool_pairs(
            dialog_messages,
            limit=self.MAX_DIALOG_MESSAGES,
        )

        new_messages = system_messages + trimmed_dialog

        self._validate_message_sequence(new_messages)

        logger.info(
            "После обрезки сообщений: всего=%s, system=%s, dialog=%s",
            len(new_messages),
            len(system_messages),
            len(trimmed_dialog),
        )

        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *new_messages,
            ]
        }

    def _trim_preserving_tool_pairs(
        self,
        messages: list[BaseMessage],
        limit: int,
    ) -> list[BaseMessage]:
        """
        Обрезает историю с конца, не разрывая блоки:
        AIMessage(tool_calls) + идущие после него ToolMessage.
        """
        if limit <= 0:
            return []

        if len(messages) <= limit:
            return messages

        kept_reversed: list[BaseMessage] = []
        i = len(messages) - 1

        while i >= 0:
            msg = messages[i]

            # ToolMessage должен идти вместе с предшествующим AIMessage(tool_calls)
            if isinstance(msg, ToolMessage):
                block_reversed: list[BaseMessage] = []

                # Собираем подряд идущие ToolMessage справа налево
                while i >= 0 and isinstance(messages[i], ToolMessage):
                    block_reversed.append(messages[i])
                    i -= 1

                # Перед ними должен быть AIMessage с tool_calls
                if (
                    i >= 0
                    and isinstance(messages[i], AIMessage)
                    and getattr(messages[i], "tool_calls", None)
                ):
                    block_reversed.append(messages[i])
                    i -= 1
                else:
                    logger.warning(
                        "Найден ToolMessage без предшествующего AIMessage(tool_calls). "
                        "Битый хвост отброшен."
                    )
                    break

                if len(kept_reversed) + len(block_reversed) > limit:
                    logger.info(
                        "Очередной tool-блок не помещается в лимит. "
                        "Останавливаем обрезку."
                    )
                    break

                kept_reversed.extend(block_reversed)
                continue

            # Нельзя оставлять AIMessage(tool_calls) без ToolMessage
            if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                logger.warning(
                    "Найден AIMessage(tool_calls) без связанных ToolMessage справа. "
                    "Сообщение пропущено."
                )
                i -= 1
                continue

            # Обычное сообщение
            if len(kept_reversed) + 1 > limit:
                break

            kept_reversed.append(msg)
            i -= 1

        kept = list(reversed(kept_reversed))
        return kept

    def _validate_message_sequence(self, messages: list[BaseMessage]) -> None:
        """
        Проверяет, что каждый ToolMessage имеет соответствующий
        предыдущий AIMessage(tool_calls).
        """
        known_tool_call_ids = set()

        for idx, msg in enumerate(messages):
            if isinstance(msg, AIMessage):
                tool_calls = getattr(msg, "tool_calls", None) or []
                for tool_call in tool_calls:
                    tool_call_id = tool_call.get("id")
                    if tool_call_id:
                        known_tool_call_ids.add(tool_call_id)

            elif isinstance(msg, ToolMessage):
                tool_call_id = getattr(msg, "tool_call_id", None)
                if not tool_call_id or tool_call_id not in known_tool_call_ids:
                    raise ValueError(
                        f"Некорректная история сообщений: ToolMessage на позиции {idx} "
                        f"не имеет соответствующего AIMessage(tool_calls). "
                        f"tool_call_id={tool_call_id}"
                    )


# from typing import Any

# from langgraph.runtime import Runtime
# from langchain.agents.middleware import AgentMiddleware
# from langchain.messages import RemoveMessage
# from langgraph.graph.message import REMOVE_ALL_MESSAGES

# from .zena_common import logger
# from .zena_state import State, Context


# class TrimMessages(AgentMiddleware):
#     """Ограничение количества сообщений для модели."""

#     async def abefore_model(
#             self,
#             state: State,
#             runtime: Runtime[Context],
#     ) -> dict[str, Any] | None:
#         """Ограничение количества сообщений для модели."""

#         logger.info("===before_model===TrimMessages===")

#         MAX_COUNT_MASSAGES = 50


#         # Проверка на не пустой список диалога.
#         messages = state.get("messages")
#         if not messages:
#             return None

#         logger.info(f"Количество сообщений: {len(messages)}. Максимум: {MAX_COUNT_MASSAGES}")

#         if len(messages) <= MAX_COUNT_MASSAGES:
#             return None

#         first_msg = messages[0]
#         recent_messages = messages[-MAX_COUNT_MASSAGES:] if len(messages) % 2 == 0 else messages[-MAX_COUNT_MASSAGES-1:]
#         new_messages = [first_msg] + recent_messages

#         logger.info(f"Количество сообщений обрезано до : {MAX_COUNT_MASSAGES} шт.")

#         return {
#             "messages": [
#                 RemoveMessage(id=REMOVE_ALL_MESSAGES),
#                 *new_messages
#             ]
#         }
