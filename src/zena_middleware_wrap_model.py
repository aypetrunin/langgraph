from __future__ import annotations

from typing import Any, Callable


import aiofiles
import os
import hashlib

from pathlib import Path
from typing import Callable
from jinja2 import Template, Environment, StrictUndefined, DebugUndefined

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
from .zena_google_doc import GoogleDocTemplateReader


class DynamicSystemPrompt(AgentMiddleware):
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        logger.info("==awrap_model_call==DynamicSystemPrompt==")

        # Берём data из state, но делаем копию, чтобы избежать неожиданных сайд-эффектов
        state = request.state or {}
        data = dict(state.get("data", {}) or {})

        env_name = (os.getenv("ENV", "prod") or "prod").strip().lower()
        is_dev = env_name == "dev"

        source = await self._load_template_source(request=request, data=data, is_dev=is_dev)

        # Строгий рендеринг: если переменной нет — лучше упасть здесь, чем получить пустой prompt
        # jinja = Environment(undefined=StrictUndefined)
        jinja = Environment(undefined=DebugUndefined)
        system_prompt = jinja.from_string(source).render(**data)

        # Сохраняем отрендеренный prompt (как у вас и задумано)
        data["prompt_system"] = system_prompt
        # Важно: если дальше кто-то читает request.state["data"], обновим state тоже
        request.state["data"] = data

        # Логи: лучше не печатать весь prompt в prod
        self._log_prompt(system_prompt=system_prompt, data=data, is_dev=is_dev)

        return await handler(request.override(system_prompt=system_prompt))

    async def _load_template_source(self, request: ModelRequest, data: dict, is_dev: bool) -> str:
        """
        Правило выбора источника:
        - Если есть URL (контекст или data) — читаем Google Doc
          (в dev можно брать URL и из template_prompt_system, если он выглядит как URL)
        - Иначе читаем файл template_prompt_system из template/
        """
        doc_url = self._resolve_doc_url(request=request, data=data, is_dev=is_dev)

        if doc_url:
            reader = await GoogleDocTemplateReader.create(
                doc_url=doc_url,
                cache_ttl_sec=120,
                meta_check_ttl_sec=60,
            )
            return await reader.read_text()

        tpl_name = data.get("template_prompt_system")
        if not tpl_name:
            raise RuntimeError(
                "Missing template_prompt_system. Provide a filename (e.g. system_prompt.j2) "
                "or template_prompt_system_url/_prompt_google_url for Google Docs."
            )

        tpl_path = Path(__file__).parent / "template" / tpl_name
        if not tpl_path.exists():
            raise FileNotFoundError(f"Template file not found: {tpl_path}")

        async with aiofiles.open(tpl_path, encoding="utf-8") as f:
            return await f.read()

    def _resolve_doc_url(self, request: ModelRequest, data: dict, is_dev: bool) -> str | None:
        doc_url = None

        # 1) runtime.context — ТОЛЬКО в dev
        if is_dev:
            ctx = getattr(request.runtime, "context", None) or {}
            doc_url = ctx.get("_prompt_google_url")

        # 2) явный ключ в data — разрешён и в dev, и в prod
        if not doc_url:
            doc_url = data.get("template_prompt_system_url")

        return doc_url


    def _log_prompt(self, system_prompt: str, data: dict, is_dev: bool) -> None:
        dialog_state = data.get("dialog_state")
        logger.info("dialog_state=%r", dialog_state)

        prompt_len = len(system_prompt)
        prompt_hash = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:12]

        if is_dev:
            # В dev можно позволить себе больше, но всё равно осторожно:
            logger.info("system_prompt(len=%s, sha=%s):\n%s", prompt_len, prompt_hash, system_prompt)
        else:
            # В prod — только метаданные
            logger.info("system_prompt rendered (len=%s, sha=%s)", prompt_len, prompt_hash)


# class DynamicSystemPrompt(AgentMiddleware):
#     async def awrap_model_call(
#         self,
#         request: ModelRequest,
#         handler: Callable[[ModelRequest], ModelResponse],
#     ) -> ModelResponse:
#         logger.info("==awrap_model_call==DynamicSystemPrompt==")

#         data = request.state.get("data", {})
#         env = os.getenv("ENV", "prod").lower()

#         # --- DEV: берём шаблон из Google Docs ---
#         if env == "dev":
#             logger.info("Шаблон из Google.")
#             # В dev допускаем, что template_prompt_system может быть URL,
#             # а также поддерживаем отдельный ключ template_prompt_system_url
#             doc_url = request.runtime.context.get('_prompt_google_url')
            
#             if not doc_url:
#                 doc_url = data.get("template_prompt_system_url") or data.get("template_prompt_system")

#             if not doc_url:
#                 raise RuntimeError("Missing template_prompt_system_url (or template_prompt_system as URL) in dev")

#             reader = await GoogleDocTemplateReader.create(
#                 doc_url=doc_url,
#                 cache_ttl_sec=120,        # держим текст 60с
#                 meta_check_ttl_sec=60,   # каждые 60с проверяем изменения (etag/modifiedTime)
#             )
#             source = await reader.read_text()
#         # --- PROD: берём шаблон из файла ---
#         else:

#             doc_url = data.get("template_prompt_system_url")
            
#             if doc_url:
#                 reader = await GoogleDocTemplateReader.create(
#                     doc_url=doc_url,
#                     cache_ttl_sec=120,        # держим текст 60с
#                     meta_check_ttl_sec=60,   # каждые 60с проверяем изменения (etag/modifiedTime)
#                 )
#                 source = await reader.read_text()
#             else:
#                 tpl_system_prompt = data["template_prompt_system"]  # имя файла (например: "system_prompt.j2")
#                 tpl_path = Path(__file__).parent / "template" / tpl_system_prompt

#                 async with aiofiles.open(tpl_path, encoding="utf-8") as f:
#                     source = await f.read()

#         logger.info(f"request.state: {request.state}\n")

#         system_prompt = Template(source).render(**data)

#         # Важно: не затирать, а сохранять отрендеренный prompt
#         data["prompt_system"] = system_prompt

#         logger.info(f"dialog_state:{data.get('dialog_state')}\n")
#         logger.info(f"system_prompt:\n{system_prompt}\n")

#         return await handler(request.override(system_prompt=system_prompt))


# class DynamicSystemPrompt(AgentMiddleware):
#     async def awrap_model_call(
#         self,
#         request: ModelRequest,
#         handler: Callable[[ModelRequest], ModelResponse],
#     ) -> ModelResponse:
#         logger.info("==awrap_model_call==DynamicSystemPrompt==")
        
#         tpl_system_prompt = request.state["data"]["template_prompt_system"]
#         tpl_path = Path(__file__).parent / "template" / tpl_system_prompt

#         async with aiofiles.open(tpl_path, encoding="utf-8") as f:
#             source = await f.read()
#         logger.info(f"request.state: {request.state}\n")
#         data = request.state.get("data", {})
#         system_prompt = Template(source).render(**data)
#         data["prompt_system"] = "" # system_prompt
#         logger.info(f"dialog_state:{data['dialog_state']}\n")
#         logger.info(f"system_prompt:\n{system_prompt[:]}\n")
#         return await handler(request.override(system_prompt=system_prompt))


# tool_selector_middleware.py
# Полная версия для вставки:
# - globals: zena_faq, zena_services + remember-tools доступны всегда (state-neutral)
# - inherit_previous: инструменты предыдущих стадий доступны на текущей (classic ports)
# - guards: слоты только если есть office_id+desired_date; запись только если есть контакты+desired_time
# - postrecord override: после записи только zena_recommendations


class ToolSelectorMiddleware(AgentMiddleware):
    """Middleware для выбора релевантных инструментов по состоянию диалога + guards."""

    # ---------- FSM config ----------
    ORDER = ["new", "selecting", "remember", "available_time", "postrecord"]

    # Глобальные инструменты (доступны всегда, не меняют dialog_state)
    GLOBAL_TOOLS: set[str] = {
        "zena_faq",
        "zena_services",
        # remember tools (всегда доступны, т.к. клиент может назвать их где угодно)
        "zena_remember_office",
        "zena_remember_master",
        "zena_remember_desired_date",
        "zena_remember_desired_time",
    }

    # Stage tools для classic портов (400x/500x)
    # ВАЖНО: используй ТОЧНЫЕ имена инструментов, как они зарегистрированы.
    STAGE_TOOLS_CLASSIC: dict[str, set[str]] = {
        "new": {"zena_product_search"},
        "selecting": {
            "zena_product_search",
            "zena_remember_product_id",
            "zena_remember_product_id_list",
        },
        "remember": {
            "zena_avaliable_time_for_master",
            "zena_available_time_for_master_list",
        },
        "available_time": {
            "zena_record_time",
            "zena_avaliable_time_for_master",
            "zena_available_time_for_master_list",
        },
        "postrecord": {
            "zena_recommendations"
        },
    }

    # postrecord override: жёстко только recommendations
    POSTRECORD_OVERRIDE: set[str] = {"zena_recommendations"}

    # ---------- ports ----------
    CLASSIC_PORTS = {4001, 5001, 4002, 5002, 4005, 5005, 4006, 5006, 5021, 4021}
    PORTS_4007_5007 = {4007, 5007}
    PORT_5020 = {5020}

    # -------------------------
    # Main selection
    # -------------------------
    async def _select_relevant_tools(self, state: dict, tools: list[StructuredTool]) -> list[StructuredTool]:
        logger.info("===wrap_model_call===_select_relevant_tools===")

        data = state.get("data", {}) or {}
        mcp_port = data.get("mcp_port")
        dialog_state = (data.get("dialog_state") or "new").strip()

        logger.info(f"dialog_state: {dialog_state}")
        logger.info(f"mcp_port: {mcp_port}")
        logger.info(f"all_tools: {[t.name for t in tools]}")

        allowed = self._build_allowed_tools(mcp_port=mcp_port, dialog_state=dialog_state, data=data)

        filtered = [tool for tool in tools if tool.name in allowed]
        logger.info(f"allowed_tools: {sorted(allowed)}")
        logger.info(f"tools_filtered: {[t.name for t in filtered]}")
        return filtered

    def _build_allowed_tools(self, *, mcp_port: int | None, dialog_state: str, data: dict) -> set[str]:
        # Всегда разрешаем globals (FAQ/Services/Remember*)
        base = set(self.GLOBAL_TOOLS)

        if mcp_port is None:
            return base

        if mcp_port in self.PORT_5020:
            return self._allowed_for_5020(dialog_state, data, base)

        if mcp_port in self.PORTS_4007_5007:
            return self._allowed_for_4007_5007(dialog_state, data, base)

        if mcp_port in self.CLASSIC_PORTS:
            return self._allowed_for_classic_ports(dialog_state, data, base)

        return base

    # -------------------------
    # Classic ports
    # -------------------------
    def _allowed_for_classic_ports(self, dialog_state: str, data: dict, base: set[str]) -> set[str]:
        # postrecord override
        if dialog_state == "postrecord":
            return set(self.POSTRECORD_OVERRIDE)

        allowed = set(base)

        # inherit_previous: stage tools всех стадий <= текущей
        allowed |= self._inherited_stage_tools(dialog_state, self.STAGE_TOOLS_CLASSIC)

        # guards: фильтрация опасных инструментов
        allowed = self._apply_guards(dialog_state, allowed, data)

        return allowed

    def _inherited_stage_tools(self, dialog_state: str, stage_map: dict[str, set[str]]) -> set[str]:
        if dialog_state not in self.ORDER:
            dialog_state = "new"
        idx = self.ORDER.index(dialog_state)

        out: set[str] = set()
        for st in self.ORDER[: idx + 1]:
            out |= stage_map.get(st, set())
        return out

    def _apply_guards(self, dialog_state: str, allowed: set[str], data: dict) -> set[str]:
        """
        Guards по воронке:
          - Слоты: только если есть office_id + desired_date
          - Запись: только если есть consent + (name) + phone + email + desired_time
        """
        # 1) Слоты не даём, пока не выбран филиал и дата
        # (это актуально и на remember, и на available_time если пользователь меняет дату/офис)
        if dialog_state in ("remember", "available_time"):
            if not self._has_office_and_date(data):
                allowed.discard("zena_avaliable_time_for_master")
                allowed.discard("zena_available_time_for_master_list")

        # 2) Запись не даём без пакета данных + выбранного времени
        if dialog_state == "available_time":
            if not self._has_contact_bundle(data) or not self._has_desired_time(data):
                allowed.discard("zena_record_time")

        return allowed

    @staticmethod
    def _has_office_and_date(data: dict) -> bool:
        office_id = str(data.get("office_id") or "").strip()
        desired_date = str(data.get("desired_date") or "").strip()
        return bool(office_id and desired_date)

    @staticmethod
    def _has_desired_time(data: dict) -> bool:
        t = str(data.get("desired_time") or "").strip()
        return bool(t)

    @staticmethod
    def _has_contact_bundle(data: dict) -> bool:
        consent = bool(data.get("consent"))
        phone = str(data.get("phone") or "").strip()
        email = str(data.get("email") or "").strip()
        first = str(data.get("first_name") or "").strip()
        last = str(data.get("last_name") or "").strip()
        name_ok = bool(first or last)
        return bool(consent and name_ok and phone and email)

    # -------------------------
    # 4007/5007 (оставляем твою текущую логику, но добавляем globals + guards + override)
    # -------------------------
    def _allowed_for_4007_5007(self, dialog_state: str, data: dict, base: set[str]) -> set[str]:
        if dialog_state == "postrecord":
            return {"zena_recommendations"}

        allowed = set(base)

        match dialog_state:
            case "new":
                allowed.add("zena_record_product_id_list")
            case "remember":
                allowed |= {"zena_remember_product_id_list", "zena_avaliable_time_for_master_list"}
            case "available_time":
                allowed |= {"zena_remember_product_id_list", "zena_avaliable_time_for_master_list", "zena_record_time"}
            case _:
                pass

        # guards
        if dialog_state in ("remember", "available_time"):
            if not self._has_office_and_date(data):
                allowed.discard("zena_avaliable_time_for_master_list")

        if dialog_state == "available_time":
            if not self._has_contact_bundle(data) or not self._has_desired_time(data):
                allowed.discard("zena_record_time")

        return allowed

    # -------------------------
    # 5020 (Alena) — твоя логика + globals
    # -------------------------
    def _allowed_for_5020(self, dialog_state: str, data: dict, base: set[str]) -> set[str]:
        allowed = set(base)

        phone = str(data.get("phone") or "").strip()
        onboarding = data.get("onboarding") or {}
        onboarding_stage = onboarding.get("onboarding_stage")
        onboarding_status = onboarding.get("onboarding_status")

        # обычный режим диалога
        if (onboarding_status is None or onboarding_status is True) and phone:
            allowed.add("zena_get_client_statistics")

            if dialog_state == "new":
                allowed.add("zena_get_client_lessons")
            elif dialog_state == "selecting":
                allowed.add("zena_remember_lesson_id")
            elif dialog_state == "remember":
                allowed.add("zena_update_client_lesson")

        # режим опроса
        else:
            if isinstance(onboarding_stage, int) and onboarding_stage >= 5:
                allowed.add("zena_update_client_info")

        return allowed

    # -------------------------
    # Model selection (оставь свой импорт/логику, ниже заглушка)
    # -------------------------
    async def _select_model(self, state: dict):

        data = state.get("data", {}) or {}
        mcp_port = data.get("mcp_port")
        dialog_state = (data.get("dialog_state") or "new").strip()

        if mcp_port in self.PORTS_4007_5007:
            return model_4o if dialog_state in ("new", "available_time") else model_4o_mini
 
        if mcp_port in self.PORT_5020:
            return model_4o_mini

        if mcp_port in self.CLASSIC_PORTS:
            return model_4o if dialog_state in ("new", "remember", "postrecord") else model_4o_mini

        return model_4o_mini

    # -------------------------
    # Middleware entry
    # -------------------------
    async def awrap_model_call(self, request, handler):
        logger.info("===wrap_model_call===ToolSelectorMiddleware===")

        request.tools = await self._select_relevant_tools(request.state, request.tools)
        request.model = await self._select_model(request.state)

        return await handler(request)



# class ToolSelectorMiddleware(AgentMiddleware):
#     """Middleware для выбора релевантных инструментов по состоянию диалога."""

#     async def _select_relevant_tools(
#         self,
#         state: State,
#         tools: list[StructuredTool],
#     ) -> list[StructuredTool]:

#         logger.info("===wrap_model_call===_select_relevant_tools===")

#         data = state.get("data", {}) or {}
#         mcp_port = data.get("mcp_port")
#         dialog_state = data.get("dialog_state")

#         logger.info(f"dialog_state: {dialog_state}")
#         logger.info(f"mcp_port: {mcp_port}")
#         logger.info(f"all_tools: {[t.name for t in tools]}")

#         allowed = self._build_allowed_tools(mcp_port=mcp_port, dialog_state=dialog_state, data=data)

#         filtered = [tool for tool in tools if tool.name in allowed]
#         logger.info(f"tools: {[t.name for t in filtered]}")
#         return filtered

#     # -------------------------
#     # Builders
#     # -------------------------

#     def _build_allowed_tools(self, *, mcp_port: int | None, dialog_state: str | None, data: dict) -> set[str]:
#         # дефолтный набор, если порт неизвестен
#         if mcp_port is None:
#             return {"zena_faq"}

#         if mcp_port in {4007, 5007}:
#             return self._allowed_for_4007_5007(dialog_state)

#         if mcp_port in {4001, 5001, 4002, 5002, 4005, 5005, 4006, 5006}:
#             return self._allowed_for_classic_ports(dialog_state)

#         if mcp_port == 5020:
#             return self._allowed_for_5020(dialog_state, data)

#         # неизвестный порт
#         return {"zena_faq"}

#     def _allowed_for_4007_5007(self, dialog_state: str | None) -> set[str]:
#         allowed = {"zena_faq", "zena_services"}

#         match dialog_state:
#             case "new":
#                 allowed.add("zena_record_product_id_list")

#             case "remember":
#                 allowed |= {"zena_remember_product_id_list", "zena_avaliable_time_for_master_list"}

#             case "available_time":
#                 allowed |= {
#                     "zena_remember_product_id_list",
#                     "zena_avaliable_time_for_master_list",
#                     "zena_record_time",
#                 }

#             case "postrecord":
#                 allowed.add("zena_recommendations")

#             case _:
#                 pass

#         return allowed

#     def _allowed_for_classic_ports(self, dialog_state: str | None) -> set[str]:
#         # базовый набор
#         allowed = {"zena_faq", "zena_services", "zena_product_search"}

#         if dialog_state != "new":
#             allowed.add("zena_remember_product_id")

#         if dialog_state not in ("new", "selecting"):
#             allowed |= {"zena_avaliable_time_for_master", "zena_record_time"}

#         # ВАЖНО: у тебя тут "обнуление" (оставляем только recommendations)
#         if dialog_state == "postrecord":
#             return {"zena_recommendations"}

#         return allowed

#     def _allowed_for_5020(self, dialog_state: str | None, data: dict) -> set[str]:
#         allowed = {"zena_faq"}

#         phone = (data.get("phone") or "").strip()
#         onboarding = data.get("onboarding") or {}
#         onboarding_stage = onboarding.get("onboarding_stage")
#         onboarding_status = onboarding.get("onboarding_status")

#         # обычный режим диалога
#         if (onboarding_status is None or onboarding_status is True) and phone:
#             allowed.add("zena_get_client_statistics")

#             if dialog_state == "new":
#                 allowed.add("zena_get_client_lessons")
#             elif dialog_state == "selecting":
#                 allowed.add("zena_remember_lesson_id")
#             elif dialog_state == "remember":
#                 allowed.add("zena_update_client_lesson")

#         # режим опроса
#         else:
#             if isinstance(onboarding_stage, int) and onboarding_stage >= 5:
#                 allowed.add("zena_update_client_info")

#         return allowed


#     async def _select_model (self, state: State) -> BaseChatModel:
#         """Выбор модели под конкретную стадию диалога."""

#         logger.info("===wrap_model_call===_select_model===")

#         data = state.get("data", {}) 
#         mcp_port = data.get("mcp_port")
#         dialog_state = data.get("dialog_state")

#         if mcp_port in [4007, 5007]:
#             model = model_4o if dialog_state in ["new", "available_time"] else model_4o_mini
#             logger.info(f"dialog_state: {dialog_state}")
#             logger.info(f"Выбрана модель: {model}")
#             return model
#         elif mcp_port in [5020]:
#              return model_4o_mini
#         elif mcp_port in [5002]:
#             model = model_4o if dialog_state in ["remember"] else model_4o_mini
#             logger.info(f"dialog_state: {dialog_state}")
#             logger.info(f"Выбрана модель: {model}")
#             return model
#         else:
#             return model_4o_mini


#     async def awrap_model_call(
#         self,
#         request: ModelRequest,
#         handler: Callable[[ModelRequest], ModelResponse],
#     ) -> ModelResponse:
#         """Middleware для выбора релевантных инструментов и модели."""

#         logger.info("===wrap_model_call===ToolSelectorMiddleware===")

#         request.tools = await self._select_relevant_tools(request.state, request.tools)
#         request.model = await self._select_model(request.state)
        
#         return await handler(request)



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


    # async def _select_relevant_tools(
    #         self,
    #         state: State, 
    #         tools: list[StructuredTool]
    # ) -> list[StructuredTool]:
    #     """Выбора релевантных инструментов по состоянию диалога."""
        
    #     logger.info("===wrap_model_call===_select_relevant_tools===")

    #     data = state.get("data", {}) 
    #     mcp_port = data.get("mcp_port")
    #     dialog_state = data.get("dialog_state")

    #     logger.info(f"dialog_state: {dialog_state}")
    #     logger.info(f"mcp_port: {mcp_port}")

    #     print([tool.name for tool in tools])


    #     # Подключение инструментов ы зависисмости от стадии диалога.
    #     if mcp_port in [4007, 5007]:

    #         allowed = {"zena_faq", "zena_services"}

    #         if dialog_state in ["new"]:
    #             print('dialog_state in ["new"]')
    #             allowed.update({"zena_record_product_id_list"})

    #         elif dialog_state in ["remember"]:
    #             allowed.update({"zena_remember_product_id_list"})
    #             allowed.update({"zena_avaliable_time_for_master_list"})

    #         elif dialog_state in ["available_time"]:
    #             allowed.update({"zena_remember_product_id_list"})
    #             allowed.update({"zena_avaliable_time_for_master_list"})
    #             allowed.update({"zena_record_time"})
            
    #         elif dialog_state in ["postrecord"]:
    #             allowed.update({"zena_recommendations"})
        
    #     elif mcp_port in [4001, 5001, 4002, 5002, 4005, 5005, 4006, 5006,]:
            
    #         allowed = {"zena_faq", "zena_services", "zena_product_search"}

    #         if dialog_state not in ["new"]:
    #             allowed.update({"zena_remember_product_id"})

    #         if dialog_state not in ["new", "selecting"]:
    #             allowed.update({"zena_avaliable_time_for_master"})
    #             allowed.update({"zena_record_time"})

    #         if dialog_state in ["postrecord"]:
    #             allowed = set() 
    #             allowed.update({"zena_recommendations"})
                
    #     elif mcp_port in [5020]:
            
    #         allowed = {"zena_faq"}

    #         phone = data.get('phone')
    #         onboarding_stage = data.get("onboarding", {}).get("onboarding_stage")
    #         onboarding_status = data.get("onboarding", {}).get("onboarding_status")

    #         # Если обычный режим диалога
    #         if (onboarding_status is None or onboarding_status) and phone != '':

    #             allowed.add("zena_get_client_statistics")

    #             if dialog_state == "new":
    #                 allowed.add("zena_get_client_lessons")

    #             if dialog_state == "selecting":
    #                 allowed.add("zena_remember_lesson_id")

    #             if dialog_state == "remember":
    #                 allowed.add("zena_update_client_lesson")

    #         # Если режим - опроса.
    #         else:
    #             if onboarding_stage and onboarding_stage >= 5:
    #                 allowed.add("zena_update_client_info")

    #     logger.info(f"tools: {[tool.name for tool in tools if tool.name in allowed]}")
    #     return [tool for tool in tools if tool.name in allowed]