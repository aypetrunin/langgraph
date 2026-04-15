"""Middleware, выполняемые после вызова LLM-модели.

GetCRMGOOnboardStage — управляет этапом онбординга для агента Алена (порт 5020):
    инкрементирует onboarding_stage, если клиент ещё не прошёл онбординг.
GetToolArgs — извлекает аргументы вызовов инструментов из AIMessage
    и сохраняет их в state['tools_args'] для логирования.
GetCountToken — подсчитывает токены (input/output/total) из usage_metadata
    и накапливает их в state['tokens'].
"""

from typing import Any, Mapping, cast

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, AnyMessage, BaseMessage
from langgraph.runtime import Runtime

from .zena_logging import get_logger
from .zena_state import Context, State

logger = get_logger()


class GetCRMGOOnboardStage(AgentMiddleware):
    """Инкрементирует этап онбординга для агента Алена (порт 5020)."""

    async def aafter_model(
        self,
        state: State,
        runtime: Runtime[Context],
    ) -> dict[str, Any] | None:
        """Инкрементирует onboarding_stage после ответа модели."""
        logger.info("middleware.started", middleware="GetCRMGOOnboardStage")

        data = state.get("data", {})

        
        if data.get("mcp_port") != 5020:
            return None

        messages: list[AnyMessage] | None = state.get("messages")

        if not messages:
            return None
        
        last_message = messages[-1]

        if not isinstance(last_message, AIMessage):
            return None

        tool_calls: list[Any] | None = getattr(last_message, "tool_calls", None)
        logger.debug("model.tool_calls", tool_calls=tool_calls)
        if tool_calls:
            return None

        onboarding = data.get("onboarding", {}).get("onboarding_status")

        if onboarding is None or onboarding:
            return None

        onboarding_stage = data.get("onboarding").get("onboarding_stage", 0)
        if onboarding_stage < 6:
            data["onboarding"]["onboarding_stage"] += 1
 

        return {
            "data": data
        }


class GetToolArgs(AgentMiddleware):
    """Сохраняет аргументы, передаваемые в инструмент."""

    async def aafter_model(
        self,
        state: State,
        runtime: Runtime[Context],
    ) -> dict[str, Any] | None:
        """Извлекает аргументы вызовов инструментов из последнего AIMessage."""
        logger.info("middleware.started", middleware="GetToolArgs")

        messages: list[AnyMessage] | None = state.get("messages")
        if not messages:
            return None

        last_message = messages[-1]
        if not isinstance(last_message, AIMessage):
            return None

        tool_calls: list[Any] | None = getattr(last_message, "tool_calls", None)
        if not tool_calls:
            return None

        tools_args: list[dict[str, Any]] = [
            {k: v for k, v in cast(dict[str, Any], tool["args"]).items() if k != "session_id"}
            for tool in tool_calls
        ]

        logger.debug("tool.args_extracted", tools_args=tools_args)

        return {
            "tools_args": tools_args
        }


class GetCountToken(AgentMiddleware):
    """Подсчёт токенов по последнему сообщению и сохранение в state['tokens']."""

    async def aafter_model(
        self,
        state: State,
        runtime: Runtime[Context],
    ) -> dict[str, Any] | None:
        """Подсчитывает токены из usage_metadata и накапливает их в state."""
        logger.info("middleware.started", middleware="GetCountToken")

        messages: list[BaseMessage] | None = cast(
            list[BaseMessage] | None, state.get("messages")
        )
        if not messages:
            return None

        msg: BaseMessage = messages[-1]

        usage: dict[str, Any] | None = self._extract_usage(msg)
        if not usage:
            return None

        tokens_update: dict[str, int] = self._calculate_tokens_update(state, state.get("tokens", {}), usage)

        logger.info("tokens.counted", **tokens_update)

        return {
            "tokens": tokens_update
        }


    def _extract_usage(self, msg: BaseMessage) -> dict[str, Any] | None:
        # Проверяем usage_metadata
        usage_metadata = getattr(msg, "usage_metadata", None)
        if isinstance(usage_metadata, dict):
            return cast(dict[str, Any], usage_metadata or {})

        # Проверяем response_metadata.token_usage
        response_metadata = getattr(msg, "response_metadata", None)
        if isinstance(response_metadata, dict):
            token_usage = cast(dict[str, Any], response_metadata.get("token_usage") or {})
            if isinstance(token_usage, dict):
                return {
                    "input_tokens": self._get_token_count(token_usage, ["prompt_tokens", "input_tokens"]),
                    "output_tokens": self._get_token_count(token_usage, ["completion_tokens", "output_tokens"]),
                    "total_tokens": token_usage.get("total_tokens", 0),
                }

        return None

    def _get_token_count(self, token_usage: Mapping[str, Any], keys: list[str]) -> int:
        for key in keys:
            value = token_usage.get(key)
            if isinstance(value, (int, float)):
                return int(value)
        return 0

    def _calculate_tokens_update(
        self,
        state: State,
        data: Mapping[str, Any],
        usage: dict[str, Any],
    ) -> dict[str, int]:
        inp: int = int(usage.get("input_tokens", 0) or 0)
        out: int = int(usage.get("output_tokens", 0) or 0)
        tot: int = int(usage.get("total_tokens", 0) or (inp + out))

        current_tokens: dict[str, int] = self._get_current_tokens(state, data)
        
        return {
            "input_tokens": current_tokens["input_tokens"] + inp,
            "output_tokens": current_tokens["output_tokens"] + out,
            "total_tokens": current_tokens["total_tokens"] + tot,
        }

    def _get_current_tokens(
        self,
        state: State,
        data: Mapping[str, Any],
    ) -> dict[str, int]:
        current_tokens_raw = state.get("tokens")
        if isinstance(current_tokens_raw, dict):
            return {
                "input_tokens": int(current_tokens_raw.get("input_tokens", 0) or 0),
                "output_tokens": int(current_tokens_raw.get("output_tokens", 0) or 0),
                "total_tokens": int(current_tokens_raw.get("total_tokens", 0) or 0),
            }
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}