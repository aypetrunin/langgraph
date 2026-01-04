"""Модуль реализует обращение по API."""

import aiohttp
import asyncio

from typing import Any

from .zena_common import logger, retry_async


@retry_async()
async def fetch_personal_info(user_id: int) -> dict[str, Any]:
    """Получение персональной информации через API."""
    logger.info("===zena_requests.fetch_personal_info===")

    url = f"https://httpservice.ai2b.pro/v1/vk/personal-data/{user_id}"

    logger.info(f"Отправка запроса: {url}")

    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                return await resp.json()
            logger.warning(f"Ошибка запроса: {resp.status}")
            raise RuntimeError(f"Ошибка запроса: {resp.status}")


@retry_async()
async def fetch_crm_go_client_info(
    phone: str,
    channel_id: str = '20'
) -> dict[str, Any]:
    """Получение персональной информации через API."""

    logger.info("===zena_requests.fetch_crm_go_client_info===")

    payload = {
        "channel_id": channel_id,
        "phone": phone
    }

    url = f"https://httpservice.ai2b.pro/appointments/go_crm/client_card_by_phone"

    logger.info(f"Отправка запроса: {url}, payload: {payload}")

    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload) as resp:
             if resp.status == 200:
                responce = await resp.json()
                logger.info(f"responce: {responce}")
                return responce
        
        if resp.status == 404:
            return {'success': False, 'message': "Клиента нет базе данных"}
        else:
            logger.warning(f"Ошибка запроса: {resp.status}")
            raise RuntimeError(f"Ошибка запроса: {resp.status}")
    
    return {'success': False, 'message': "Клиента нет базе данных"}


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


def get_stage_onboarding(payload: dict) -> int:
    """Определяет статус опроса по заполненности полей."""

    if not payload.get("parent_name"):  # Заполнен parent_name
        return 0
    
    if not payload.get("child_name"):  # Заполнен child_name
        return 1
    
    if not payload.get("child_date_of_birth"):  # Заполнена дата рождения
        return 2
    
    if not payload["contact_reason"]:  # Заполнена причина
        return 3
    
    return 4  # Fallback


async def main():
    phone = "799967382561"
    response = await fetch_crm_go_client_info(
        phone=phone,
    )
    print(response)


if __name__ == "__main__":
    asyncio.run(main())



# cd /home/copilot_superuser/petrunin/zena/langgraph
# uv run python -m src.zena_requests