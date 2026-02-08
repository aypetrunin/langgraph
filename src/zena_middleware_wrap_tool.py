# zena_middleware_wrap_tool.py
#
# Единая точка приема результатов MCP-tools в LangGraph.
# Контракт MCP-серверов: Payload = {success: bool, data|code+error}
# Fail-fast: если tool не вернул Payload — падаем сразу.

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, Type

from langgraph.types import Command
from langchain_core.messages import ToolMessage
from langchain.tools.tool_node import ToolCallRequest
from langchain.agents.middleware import AgentMiddleware

from .zena_common import logger, _content_to_text

AVAILIABLE_PORT_ALENA = {15020, 5020}
AVAILIABLE_PORT_DEFAULT = {15001, 5001, 5002, 15002, 15005, 5005, 15006, 5006, 15021, 5021, 15024, 5024}

PostProcessor = Callable[["Envelope", ToolCallRequest], Awaitable[Any]]


# ----------------------------
# Unified envelope (Payload-only)
# ----------------------------

@dataclass(frozen=True)
class Envelope:
    success: bool
    data: Any = None
    code: Optional[str] = None
    error: Optional[str] = None
    raw: Any = None  # исходный распарсенный контент (для дебага)

    def is_ok(self) -> bool:
        return bool(self.success)

    def is_err(self) -> bool:
        return not bool(self.success)


def _parse_tool_content(result: ToolMessage) -> Any:
    """
    ToolMessage.content обычно строка.
    Возвращает:
      - dict/list/... если удалось json.loads
      - иначе str (raw_content)
    """
    raw_content = _content_to_text(getattr(result, "content", ""))
    logger.info("raw_content: %s", raw_content)
    if not raw_content or not raw_content.strip():
        return ""  # иногда tool возвращает пустое
    try:
        return json.loads(raw_content)
    except (json.JSONDecodeError, TypeError):
        return raw_content


def _normalize_envelope(parsed: Any, *, tool_name: str | None = None) -> Envelope:
    """
    ЖЁСТКИЙ контракт (обязательный):
      ok:  {"success": True,  "data": ...}
      err: {"success": False, "code": "...", "error": "..."}

    Fail-fast:
      если формат не Payload — бросаем RuntimeError.
    """
    if not isinstance(parsed, dict) or not isinstance(parsed.get("success"), bool):
        tn = tool_name or "<unknown_tool>"
        raise RuntimeError(
            "MCP tool contract violation: expected Payload dict with boolean field 'success'. "
            f"tool={tn} parsed_type={type(parsed).__name__} parsed={parsed!r}"
        )

    success = bool(parsed["success"])
    data = parsed.get("data")
    code = parsed.get("code")
    error = parsed.get("error")

    # страховка: error/code приводим к строке (контракт подразумевает str)
    if error is not None and not isinstance(error, str):
        error = str(error)
    if code is not None and not isinstance(code, str):
        code = str(code)

    return Envelope(
        success=success,
        data=data,
        code=code,
        error=error,
        raw=parsed,
    )


def _port_allowed_default(request: ToolCallRequest) -> bool:
    data = request.state.get("data") or {}
    return data.get("mcp_port") in AVAILIABLE_PORT_DEFAULT


def _port_allowed_alena(request: ToolCallRequest) -> bool:
    data = request.state.get("data") or {}
    return data.get("mcp_port") in AVAILIABLE_PORT_ALENA


async def _run_template(
    *,
    request: ToolCallRequest,
    env: Envelope,
    expected_data_type: Type | tuple[Type, ...],
    on_ok: Callable[[dict, Any, ToolCallRequest], None],
    port_guard: Callable[[ToolCallRequest], bool],
    require_truthy_data: bool = True,
    require_success: bool = True,
) -> Any:
    """
    Общий шаблон: port_guard + envelope + (success?) + typecheck(data) + truthy(data) + on_ok.
    Возвращает env.data, либо None.
    """
    if not port_guard(request):
        return None

    if require_success and not env.success:
        return None

    data_value = env.data
    if not isinstance(data_value, expected_data_type):
        return None
    if require_truthy_data and not data_value:
        return None

    state_data = request.state["data"]
    on_ok(state_data, data_value, request)
    return data_value


async def zena_default(
    *,
    request: ToolCallRequest,
    env: Envelope,
    expected_data_type: Type | tuple[Type, ...],
    on_ok: Callable[[dict, Any, ToolCallRequest], None],
    require_truthy_data: bool = True,
    require_success: bool = True,
) -> Any:
    return await _run_template(
        request=request,
        env=env,
        expected_data_type=expected_data_type,
        on_ok=on_ok,
        port_guard=_port_allowed_default,
        require_truthy_data=require_truthy_data,
        require_success=require_success,
    )


async def zena_alena(
    *,
    request: ToolCallRequest,
    env: Envelope,
    expected_data_type: Type | tuple[Type, ...],
    on_ok: Callable[[dict, Any, ToolCallRequest], None],
    require_truthy_data: bool = True,
    require_success: bool = True,
) -> Any:
    return await _run_template(
        request=request,
        env=env,
        expected_data_type=expected_data_type,
        on_ok=on_ok,
        port_guard=_port_allowed_alena,
        require_truthy_data=require_truthy_data,
        require_success=require_success,
    )


def parse_item(item: dict) -> dict:
    keys_map = {
        "item_id": "product_id",
        "item_name": "product_name",
        "item_duration": "duration",
        "item_price": "price",
    }
    return {key: item.get(src_key) for key, src_key in keys_map.items() if item.get(src_key) is not None}


# =======================
# DEFAULT (400x/500x) PP
# =======================

async def pp_available_time_for_master(env: Envelope, request: ToolCallRequest) -> Any:
    def on_ok(data: dict, tools_data: list, request: ToolCallRequest) -> None:
        data.setdefault("available_time", [])
        data["available_time"].append(tools_data)
        if not data.get("user_records"):
            data["dialog_state"] = "available_time"

    return await zena_default(request=request, env=env, expected_data_type=list, on_ok=on_ok, require_success=True)


async def pp_available_time_for_master_list(env: Envelope, request: ToolCallRequest) -> Any:
    def on_ok(data: dict, tools_data: list, request: ToolCallRequest) -> None:
        if len(tools_data) < 2:
            return
        data.setdefault("available_time", [])
        data.setdefault("available_sequences", [])
        data["available_time"].append(tools_data[0])
        data["available_sequences"].append(tools_data[1])
        data["dialog_state"] = "available_time"

    return await zena_default(request=request, env=env, expected_data_type=list, on_ok=on_ok, require_success=True)


async def pp_record_time(env: Envelope, request: ToolCallRequest) -> Any:
    def on_ok(data: dict, tools_data: dict, request: ToolCallRequest) -> None:
        tool_args = request.tool_call.get("args") or {}
        data["dialog_state"] = "postrecord"
        data["desired_date"] = tool_args.get("date")
        data["office_id"] = tool_args.get("office_id")
        data["desired_master"] = {"master_id": tool_args.get("master_id")}
        data["item_selected"] = [{"item_id": tool_args.get("product_id")}]

    return await zena_default(
        request=request,
        env=env,
        expected_data_type=dict,
        on_ok=on_ok,
        require_truthy_data=False,
        require_success=True,
    )


async def pp_recommendations(env: Envelope, request: ToolCallRequest) -> Any:
    def on_ok(data: dict, tools_data: Any, request: ToolCallRequest) -> None:
        data.update(
            {
                "dialog_state": "new",
                "items_search": [],
                "item_selected": [],
                "available_time": [],
                "available_sequences": [],
                "user_records": [],
                "office_id": None,
                "desired_date": None,
                "desired_time": None,
                "desired_master": None,
            }
        )

    return await zena_default(
        request=request,
        env=env,
        expected_data_type=(list, dict, str, type(None)),
        on_ok=on_ok,
        require_truthy_data=False,
        require_success=True,
    )


async def pp_call_administrator(env: Envelope, request: ToolCallRequest) -> Any:
    def on_ok(data: dict, tools_data: Any, request: ToolCallRequest) -> None:
        data.update(
            {
                "dialog_state": "new",
                "items_search": [],
                "item_selected": [],
                "available_time": [],
                "available_sequences": [],
                "user_records": [],
                "office_id": None,
                "desired_date": None,
                "desired_time": None,
                "desired_master": None,
            }
        )

    return await zena_default(
        request=request,
        env=env,
        expected_data_type=(str, dict, type(None)),
        on_ok=on_ok,
        require_truthy_data=False,
        require_success=True,
    )


async def pp_records(env: Envelope, request: ToolCallRequest) -> Any:
    def on_ok(data: dict, tools_data: list, request: ToolCallRequest) -> None:
        if tools_data:
            data["user_records"] = tools_data
            data["desired_date"] = None
            data["desired_time"] = None
        else:
            data["user_records"] = "У Вас нет записей на услуги."

    return await zena_default(
        request=request,
        env=env,
        expected_data_type=list,
        on_ok=on_ok,
        require_truthy_data=False,
        require_success=True,
    )


async def pp_record_delete(env: Envelope, request: ToolCallRequest) -> Any:
    def on_ok(data: dict, tools_data: Any, request: ToolCallRequest) -> None:
        data["user_records"] = []
        data["desired_date"] = None
        data["desired_time"] = None

    return await zena_default(
        request=request,
        env=env,
        expected_data_type=(str, dict, type(None)),
        on_ok=on_ok,
        require_truthy_data=False,
        require_success=True,
    )


async def pp_record_reschedule(env: Envelope, request: ToolCallRequest) -> Any:
    def on_ok(data: dict, tools_data: Any, request: ToolCallRequest) -> None:
        data["user_records"] = []
        data["desired_date"] = None
        data["desired_time"] = None

    return await zena_default(
        request=request,
        env=env,
        expected_data_type=(str, dict, type(None)),
        on_ok=on_ok,
        require_truthy_data=False,
        require_success=True,
    )


async def pp_remember_office(env: Envelope, request: ToolCallRequest) -> Any:
    def on_ok(data: dict, tools_data: dict, request: ToolCallRequest) -> None:
        office_id = tools_data.get("office_id")
        if office_id is not None:
            data["office_id"] = str(office_id)

    return await zena_default(
        request=request,
        env=env,
        expected_data_type=dict,
        on_ok=on_ok,
        require_truthy_data=False,
        require_success=True,
    )


async def pp_remember_desired_date(env: Envelope, request: ToolCallRequest) -> Any:
    def on_ok(data: dict, tools_data: dict, request: ToolCallRequest) -> None:
        desired_date = tools_data.get("desired_date")
        if desired_date is not None:
            data["desired_date"] = str(desired_date)

    return await zena_default(
        request=request,
        env=env,
        expected_data_type=dict,
        on_ok=on_ok,
        require_truthy_data=False,
        require_success=True,
    )


async def pp_remember_desired_time(env: Envelope, request: ToolCallRequest) -> Any:
    def on_ok(data: dict, tools_data: dict, request: ToolCallRequest) -> None:
        desired_time = tools_data.get("desired_time")
        if desired_time is not None:
            data["desired_time"] = str(desired_time)

    return await zena_default(
        request=request,
        env=env,
        expected_data_type=dict,
        on_ok=on_ok,
        require_truthy_data=False,
        require_success=True,
    )


async def pp_remember_master(env: Envelope, request: ToolCallRequest) -> Any:
    def on_ok(data: dict, tools_data: dict, request: ToolCallRequest) -> None:
        master_id = tools_data.get("master_id")
        if master_id:
            data["desired_master"] = {
                "master_id": str(master_id),
                "master_name": str(tools_data.get("master_name", "")),
            }

    return await zena_default(
        request=request,
        env=env,
        expected_data_type=dict,
        on_ok=on_ok,
        require_truthy_data=False,
        require_success=True,
    )


async def pp_product_remember(env: Envelope, request: ToolCallRequest) -> Any:
    items_out: list[dict] = []

    def on_ok(data: dict, tools_data: Any, request: ToolCallRequest) -> None:
        nonlocal items_out
        products: list[Any] = tools_data or []
        items_out = [parse_item(x) for x in products if isinstance(x, dict)]
        if not items_out:
            return
        data["item_selected"] = items_out
        data["dialog_state"] = "remember"

    await zena_default(
        request=request,
        env=env,
        expected_data_type=list,
        on_ok=on_ok,
        require_truthy_data=True,
        require_success=True,
    )
    return items_out or None


async def pp_product_search(env: Envelope, request: ToolCallRequest) -> Any:
    added_items: list[dict] = []

    def on_ok(data: dict, tools_data: list, request: ToolCallRequest) -> None:
        nonlocal added_items
        items_search = data.setdefault("items_search", [])
        existing_ids = {it.get("item_id") for it in items_search if isinstance(it, dict)}

        new_items: list[dict] = []
        for raw_item in tools_data:
            if not isinstance(raw_item, dict):
                continue
            item = parse_item(raw_item)
            item_id = item.get("item_id")
            if item_id and item_id not in existing_ids:
                new_items.append(item)
                existing_ids.add(item_id)

        if new_items:
            items_search.extend(new_items)
            added_items = new_items

        if items_search:
            data["dialog_state"] = "selecting"

    await zena_default(
        request=request,
        env=env,
        expected_data_type=list,
        on_ok=on_ok,
        require_truthy_data=False,
        require_success=True,
    )
    return added_items or None


TOOL_POSTPROCESSORS_DEFAULT: dict[str, PostProcessor] = {
    "zena_avaliable_time_for_master": pp_available_time_for_master,
    "zena_record_time": pp_record_time,
    "zena_recommendations": pp_recommendations,
    "zena_product_search": pp_product_search,
    "zena_remember_product_id": pp_product_remember,
    "zena_remember_office": pp_remember_office,
    "zena_remember_master": pp_remember_master,
    "zena_remember_desired_date": pp_remember_desired_date,
    "zena_remember_desired_time": pp_remember_desired_time,
    "zena_records": pp_records,
    "zena_record_delete": pp_record_delete,
    "zena_record_reschedule": pp_record_reschedule,
    "zena_call_administrator": pp_call_administrator,
}

TOOL_POSTPROCESSORS_5007: dict[str, PostProcessor] = {
    "zena_available_time_for_master_list": pp_available_time_for_master_list,
    "zena_record_time": pp_record_time,
    "zena_recommendations": pp_recommendations,
    "zena_product_search": pp_product_search,
    "zena_remember_product_id_list": pp_product_remember,
    "zena_remember_office": pp_remember_office,
    "zena_remember_master": pp_remember_master,
    "zena_remember_desired_date": pp_remember_desired_date,
    "zena_remember_desired_time": pp_remember_desired_time,
}


# =======================
# ALENA (5020) PP
# =======================

async def pp_get_client_lessons(env: Envelope, request: ToolCallRequest) -> Any:
    def on_ok(data: dict, tools_data: dict, request: ToolCallRequest) -> None:
        data["dialog_state"] = "selecting"
        data["items_search"] = tools_data

    return await zena_alena(request=request, env=env, expected_data_type=dict, on_ok=on_ok, require_success=True)


async def pp_remember_lesson_id(env: Envelope, request: ToolCallRequest) -> Any:
    selected: Any = None

    def on_ok(data: dict, tools_data: Any, request: ToolCallRequest) -> None:
        nonlocal selected
        selected = tools_data
        data["dialog_state"] = "remember"
        data["item_selected"] = tools_data

    await zena_alena(
        request=request,
        env=env,
        expected_data_type=(dict, list, str),
        on_ok=on_ok,
        require_truthy_data=True,
        require_success=True,
    )
    return selected


async def pp_update_client_lesson(env: Envelope, request: ToolCallRequest) -> Any:
    def on_ok(data: dict, tools_data: Any, request: ToolCallRequest) -> None:
        data["dialog_state"] = "new"

    return await zena_alena(
        request=request,
        env=env,
        expected_data_type=(str, dict, list),
        on_ok=on_ok,
        require_truthy_data=False,
        require_success=True,
    )


async def pp_update_client_info(env: Envelope, request: ToolCallRequest) -> Any:
    def on_ok(data: dict, tools_data: Any, request: ToolCallRequest) -> None:
        data.setdefault("onboarding", {})
        data["onboarding"]["onboarding_status"] = True

    return await zena_alena(
        request=request,
        env=env,
        expected_data_type=(str, dict, list),
        on_ok=on_ok,
        require_truthy_data=False,
        require_success=True,
    )


TOOL_POSTPROCESSORS_ALENA: dict[str, PostProcessor] = {
    "zena_get_client_lessons": pp_get_client_lessons,
    "zena_remember_lesson_id": pp_remember_lesson_id,
    "zena_update_client_lesson": pp_update_client_lesson,
    "zena_update_client_info": pp_update_client_info,
}


def _get_registry_for_request(request: ToolCallRequest) -> dict[str, PostProcessor]:
    data = request.state.get("data") or {}
    port = data.get("mcp_port")
    if port in AVAILIABLE_PORT_ALENA:
        return TOOL_POSTPROCESSORS_ALENA
    return TOOL_POSTPROCESSORS_DEFAULT


class ToolMonitoringMiddleware(AgentMiddleware):
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        tool_name = request.tool_call.get("name")
        tool_args = request.tool_call.get("args")

        logger.info("===wrap_tool_call===ToolMonitoringMiddleware===")
        logger.info("Executing tool: %s", tool_name)
        logger.info("Tool arguments: %s", tool_args)

        try:
            result = await handler(request)

            parsed = _parse_tool_content(result) if isinstance(result, ToolMessage) else result

            # FAIL-FAST тут: если contract violation — RuntimeError улетит вверх.
            env = _normalize_envelope(parsed, tool_name=tool_name)

            logger.info(
                "Tool envelope: success=%s code=%s error=%s data_type=%s",
                env.success,
                env.code,
                env.error,
                type(env.data).__name__,
            )

            registry = _get_registry_for_request(request)

            # если реально есть особый реестр под 5007 — оставляем
            data_state = request.state.get("data") or {}
            if data_state.get("mcp_port") == 5007:
                registry = TOOL_POSTPROCESSORS_5007

            pp = registry.get(tool_name)
            pp_result = await pp(env, request) if pp else None

            request.state.setdefault("tools_result", []).append(
                {
                    "name": tool_name,
                    "args": tool_args,
                    "envelope": {
                        "success": env.success,
                        "code": env.code,
                        "error": env.error,
                    },
                    "result": pp_result,
                }
            )

            return result

        except Exception as e:
            logger.exception("Tool failed: %s", e)
            raise
