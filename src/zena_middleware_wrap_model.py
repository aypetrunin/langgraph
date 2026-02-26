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

        # logger.info(f"data: {data}")

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
            logger.info("system_prompt(len=%s, sha=%s):\n%s", prompt_len, prompt_hash, system_prompt[:300])
        else:
            # В prod — только метаданные
            logger.info("system_prompt rendered (len=%s, sha=%s)", prompt_len, prompt_hash)



class ToolSelectorMiddleware(AgentMiddleware):
    """
    Middleware: решает, какие инструменты (tools) доступны модели (LLM) на каждом шаге.

    Есть 2 независимые ветки логики:

    A) "Классическая запись" (твоя старая логика через dialog_state):
       - слоты мастера доступны только если есть office_id + desired_date
       - запись (zena_record_time) доступна только если есть контакты + desired_time

    B) "Управление существующими записями" (просмотр/отмена/перенос) через user_records:
       - zena_records (просмотр) доступен всегда
       - если user_records НЕ пустой:
           * разрешаем zena_record_delete (отмена)
           * разрешаем zena_avaliable_time_for_master (+ list) ТОЛЬКО если есть desired_date
             (потому что чтобы искать слоты для переноса, нам нужна дата)
           * разрешаем zena_record_reschedule ТОЛЬКО если выбраны desired_date И desired_time
             (перенос "куда?" — нужна дата и время)
       - если user_records пустой:
           * запрещаем delete / reschedule / слоты переноса
    """

    # ====== FSM (для классической записи) ======
    ORDER = ["new", "selecting", "remember", "available_time", "postrecord"]

    # ====== ALWAYS AVAILABLE TOOLS ======
    GLOBAL_TOOLS: set[str] = {
        "zena_faq",
        "zena_services",

        # remember tools (могут понадобиться в любой момент)
        "zena_remember_office",
        "zena_remember_master",
        "zena_remember_desired_date",
        "zena_remember_desired_time",

        # просмотр записей клиента — всегда доступен
        "zena_records",
        "zena_call_administrator",
    }

    # ====== Stage tools for classic ports ======
    STAGE_TOOLS_CLASSIC: dict[str, set[str]] = {
        "new": {"zena_product_search"},
        "selecting": {
            "zena_product_search",
            "zena_remember_product_id",
        },
        "remember": {
            "zena_avaliable_time_for_master",
        },
        "available_time": {
            "zena_record_time",
            "zena_avaliable_time_for_master",
        },
        "postrecord": {"zena_recommendations"},
    }

    POSTRECORD_OVERRIDE: set[str] = {"zena_recommendations"}

    # ====== Ports ======
    CLASSIC_PORTS = {15001, 5001, 5002, 15002, 15005, 5005, 15006, 5006, 15021, 5021, 15024, 5024, 15017, 5017,}
    PORTS_4007_5007 = {15007, 5007}
    PORT_5020 = {15020, 5020}

    # =========================================================================
    # MAIN: select tools
    # =========================================================================
    async def _select_relevant_tools(self, state: dict, tools: list[StructuredTool]) -> list[StructuredTool]:
        logger.info("===wrap_model_call===_select_relevant_tools===")

        data = state.get("data", {}) or {}
        mcp_port = data.get("mcp_port")
        dialog_state = (data.get("dialog_state") or "new").strip()

        logger.info("dialog_state=%s", dialog_state)
        logger.info("mcp_port=%s", mcp_port)
        logger.info("all_tools=%s", [t.name for t in tools])

        allowed = self._build_allowed_tools(mcp_port=mcp_port, dialog_state=dialog_state, data=data)
        filtered = [tool for tool in tools if tool.name in allowed]

        logger.info("allowed_tools=%s", sorted(allowed))
        logger.info("tools_filtered=%s", [t.name for t in filtered])
        return filtered

    def _build_allowed_tools(self, *, mcp_port: int | None, dialog_state: str, data: dict) -> set[str]:
        logger.info("_build_allowed_tools")
        
        base = set(self.GLOBAL_TOOLS)

        if mcp_port is None:
            return self._apply_guards(dialog_state, base, data)

        if mcp_port in self.PORT_5020:
            allowed = self._allowed_for_5020(dialog_state, data, base)
            return self._apply_guards(dialog_state, allowed, data)

        if mcp_port in self.PORTS_4007_5007:
            allowed = self._allowed_for_4007_5007(dialog_state, data, base)
            return self._apply_guards(dialog_state, allowed, data)

        if mcp_port in self.CLASSIC_PORTS:
            allowed = self._allowed_for_classic_ports(dialog_state, data, base)
            logger.info(f"allowed: {allowed}")
            responce = self._apply_guards(dialog_state, allowed, data)
            logger.info(f"responce _apply_guards: {responce}")
            return responce

        return self._apply_guards(dialog_state, base, data)

    # =========================================================================
    # Classic ports logic
    # =========================================================================
    def _allowed_for_classic_ports(self, dialog_state: str, data: dict, base: set[str]) -> set[str]:
        if dialog_state == "postrecord":
            return set(self.POSTRECORD_OVERRIDE)

        allowed = set(base)
        allowed |= self._inherited_stage_tools(dialog_state, self.STAGE_TOOLS_CLASSIC)
        return allowed

    def _inherited_stage_tools(self, dialog_state: str, stage_map: dict[str, set[str]]) -> set[str]:
        if dialog_state not in self.ORDER:
            dialog_state = "new"
        idx = self.ORDER.index(dialog_state)

        out: set[str] = set()
        for st in self.ORDER[: idx + 1]:
            out |= stage_map.get(st, set())
        return out

    # =========================================================================
    # GUARDS
    # =========================================================================
    def _apply_guards(self, dialog_state: str, allowed: set[str], data: dict) -> set[str]:
        """
        A) Classic funnel guards (твои правила записи)
        B) Records management guards (просмотр/отмена/перенос)

        ВАЖНО: в ветке B мы теперь контролируем BOTH desired_date и desired_time:
          - слоты для переноса => нужен desired_date
          - финальный перенос => нужны desired_date + desired_time
        """

        logger.info(
            "DEBUG keys: office_id=%r desired_date=%r date=%r",
            data.get("office_id"), data.get("desired_date"), data.get("date")
        )


        # -----------------------------
        # A) Classic funnel guards
        # -----------------------------
        if dialog_state in ("remember", "available_time"):
            # Без офиса и даты спрашивать слоты бессмысленно
            if not self._has_office_and_date(data):
                logger.info("_has_office_and_date == False")
                allowed.discard("zena_avaliable_time_for_master")
                allowed.discard("zena_available_time_for_master_list")

        if dialog_state == "available_time":
            # Без пакета контактов и выбранного времени "запись" не делаем
            if not self._has_contact_bundle(data) or not self._has_desired_time(data):
                allowed.discard("zena_record_time")

        # -----------------------------
        # B) Existing records management via user_records
        # -----------------------------
        if self._has_user_records(data):
            logger.info("Ветка В")
            # 1) Отмена — доступна сразу, если есть записи
            allowed.add("zena_record_delete")

            # 2) Слоты для переноса — только если пользователь указал/выбрал desired_date
            # (иначе "на какую дату искать новые слоты?" непонятно)
            if self._has_desired_date(data):
                allowed.add("zena_avaliable_time_for_master")
                allowed.add("zena_available_time_for_master_list")
                allowed.discard("zena_record_delete")
            else:
                allowed.discard("zena_avaliable_time_for_master")
                allowed.discard("zena_available_time_for_master_list")

            # 3) Финальный перенос — только если выбраны И дата, И время
            if self._has_desired_date(data) and self._has_desired_time(data):
                allowed.add("zena_record_reschedule")
            else:
                allowed.discard("zena_record_reschedule")

        return allowed

    # =========================================================================
    # Helpers
    # =========================================================================
    @staticmethod
    def _has_user_records(data: dict) -> bool:
        recs = data.get("user_records")
        return isinstance(recs, list) and len(recs) > 0

    @staticmethod
    def _has_desired_date(data: dict) -> bool:
        """
        desired_date — дата, на которую пользователь хочет перенести.
        Обычно хранится как строка (например "2026-01-28" или "28.01.2026").
        """
        d = str(data.get("desired_date") or "").strip()
        return bool(d)

    @staticmethod
    def _has_office_and_date(data: dict) -> bool:
        logger.info("_has_office_and_date")
        office_id = str(data.get("office_id") or "").strip()
        desired_date = str(data.get("desired_date") or "").strip()
        responce = bool(office_id and desired_date)
        logger.info(f"responce: {responce}")
        return responce

    @staticmethod
    def _has_desired_time(data: dict) -> bool:
        t = str(data.get("desired_time") or "").strip()
        return bool(t)

    @staticmethod
    def _has_contact_bundle(data: dict) -> bool:
        consent = bool(data.get("consent"))
        phone = str(data.get("phone") or "").strip()
        # email = str(data.get("email") or "").strip()
        # first = str(data.get("first_name") or "").strip()
        # last = str(data.get("last_name") or "").strip()
        # name_ok = bool(first or last)
        # return bool(consent and name_ok and phone and email)
        return bool(consent and phone)

    # =========================================================================
    # 4007/5007
    # =========================================================================
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

        return allowed

    # =========================================================================
    # 5020
    # =========================================================================
    def _allowed_for_5020(self, dialog_state: str, data: dict, base: set[str]) -> set[str]:
        allowed = set(base)

        phone = str(data.get("phone") or "").strip()
        onboarding = data.get("onboarding") or {}
        onboarding_stage = onboarding.get("onboarding_stage")
        onboarding_status = onboarding.get("onboarding_status")

        if (onboarding_status is None or onboarding_status is True) and phone:
            allowed.add("zena_get_client_statistics")

            if dialog_state == "new":
                allowed.add("zena_get_client_lessons")
            elif dialog_state == "selecting":
                allowed.add("zena_remember_lesson_id")
            elif dialog_state == "remember":
                allowed.add("zena_update_client_lesson")
        else:
            if isinstance(onboarding_stage, int) and onboarding_stage >= 5:
                allowed.add("zena_update_client_info")

        return allowed

    # =========================================================================
    # Model selection
    # =========================================================================
    async def _select_model(self, state: dict):
        data = state.get("data", {}) or {}
        mcp_port = data.get("mcp_port")
        dialog_state = (data.get("dialog_state") or "new").strip()

        if mcp_port in self.PORTS_4007_5007:
            return model_4o if dialog_state in ("new", "available_time") else model_4o_mini

        if mcp_port in self.PORT_5020:
            return model_4o_mini

        if mcp_port in self.CLASSIC_PORTS:
            return model_4o if dialog_state in ("postrecord") else model_4o_mini # "new", "remember"

        return model_4o_mini

    # =========================================================================
    # Middleware entry
    # =========================================================================
    async def awrap_model_call(self, request, handler):
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
