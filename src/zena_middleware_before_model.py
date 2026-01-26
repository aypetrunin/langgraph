from typing import Any

from langgraph.runtime import Runtime
from langchain.agents.middleware import AgentMiddleware
from langchain.messages import RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from .zena_common import logger
from .zena_state import State, Context


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
