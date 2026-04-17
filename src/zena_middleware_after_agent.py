"""Middleware, выполняемые после завершения работы агента.

SaveResponseAgent — отправляет финальный ответ агента в httpservice для
    сохранения в историю диалога и аналитику.
ResetData — сбрасывает аккумулируемые поля state (tools_args, tools_name,
    tools_result, tokens) перед следующей итерацией.
"""

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

from .zena_common import _content_to_text
from .zena_httpservice import sent_message_to_history
from .zena_logging import bind_request_ctx, get_logger, log_graph_total
from .zena_state import RESET, Context, State

logger = get_logger()


class SaveResponseAgent(AgentMiddleware):
    """Сохраняет ответ агента через httpservice API."""

    async def aafter_agent(
        self,
        state: State,
        runtime: Runtime[Context],
    ) -> dict[str, Any] | None:
        """Отправляет текст ответа, использованные инструменты и токены в httpservice."""
        bind_request_ctx(runtime)
        logger.info("middleware.started", middleware="SaveResponseAgent")

        try:
            data = state.get("data", {})
            text = _content_to_text(state["messages"][-1].content)
            ctx = runtime.context

            # Определяем user_id и access_token в зависимости от источника запуска:
            # - из приложения: данные приходят через runtime.context
            # - из LangGraph Studio: данные берутся из state.data
            user_id = ctx.get("_user_id") or int(data.get("user_id"))
            access_token = (
                ctx.get("_access_token")
                or data.get("session_id", "").split("-", 1)[1]
            )
            reply_to_history_id = (
                ctx.get("_reply_to_history_id", 11050)
                if text != "Память очищена"
                else 11050
            )

            payload = {
                "user_id": user_id,
                "text": text,
                "access_token": access_token,
                "user_companychat": state.get("user_companychat"),
                "reply_to_history_id": reply_to_history_id,
                "tools": state.get("tools_result", []),
                "tools_args": None,
                "tools_result": None,
                "tokens": state.get("tokens", {}),
                "prompt_system": data.get("prompt_system", ""),
                "dialog_state": data.get("dialog_state_in", ""),
                "dialog_state_new": data.get("dialog_state", ""),
                "template_prompt_system": data.get("template_prompt_system", ""),
            }

            response = await sent_message_to_history(**payload)

            if response.get("status", "not") == "ok":
                logger.info("response.saved", success=True)
            else:
                logger.error("response.saved", success=False)

            return None

        except Exception:
            logger.exception("middleware.error", middleware="SaveResponseAgent")
            return None


class ResetData(AgentMiddleware):
    """Сброс аккумулируемых данных после ответа агента.

    Очищает tools_args, tools_name, tools_result (через RESET-сентинел)
    и обнуляет счётчики токенов для следующей итерации.
    """

    async def aafter_agent(
        self,
        state: State,
        runtime: Runtime[Context],
    ) -> dict[str, Any] | None:
        """Возвращает RESET для списковых полей и нули для токенов."""
        bind_request_ctx(runtime)
        logger.info("middleware.started", middleware="ResetData")
        log_graph_total()

        return {
            "tools_args": RESET,
            "tools_name": RESET,
            "tools_result": RESET,
            "tokens": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            },
        }