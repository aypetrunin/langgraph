import aiofiles

from pathlib import Path
from typing import Callable
from jinja2 import Template

from langchain_core.tools.structured import StructuredTool
from langchain_core.language_models.chat_models import BaseChatModel

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    dynamic_prompt,
)

from .zena_common import logger, model_4o, model_4o_mini
from .zena_state import State, Context


class DynamicSystemPrompt(AgentMiddleware):
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        logger.info("==awrap_model_call==DynamicSystemPrompt==")
        
        tpl_system_prompt = request.state["data"]["template_prompt_system"]
        tpl_path = Path(__file__).parent / "template" / tpl_system_prompt

        async with aiofiles.open(tpl_path, encoding="utf-8") as f:
            source = await f.read()
        logger.info(f"request.state: {request.state}\n")
        data = request.state.get("data", {})
        system_prompt = Template(source).render(**data)
        data["prompt_system"] = "" # system_prompt
        logger.info(f"dialog_state:{data['dialog_state']}\n")
        logger.info(f"system_prompt:\n{system_prompt[:]}\n")
        return await handler(request.override(system_prompt=system_prompt))


class ToolSelectorMiddleware(AgentMiddleware):
    """Middleware для выбора релевантных инструментов по состоянию диалога."""

    async def _select_relevant_tools(
            self,
            state: State, 
            tools: list[StructuredTool]
    ) -> list[StructuredTool]:
        """Выбора релевантных инструментов по состоянию диалога."""
        
        logger.info("===wrap_model_call===_select_relevant_tools===")

        data = state.get("data", {}) 
        mcp_port = data.get("mcp_port")
        dialog_state = data.get("dialog_state")

        logger.info(f"dialog_state: {dialog_state}")
        logger.info(f"mcp_port: {mcp_port}")

        print([tool.name for tool in tools])


        # Подключение инструментов ы зависисмости от стадии диалога.
        if mcp_port in [4007, 5007]:

            allowed = {"zena_faq", "zena_services"}

            if dialog_state in ["new"]:
                print('dialog_state in ["new"]')
                allowed.update({"zena_record_product_id_list"})

            elif dialog_state in ["remember"]:
                allowed.update({"zena_remember_product_id_list"})
                allowed.update({"zena_avaliable_time_for_master_list"})

            elif dialog_state in ["available_time"]:
                allowed.update({"zena_remember_product_id_list"})
                allowed.update({"zena_avaliable_time_for_master_list"})
                allowed.update({"zena_record_time"})
            
            elif dialog_state in ["postrecord"]:
                allowed.update({"zena_recommendations"})
        
        elif mcp_port in [4001, 5001, 4002, 5002, 4005, 5005, 4006, 5006,]:
            
            allowed = {"zena_faq", "zena_services", "zena_product_search"}

            if dialog_state not in ["new"]:
                allowed.update({"zena_remember_product_id"})

            if dialog_state not in ["new", "selecting"]:
                allowed.update({"zena_avaliable_time_for_master"})
                allowed.update({"zena_record_time"})
        
        elif mcp_port in [5020]:
            
            allowed = {"zena_faq"}

            phone = data.get('phone')
            onboarding_stage = data.get("onboarding", {}).get("onboarding_stage")
            onboarding_status = data.get("onboarding", {}).get("onboarding_status")

            # Если обычный режим диалога
            if (onboarding_status is None or onboarding_status) and phone != '':

                allowed.add("zena_get_client_statistics")

                if dialog_state == "new":
                    allowed.add("zena_get_client_lessons")

                if dialog_state == "selecting":
                    allowed.add("zena_remember_lesson_id")

                if dialog_state == "remember":
                    allowed.add("zena_update_client_lesson")

            # Если режим - опроса.
            else:
                if onboarding_stage and onboarding_stage >= 5:
                    allowed.add("zena_update_client_info")

        logger.info(f"tools: {[tool.name for tool in tools if tool.name in allowed]}")
        return [tool for tool in tools if tool.name in allowed]


    async def _select_model (self, state: State) -> BaseChatModel:
        """Выбор модели под конкретную стадию диалога."""

        logger.info("===wrap_model_call===_select_model===")

        data = state.get("data", {}) 
        mcp_port = data.get("mcp_port")
        dialog_state = data.get("dialog_state")

        if mcp_port in [4007, 5007]:
            model = model_4o if dialog_state in ["new", "available_time"] else model_4o_mini
            logger.info(f"dialog_state: {dialog_state}")
            logger.info(f"Выбрана модель: {model}")
            return model
        elif mcp_port in [5020]:
             return model_4o_mini
        elif mcp_port in [5002]:
            model = model_4o if dialog_state in ["remember"] else model_4o_mini
            logger.info(f"dialog_state: {dialog_state}")
            logger.info(f"Выбрана модель: {model}")
            return model
        else:
            return model_4o_mini


    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """Middleware для выбора релевантных инструментов и модели."""

        logger.info("===wrap_model_call===ToolSelectorMiddleware===")

        request.tools = await self._select_relevant_tools(request.state, request.tools)
        request.model = await self._select_model(request.state)
        
        return await handler(request)



@dynamic_prompt
async def personalized_prompt(request: ModelRequest) -> str:
    """Формирование промпта."""

    logger.info("==dynamic_prompt==")
    # logger.info(f'state: {request.state}')
    # logger.info(f'state: {request.state["data"]}')
    tpl_system_prompt = request.state["data"]["template_prompt_system"]
    # tpl_system_prompt = 'prompt_agent_of_service_selection_v1.md'
    tpl_path = Path(__file__).parent / "template" / tpl_system_prompt

    async with aiofiles.open(tpl_path, encoding="utf-8") as f:
        source = await f.read()
    data = request.state.get("data", {})
    # data['item_selected'] = request.state.get("item_selected", [])
    # logger.info(f'\nstate: {request.state}')
    system_prompt = Template(source).render(**data)
    # system_prompt = Template(source).render(**request.state.get("data", {}))
    logger.info(f"system_prompt:\n{system_prompt}")

    return system_prompt