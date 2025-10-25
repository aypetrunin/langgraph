from __future__ import annotations

from typing_extensions import TypedDict, List
from typing_extensions import Annotated
from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages
from operator import add


class Context(TypedDict):
    _user_companychat: int
    _reply_to_history_id: int
    _access_token: str
    _user_id: int


class InputState(TypedDict, total=True):
    messages: Annotated[List[AnyMessage], add_messages]


class OutputState(TypedDict, total=False):
    messages: Annotated[List[AnyMessage], add_messages]
    time_node: Annotated[List[dict], add]
    time_all: Annotated[float, add]
    tokens: dict
    tools_name: Annotated[List[str], add]
    tools_args: Annotated[List[dict], add]
    tools_results: Annotated[List[dict], add]
    prompt_system: str
    template_prompt_system: str
    dialog_state: str
    dialog_state_new: str
    query: str


class PrivateState(TypedDict, total=False):
    data: dict
    tools: list 
    user_companychat: int


class State(InputState, OutputState, PrivateState, total=False):  # type: ignore[misc]
    pass