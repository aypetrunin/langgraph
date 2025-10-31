"""Модуль описывающий состояние графа."""

from __future__ import annotations

from operator import add
from typing import Any

from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages
from typing_extensions import Annotated, List, TypedDict


class Context(TypedDict):
    """Переменные контекста выполнения."""

    _user_companychat: int
    _reply_to_history_id: int
    _access_token: str
    _user_id: int


class InputState(TypedDict, total=True):
    """Входная переменная."""

    messages: Annotated[List[AnyMessage], add_messages]


class OutputState(TypedDict, total=False):
    """Выходные переменные."""

    messages: Annotated[List[AnyMessage], add_messages]
    time_node: Annotated[List[dict[str, Any]], add]
    time_all: Annotated[float, add]
    tokens: dict[str, Any]
    tools_name: Annotated[List[str], add]
    tools_args: Annotated[List[dict[str, Any]], add]
    tools_results: Annotated[List[dict[str, Any]], add]
    prompt_system: str
    template_prompt_system: str
    dialog_state: str
    dialog_state_new: str
    query: str


class PrivateState(TypedDict, total=False):
    """Приватные переменные."""

    data: dict[str, Any]
    tools: list[str]
    user_companychat: int


class State(InputState, OutputState, PrivateState, total=False):  # type: ignore[misc]
    """Состояние графа."""

    pass
