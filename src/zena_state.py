"""Модуль описывающий состояние графа."""

from __future__ import annotations

from operator import add

from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages

from typing import Any
from typing_extensions import TypedDict, NotRequired, Required, Generic, TypeVar, Annotated

from langchain.agents.middleware.types import AgentState as BaseAgentState

ResponseT = TypeVar("ResponseT")

RESET = object()

def add_tools_or_reset(current: list[Any] | None, update: Any) -> list[Any]:
    # первый апдейт
    if current is None:
        current = []

    # пришёл сигнал на сброс
    if update is RESET:
        return []

    # если апдейт — один элемент, нормализуем к списку
    if not isinstance(update, list):
        update = [update]

    return current + update


class Context(TypedDict):
    """Переменные контекста выполнения."""
    _user_companychat: int
    _reply_to_history_id: int
    _access_token: str
    _user_id: int
    _studio: bool
    _prompt_google_url: str


class InputState(TypedDict, total=True):
    """Входная переменная."""
    messages: Annotated[list[AnyMessage], add_messages]
    data: dict[str, Any]


class OutputState(TypedDict, total=False):
    """Выходные переменные."""
    messages: Annotated[list[AnyMessage], add_messages]
    data: dict[str, Any]

class PrivateState(TypedDict, total=False):
    """Приватные переменные."""

    user_companychat: int
    # items_search: list[dict[str, Any]]
    # item_selected: list[dict[str, Any]]
    tools_name: Annotated[list[str], add_tools_or_reset]
    tools_args: Annotated[list[dict[str, Any]], add_tools_or_reset]
    tools_result: Annotated[list[dict[str, Any]], add_tools_or_reset]
    tokens: dict[str, Any]

class State(InputState, OutputState, PrivateState, total=False):  # type: ignore[misc]
    """Состояние графа."""
    pass
