"""Функции для работы с API httpservice.ai2b.pro."""

import aiohttp
from typing_extensions import Any

from .zena_common import retry_async


@retry_async()
async def sent_message_to_history(
    user_id: int,
    text: str,
    user_companychat: int,
    reply_to_history_id: int,
    access_token: str,
    tokens: dict[str, Any],
    tools: list[str],
    tools_args: dict[str, Any],
    tools_result: dict[str, Any],
    prompt_system: str,
    template_prompt_system: str,
    dialog_state: str,
    dialog_state_new: str,
) -> dict[str, Any]:
    """Отправка переменных на endpoint для сохранения."""
    url = "https://httpservice.ai2b.pro/v1/telegram/n8n/outgoing"
    payload = {
        "user_id": user_id,
        "text": text,
        "user_companychat": user_companychat,
        "reply_to_history_id": reply_to_history_id,
        "access_token": access_token,
        "tokens": tokens,
        "tools": tools,
        "tools_args": tools_args,
        "tools_result": tools_result,
        "prompt_system": prompt_system,
        "template_prompt_system": template_prompt_system,
        "dialog_state": dialog_state,
        "dialog_state_new": dialog_state_new,
    }
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload) as resp:
            resp.raise_for_status()
            return await resp.json()
