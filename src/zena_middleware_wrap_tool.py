import json
from typing import Any, Awaitable, Callable, Type

from langgraph.types import Command
from langchain_core.messages import ToolMessage
from langchain.tools.tool_node import ToolCallRequest
from langchain.agents.middleware import AgentMiddleware

from .zena_common import logger, _content_to_text

AVAILIABLE_PORT_ALENA = {5020}
AVAILIABLE_PORT_DEFAULT = {4001, 5001, 4002, 5002, 4005, 5005, 4006, 5006, 4007, 5007}


PostProcessor = Callable[[ToolMessage, ToolCallRequest], Awaitable[Any]]


def _parse_tool_content(result: ToolMessage) -> Any:
    logger.info("_parse_tool_content")
    raw_content = _content_to_text(getattr(result, "content", ""))
    logger.info(f"raw_content: {raw_content}")
    if not raw_content or not raw_content.strip():
        return ""
    try:
        return json.loads(raw_content)
    except (json.JSONDecodeError, TypeError):
        return raw_content


def _port_allowed_default(request: ToolCallRequest) -> bool:
    data = request.state.get("data") or {}
    return data.get("mcp_port") in AVAILIABLE_PORT_DEFAULT


def _port_allowed_alena(request: ToolCallRequest) -> bool:
    data = request.state.get("data") or {}
    return data.get("mcp_port") in AVAILIABLE_PORT_ALENA


async def _run_template(
    *,
    request: ToolCallRequest,
    result: ToolMessage,
    expected_type: Type | tuple[Type, ...],
    on_ok: Callable[[dict, Any, ToolCallRequest], None],
    port_guard: Callable[[ToolCallRequest], bool],
    require_truthy: bool = True,
) -> Any:
    """Общий шаблон: port_guard + parse + typecheck + truthy + on_ok.
    Возвращает tools_result (parsed), либо None.
    """
    if not port_guard(request):
        return None

    tools_result = _parse_tool_content(result)

    if not isinstance(tools_result, expected_type):
        return None
    if require_truthy and not tools_result:
        return None

    data = request.state["data"]
    on_ok(data, tools_result, request)
    return tools_result


async def zena_default(
    *,
    request: ToolCallRequest,
    result: ToolMessage,
    expected_type: Type | tuple[Type, ...],
    on_ok: Callable[[dict, Any, ToolCallRequest], None],
    require_truthy: bool = True,
) -> Any:
    return await _run_template(
        request=request,
        result=result,
        expected_type=expected_type,
        on_ok=on_ok,
        port_guard=_port_allowed_default,
        require_truthy=require_truthy,
    )


async def zena_alena(
    *,
    request: ToolCallRequest,
    result: ToolMessage,
    expected_type: Type | tuple[Type, ...],
    on_ok: Callable[[dict, Any, ToolCallRequest], None],
    require_truthy: bool = True,
) -> Any:
    return await _run_template(
        request=request,
        result=result,
        expected_type=expected_type,
        on_ok=on_ok,
        port_guard=_port_allowed_alena,
        require_truthy=require_truthy,
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

async def pp_available_time_for_master(result: ToolMessage, request: ToolCallRequest) -> Any:
    def on_ok(data: dict, tools_result: list, request: ToolCallRequest) -> None:
        data.setdefault("available_time", [])
        data["available_time"].append(tools_result)
        data["dialog_state"] = "available_time"

    return await zena_default(request=request, result=result, expected_type=list, on_ok=on_ok)


async def pp_available_time_for_master_list(result: ToolMessage, request: ToolCallRequest) -> Any:
    def on_ok(data: dict, tools_result: list, request: ToolCallRequest) -> None:
        if len(tools_result) < 2:
            return
        data.setdefault("available_time", [])
        data.setdefault("available_sequences", [])
        data["available_time"].append(tools_result[0])
        data["available_sequences"].append(tools_result[1])
        data["dialog_state"] = "available_time"

    return await zena_default(request=request, result=result, expected_type=list, on_ok=on_ok)


async def pp_record_time(result: ToolMessage, request: ToolCallRequest) -> Any:
    def on_ok(data: dict, tools_result: dict, request: ToolCallRequest) -> None:
        if tools_result.get("success"):
            data["dialog_state"] = "postrecord"

    return await zena_default(request=request, result=result, expected_type=dict, on_ok=on_ok)


async def pp_recommendations(result: ToolMessage, request: ToolCallRequest) -> Any:
    def on_ok(data: dict, tools_result: Any, request: ToolCallRequest) -> None:
        data.update(
            {
                "dialog_state": "new",
                "items_search": [],
                "item_selected": [],
                "available_time": [],
                "available_sequences": [],
            }
        )

    return await zena_default(
        request=request,
        result=result,
        expected_type=(list, dict, str),
        on_ok=on_ok,
        require_truthy=False,
    )


async def pp_remember_office(result: ToolMessage, request: ToolCallRequest) -> Any:
    def on_ok(data: dict, tools_result: dict, request: ToolCallRequest) -> None:
        if tools_result.get("success") and tools_result.get("office_id"):
            data["office_id"] = str(tools_result["office_id"])
    return await zena_default(request=request, result=result, expected_type=dict, on_ok=on_ok)


async def pp_remember_desired_date(result: ToolMessage, request: ToolCallRequest) -> Any:
    def on_ok(data: dict, tools_result: dict, request: ToolCallRequest) -> None:
        if tools_result.get("success") and tools_result.get("desired_date"):
            data["desired_date"] = str(tools_result["desired_date"])

    return await zena_default(request=request, result=result, expected_type=dict, on_ok=on_ok)


async def pp_remember_desired_time(result: ToolMessage, request: ToolCallRequest) -> Any:
    def on_ok(data: dict, tools_result: dict, request: ToolCallRequest) -> None:
        if tools_result.get("success") and tools_result.get("desired_time"):
            data["desired_time"] = str(tools_result["desired_time"])

    return await zena_default(request=request, result=result, expected_type=dict, on_ok=on_ok)



async def pp_product_remember(result: ToolMessage, request: ToolCallRequest) -> Any:
    """Возвращает нормализованные items (item_selected)."""
    items_out: list[dict] = []

    def on_ok(data: dict, tools_result: list, request: ToolCallRequest) -> None:
 
        if not tools_result.get("success"):
            return tools_result.get("message")
        
        nonlocal items_out
        items_out = [parse_item(x) for x in tools_result.get("products") if isinstance(x, dict)]
        
        if not items_out:
            return
        
        data["item_selected"] = items_out
        data["dialog_state"] = "remember"
        logger.info(f"item_selected: {items_out}")
    
    logger.info("pp_product_remember")
    await zena_default(request=request, result=result, expected_type=dict, on_ok=on_ok)
    return items_out or None


async def pp_product_search(result: ToolMessage, request: ToolCallRequest) -> Any:
    """Возвращает нормализованные items, которые были добавлены в items_search."""
    added_items: list[dict] = []

    def on_ok(data: dict, tools_result: list, request: ToolCallRequest) -> None:
        nonlocal added_items
        items_search = data.setdefault("items_search", [])
        existing_ids = {it.get("item_id") for it in items_search if isinstance(it, dict)}

        new_items: list[dict] = []
        for raw_item in tools_result:
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

        logger.info(f"len(items_search): {len(items_search)}")
        if items_search:
            data["dialog_state"] = "selecting"

    await zena_default(request=request, result=result, expected_type=list, on_ok=on_ok)
    return added_items or None


TOOL_POSTPROCESSORS_DEFAULT: dict[str, PostProcessor] = {
    "zena_avaliable_time_for_master": pp_available_time_for_master,
    "zena_record_time": pp_record_time,
    "zena_recommendations": pp_recommendations,
    "zena_product_search": pp_product_search,
    "zena_remember_product_id": pp_product_remember,
    # NEW: remember user inputs
    "zena_remember_office": pp_remember_office,
    "zena_remember_desired_date": pp_remember_desired_date,
    "zena_remember_desired_time": pp_remember_desired_time,
}

TOOL_POSTPROCESSORS_5007: dict[str, PostProcessor] = {
    "zena_available_time_for_master_list": pp_available_time_for_master_list,
    "zena_record_time": pp_record_time,
    "zena_recommendations": pp_recommendations,
    "zena_product_search": pp_product_search,
    "zena_remember_product_id_list": pp_product_remember,
    # NEW: remember user inputs
    "zena_remember_office": pp_remember_office,
    "zena_remember_desired_date": pp_remember_desired_date,
    "zena_remember_desired_time": pp_remember_desired_time,
}


# =======================
# ALENA (5020) PP
# =======================

async def pp_get_client_lessons(result: ToolMessage, request: ToolCallRequest) -> Any:
    def on_ok(data: dict, tools_result: dict, request: ToolCallRequest) -> None:
        if tools_result.get("success"):
            data["dialog_state"] = "selecting"
            data["items_search"] = tools_result

    return await zena_alena(request=request, result=result, expected_type=dict, on_ok=on_ok)


async def pp_remember_lesson_id(result: ToolMessage, request: ToolCallRequest) -> Any:
    """Сохраняет выбранный урок как есть (dict/list) и переводит в remember."""
    selected: Any = None

    def on_ok(data: dict, tools_result: Any, request: ToolCallRequest) -> None:
        nonlocal selected
        selected = tools_result
        data["dialog_state"] = "remember"
        data["item_selected"] = tools_result

    await zena_alena(
        request=request,
        result=result,
        expected_type=(dict, list, str),
        on_ok=on_ok,
        require_truthy=True,
    )
    return selected


async def pp_update_client_lesson(result: ToolMessage, request: ToolCallRequest) -> Any:
    """Нормализует list->dict, при success переводит dialog_state в new."""
    normalized: dict[str, Any] | None = None

    def on_ok(data: dict, tools_result: Any, request: ToolCallRequest) -> None:
        nonlocal normalized
        tr = tools_result
        if isinstance(tr, list):
            tr = tr[0] if tr else {}
        normalized = tr if isinstance(tr, dict) else {}

        if normalized.get("success"):
            data["dialog_state"] = "new"

    await zena_alena(
        request=request,
        result=result,
        expected_type=(list, dict),
        on_ok=on_ok,
        require_truthy=True,
    )
    return normalized


async def pp_update_client_info(result: ToolMessage, request: ToolCallRequest) -> Any:
    """Нормализует list->dict, при success ставит onboarding_status=True."""
    normalized: dict[str, Any] | None = None

    def on_ok(data: dict, tools_result: Any, request: ToolCallRequest) -> None:
        nonlocal normalized
        tr = tools_result
        if isinstance(tr, list):
            tr = tr[0] if tr else {}
        normalized = tr if isinstance(tr, dict) else {}

        if normalized.get("success"):
            data.setdefault("onboarding", {})
            data["onboarding"]["onboarding_status"] = True

    await zena_alena(
        request=request,
        result=result,
        expected_type=(list, dict),
        on_ok=on_ok,
        require_truthy=True,
    )
    return normalized


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
        logger.info(f"Executing tool: {tool_name}")
        logger.info(f"Tool arguments: {tool_args}")

        try:
            result = await handler(request)
            logger.info(f"Tool result: {result}")
            logger.info("Tool completed successfully")

            registry = _get_registry_for_request(request)
            pp = registry.get(tool_name)
            pp_result = await pp(result, request) if pp else None

            # один элемент на один tool-call, удобно дебажить
            request.state.setdefault("tools_result", []).append(
                {"name": tool_name, "args": tool_args, "result": pp_result}
            )

            return result
        except Exception as e:
            logger.exception(f"Tool failed: {e}")
            raise
