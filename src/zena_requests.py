"""Модуль реализует обращение по API."""

from typing import Any

import aiohttp

from .zena_common import logger, retry_async


@retry_async()
async def fetch_personal_info(user_id: int) -> dict[str, Any]:
    """Получение персональной информации через API."""
    url = f"https://httpservice.ai2b.pro/v1/vk/personal-data/{user_id}"
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                return await resp.json()
            logger.warning(f"Ошибка запроса: {resp.status}")
            raise RuntimeError(f"Ошибка запроса: {resp.status}")


@retry_async()
async def sent_message_to_history(
    user_id: int,
    text: str,
    user_companychat: int,
    reply_to_history_id: int,
    access_token: str,
) -> dict[str, Any]:
    """Отправка данных по API для записи в Postgres."""
    url = "https://httpservice.ai2b.pro/v1/telegram/n8n/outgoing"
    payload = {
        "user_id": user_id,
        "text": text,
        "user_companychat": user_companychat,
        "reply_to_history_id": reply_to_history_id,
        "access_token": access_token,
    }
    # headers = {
    #     "Authorization": f"Bearer {access_token}",
    #     "Accept": "application/json",
    # }
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # async with session.post(url, json=payload, headers=headers) as resp:
            async with session.post(url, json=payload) as resp:
                resp.raise_for_status()
                # Если сервер возвращает JSON
                return await resp.json()
    except aiohttp.ClientResponseError as e:
        logger.warning(f"HTTP error: {e.status} {e.message}")
        raise
    except (aiohttp.ConnectionTimeoutError, aiohttp.ServerTimeoutError):
        logger.warning("Request timed out")
        raise
    except aiohttp.ClientError as e:
        logger.warning(f"Client error: {e}")
        raise
