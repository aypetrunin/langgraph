"""Middleware, выполняемые перед вызовом LLM-модели.

TrimMessages — ограничивает количество сообщений в контексте модели,
чтобы не превышать лимит контекстного окна. Сохраняет первое сообщение
(системный промпт) и последние N сообщений.

Настройка через env MAX_MESSAGES_HISTORY (по умолчанию 20).
"""

import os
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.messages import RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime

from .zena_common import logger
from .zena_state import Context, State


class TrimMessages(AgentMiddleware):
    """Ограничение количества сообщений перед отправкой в LLM."""

    async def abefore_model(
            self,
            state: State,
            runtime: Runtime[Context],
    ) -> dict[str, Any] | None:
        """Ограничение количества сообщений для модели."""
        logger.info("===before_model===TrimMessages===")

        MAX_COUNT_MESSAGES = int(os.getenv("MAX_MESSAGES_HISTORY", "20"))


        # Проверка на не пустой список диалога.
        messages = state.get("messages")
        if not messages:
            return None

        logger.info("Количество сообщений: %d. Максимум: %d", len(messages), MAX_COUNT_MESSAGES)

        if len(messages) <= MAX_COUNT_MESSAGES:
            return None

        first_msg = messages[0]
        recent_messages = messages[-MAX_COUNT_MESSAGES:] if len(messages) % 2 == 0 else messages[-MAX_COUNT_MESSAGES-1:]
        new_messages = [first_msg] + recent_messages

        logger.info("Количество сообщений обрезано до : %d шт.", MAX_COUNT_MESSAGES)

        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *new_messages
            ]
        }
