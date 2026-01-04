import httpx
import os
from typing import Any
from jinja2 import Template
from pathlib import Path
import aiofiles

from langchain.chat_models import init_chat_model
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langchain.agents.middleware import (
    AgentMiddleware,
    TodoListMiddleware,
    LLMToolSelectorMiddleware,
    ContextEditingMiddleware,
    ClearToolUsesEdit,
    wrap_model_call,
    dynamic_prompt,
    ModelRequest,
    ModelResponse,
    PIIMiddleware,
)


from .zena_agent_node import (
    verification_message,
    data_collection,
)

from dotenv import load_dotenv

from .zena_state import Context, InputState, OutputState, State


load_dotenv()

openai_proxy = os.getenv("OPENAI_PROXY_URL")
openai_model = os.getenv("OPENAI_MODEL")
openai_api_key = os.getenv("OPENAI_API_KEY")
http_client = httpx.AsyncClient(proxy=openai_proxy, timeout=60.0)


model = init_chat_model(
    model=openai_model,
    temperature=0,
    api_key=openai_api_key,
    http_async_client=http_client,
)

class CustomMiddleware(AgentMiddleware):
    state_schema = State

    def before_model(self, state: State, runtime) -> dict[str, Any] | None:
        print("==before_model==")
        # print(state)
        return None


@wrap_model_call
async def personalized_prompt_1(request: ModelRequest, handler) -> ModelResponse:
    """Формирование промпта."""

    tpl_system_prompt = request.state["data"]["template_prompt_system"]
    tpl_path = Path(__file__).parent / "template" / tpl_system_prompt

    async with aiofiles.open(tpl_path, encoding="utf-8") as f:
        source = await f.read()
 
    request.system_prompt = Template(source).render(**request.state["data"])

    return await handler(request)


@dynamic_prompt
async def personalized_prompt_2(request: ModelRequest) -> str:
    """Формирование промпта."""

    tpl_system_prompt = request.state["data"]["template_prompt_system"]
    tpl_path = Path(__file__).parent / "template" / tpl_system_prompt

    async with aiofiles.open(tpl_path, encoding="utf-8") as f:
        source = await f.read()
 
    system_prompt = Template(source).render(**request.state["data"])

    return system_prompt


agent = create_agent( 
    model=model,
    # state_schema=CustomState,
    # context_schema=Context,
    system_prompt="Ты полезный помошник",
    middleware=[
        CustomMiddleware(),
        # TodoListMiddleware(),
        personalized_prompt_2,
        # LLMToolSelectorMiddleware(
        #     model="gpt-4o-mini",  # Use cheaper model for selection
        #     max_tools=3,  # Limit to 3 most relevant tools
        #     always_include=["search"],  # Always include certain tools
        # ),
        # Redact emails in user input
        # PIIMiddleware("email", strategy="block", apply_to_input=True),
    ]
)

workflow = StateGraph(
    state_schema=State,
    input_schema=InputState,
    output_schema=OutputState,
    context_schema=Context,
)

workflow.add_node("verification_message", verification_message)
workflow.add_node("data_collection", data_collection)
workflow.add_node("agent", agent)

workflow.add_edge(START, "verification_message")
workflow.add_edge("verification_message", "data_collection")
workflow.add_edge("data_collection", "agent")
workflow.add_edge("agent", END)

deepagent = workflow.compile()