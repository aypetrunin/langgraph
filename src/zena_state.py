"""Схема состояния графа агента.

Определяет TypedDict-схемы для всех слоёв состояния LangGraph:
- Context      — переменные окружения (user_id, access_token и др.)
- InputState   — входные данные (messages + data)
- OutputState  — выходные данные (messages + data)
- PrivateState — внутренние переменные (tools_name, tools_args, tokens)
- State        — объединённое состояние графа

Также содержит RESET-сентинел и кастомный reducer add_tools_or_reset,
который позволяет накапливать данные инструментов и сбрасывать их
между итерациями агента.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages
from typing_extensions import Annotated, TypedDict

# Сентинел для сброса аккумулируемых полей (tools_name, tools_args и т.д.)
RESET = object()


def add_tools_or_reset(current: list[Any] | None, update: Any) -> list[Any]:
    """Reducer для аккумуляции данных инструментов с возможностью сброса.

    Используется как аннотация Annotated[list, add_tools_or_reset].
    При получении RESET — возвращает пустой список.
    При получении значения — добавляет к текущему списку.
    """
    if current is None:
        current = []

    if update is RESET:
        return []

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
